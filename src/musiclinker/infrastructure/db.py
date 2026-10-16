"""Thread-safe SQLite WAL repository with track_number indexing, multi-mode sorting, album aliases, and LLM settings."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import generate_pair_token
from musiclinker.domain.models import TrackEntity
from musiclinker.infrastructure.hasher import clean_album_name, normalize_text

DEFAULT_SETTINGS: dict[str, Any] = {
    "lastfm_api_key": os.environ.get("LASTFM_API_KEY", ""),
    "lastfm_api_secret": os.environ.get("LASTFM_API_SECRET", ""),
    "lastfm_username": os.environ.get("LASTFM_USERNAME", ""),
    "lastfm_session_key": os.environ.get("LASTFM_SESSION_KEY", ""),
    "default_bitrate_kbps": 320,
    "default_min_plays": 10,
    "default_download_cap": 25,
    "require_lan_token": True,
    "normalize_audio": True,
    "auto_clean_albums": True,
    "llm_base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    "llm_api_key": os.environ.get("LLM_API_KEY", ""),
    "llm_model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    "proxy_name": os.environ.get("PROXY_NAME", "default"),
    "proxy_server_url": os.environ.get("PROXY_SERVER_URL", ""),
    "proxy_password": os.environ.get("PROXY_PASSWORD", ""),
    "lan_pair_token": generate_pair_token(),
    "preferred_download_method": "spotify_match",
}


class DatabaseRepository:
    """Thread-local SQLite WAL repository managing tracks, album aliases, seen hashes, scrobbles, transitions, and settings."""

    def __init__(self, db_path: Path = PATHS.db_path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._initialize_schema()
        self._migrate_legacy_files()

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return conn

    def _initialize_schema(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_hash TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    artist_display TEXT NOT NULL,
                    artists_json TEXT NOT NULL DEFAULT '[]',
                    album_artist TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT '',
                    track_number INTEGER NOT NULL DEFAULT 0,
                    release_year TEXT NOT NULL DEFAULT '',
                    original_year TEXT NOT NULL DEFAULT '',
                    duration INTEGER NOT NULL DEFAULT 0,
                    file_path TEXT NOT NULL UNIQUE,
                    lrc_path TEXT NOT NULL DEFAULT '',
                    lyric_sync_mode TEXT NOT NULL DEFAULT 'none',
                    cover_mime TEXT NOT NULL DEFAULT '',
                    has_cover INTEGER NOT NULL DEFAULT 0,
                    lastfm_playcount INTEGER NOT NULL DEFAULT 0,
                    local_playcount INTEGER NOT NULL DEFAULT 0,
                    replaygain_db REAL NOT NULL DEFAULT 0.0,
                    bitrate_kbps INTEGER NOT NULL DEFAULT 320,
                    added_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);
                CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);
                CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);

                CREATE TABLE IF NOT EXISTS album_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_norm TEXT NOT NULL DEFAULT '',
                    raw_album_norm TEXT NOT NULL,
                    raw_album_display TEXT NOT NULL,
                    canonical_album TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(artist_norm, raw_album_norm)
                );

                CREATE TABLE IF NOT EXISTS seen_hashes (
                    track_hash TEXT PRIMARY KEY,
                    artist TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'download',
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scrobble_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    album TEXT NOT NULL DEFAULT '',
                    duration INTEGER NOT NULL DEFAULT 0,
                    timestamp INTEGER NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(artist, title, timestamp)
                );

                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_track_id INTEGER NOT NULL,
                    to_track_id INTEGER NOT NULL,
                    probability REAL NOT NULL DEFAULT 1.0,
                    crossfade_seconds REAL NOT NULL DEFAULT 3.0,
                    UNIQUE(from_track_id, to_track_id),
                    FOREIGN KEY(from_track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY(to_track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    position INTEGER NOT NULL DEFAULT 1,
                    added_at INTEGER NOT NULL,
                    UNIQUE(playlist_id, track_id),
                    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );
                """
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
            if "lyric_sync_mode" not in cols:
                conn.execute("ALTER TABLE tracks ADD COLUMN lyric_sync_mode TEXT NOT NULL DEFAULT 'none'")
            if "track_number" not in cols:
                conn.execute("ALTER TABLE tracks ADD COLUMN track_number INTEGER NOT NULL DEFAULT 0")


            for key, default_val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
                    (key, json.dumps(default_val)),
                )
            conn.commit()

    def _migrate_legacy_files(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            if PATHS.legacy_config_path.exists():
                try:
                    raw_cfg = json.loads(PATHS.legacy_config_path.read_text(encoding="utf-8"))
                    if isinstance(raw_cfg, dict):
                        for k, v in raw_cfg.items():
                            if v not in (None, ""):
                                conn.execute(
                                    "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                                    (k, json.dumps(v)),
                                )
                        conn.commit()
                    backup = PATHS.legacy_config_path.with_suffix(".json.bak")
                    shutil.move(str(PATHS.legacy_config_path), str(backup))
                except Exception as exc:
                    logger.warning("Failed to migrate legacy config.json: %s", exc)

            if PATHS.legacy_hashes_path.exists():
                try:
                    raw_hashes = json.loads(PATHS.legacy_hashes_path.read_text(encoding="utf-8"))
                    now_ts = int(time.time())
                    if isinstance(raw_hashes, list):
                        for h in raw_hashes:
                            conn.execute(
                                "INSERT OR IGNORE INTO seen_hashes (track_hash, source, created_at) VALUES (?, 'legacy', ?)",
                                (str(h), now_ts),
                            )
                    elif isinstance(raw_hashes, dict):
                        for h, meta in raw_hashes.items():
                            artist = meta.get("artist", "") if isinstance(meta, dict) else ""
                            title = meta.get("title", "") if isinstance(meta, dict) else str(meta)
                            conn.execute(
                                "INSERT OR IGNORE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, 'legacy', ?)",
                                (str(h), artist, title, now_ts),
                            )
                    conn.commit()
                    backup = PATHS.legacy_hashes_path.with_suffix(".json.bak")
                    shutil.move(str(PATHS.legacy_hashes_path), str(backup))
                except Exception as exc:
                    logger.warning("Failed to migrate legacy seen_hashes.json: %s", exc)

    # ---- Settings Operations ----

    def get_settings(self) -> dict[str, Any]:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value_json"])
            except Exception:
                result[row["key"]] = row["value_json"]
        return result

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            for key, value in updates.items():
                if value is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )
            conn.commit()
        return self.get_settings()

    # ---- Album Alias, Artist Summary & Hierarchy Operations ----

    def resolve_album_alias(self, artist: str, raw_album: str, track_title: str = "") -> str:
        auto_clean = bool(self.get_settings().get("auto_clean_albums", True))
        candidate = clean_album_name(raw_album, track_title) if auto_clean else (raw_album.strip() or "Singles")

        artist_norm = normalize_text(artist)
        raw_norm = normalize_text(raw_album)
        cand_norm = normalize_text(candidate)

        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT canonical_album FROM album_aliases
            WHERE (artist_norm = ? OR artist_norm = '')
              AND (raw_album_norm = ? OR raw_album_norm = ?)
            ORDER BY CASE WHEN artist_norm != '' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (artist_norm, raw_norm, cand_norm),
        ).fetchone()
        return str(row["canonical_album"]) if row else candidate

    def save_album_alias(self, artist: str, raw_album: str, canonical_album: str) -> None:
        artist_norm = normalize_text(artist)
        raw_norm = normalize_text(raw_album)
        if not raw_norm or not canonical_album.strip():
            return
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO album_aliases (artist_norm, raw_album_norm, raw_album_display, canonical_album, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artist_norm, raw_album_norm) DO UPDATE SET
                    canonical_album = excluded.canonical_album,
                    updated_at = excluded.updated_at
                """,
                (artist_norm, raw_norm, raw_album.strip(), canonical_album.strip(), int(time.time())),
            )
            conn.commit()

    def list_artists_summary(self) -> list[dict[str, Any]]:
        """Return distinct album artists with album and track counts for Layer 1 of the Layered Selector."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT album_artist AS artist,
                   COUNT(DISTINCT album) AS album_count,
                   COUNT(*) AS track_count
            FROM tracks
            GROUP BY album_artist
            ORDER BY LOWER(album_artist) ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def list_albums_summary(self) -> list[dict[str, Any]]:
        """Return distinct albums in the library with track counts and primary artist."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT album, album_artist, COUNT(*) AS track_count,
                   MAX(release_year) AS release_year,
                   MIN(id) AS sample_track_id,
                   MAX(has_cover) AS has_cover
            FROM tracks
            GROUP BY album, album_artist
            ORDER BY LOWER(album) ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def list_album_aliases(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM album_aliases ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_album_alias(self, alias_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM album_aliases WHERE id = ?", (alias_id,))
            conn.commit()

    # ---- Deduplication Hash Operations ----

    def is_hash_seen(self, track_hash: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM seen_hashes WHERE track_hash = ? UNION SELECT 1 FROM tracks WHERE track_hash = ? LIMIT 1",
            (track_hash, track_hash),
        ).fetchone()
        return row is not None

    def mark_hash_seen(self, track_hash: str, artist: str = "", title: str = "", source: str = "download") -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (track_hash, artist, title, source, int(time.time())),
            )
            conn.commit()

    def unmark_hash(self, track_hash: str) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM seen_hashes WHERE track_hash = ?", (track_hash,))
            conn.commit()

    # ---- Track Library Operations ----

    def upsert_track(
        self,
        *,
        track_hash: str,
        title: str,
        artist_display: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
        duration: int = 0,
        file_path: str,
        lrc_path: str = "",
        lyric_sync_mode: str = "none",
        cover_mime: str = "",
        has_cover: bool = False,
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        bitrate_kbps: int = 320,
    ) -> TrackEntity:
        now_ts = int(time.time())
        canonical_album = self.resolve_album_alias(album_artist, album, title)
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO tracks (
                    track_hash, title, artist_display, artists_json, album_artist,
                    album, track_number, release_year, original_year, duration, file_path,
                    lrc_path, lyric_sync_mode, cover_mime, has_cover, lastfm_playcount,
                    replaygain_db, bitrate_kbps, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_hash) DO UPDATE SET
                    title = excluded.title,
                    artist_display = excluded.artist_display,
                    artists_json = excluded.artists_json,
                    album_artist = excluded.album_artist,
                    album = excluded.album,
                    track_number = CASE WHEN excluded.track_number > 0 THEN excluded.track_number ELSE tracks.track_number END,
                    release_year = CASE WHEN excluded.release_year != '' THEN excluded.release_year ELSE tracks.release_year END,
                    original_year = CASE WHEN excluded.original_year != '' THEN excluded.original_year ELSE tracks.original_year END,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END,
                    file_path = excluded.file_path,
                    lrc_path = CASE WHEN excluded.lrc_path != '' THEN excluded.lrc_path ELSE tracks.lrc_path END,
                    lyric_sync_mode = CASE WHEN excluded.lyric_sync_mode != 'none' THEN excluded.lyric_sync_mode ELSE tracks.lyric_sync_mode END,
                    cover_mime = CASE WHEN excluded.cover_mime != '' THEN excluded.cover_mime ELSE tracks.cover_mime END,
                    has_cover = CASE WHEN excluded.has_cover = 1 THEN 1 ELSE tracks.has_cover END,
                    lastfm_playcount = MAX(tracks.lastfm_playcount, excluded.lastfm_playcount),
                    replaygain_db = excluded.replaygain_db,
                    bitrate_kbps = excluded.bitrate_kbps
                """,
                (
                    track_hash,
                    title,
                    artist_display,
                    json.dumps(artists, ensure_ascii=False),
                    album_artist,
                    canonical_album,
                    int(track_number or 0),
                    release_year,
                    original_year,
                    duration,
                    file_path,
                    lrc_path,
                    lyric_sync_mode,
                    cover_mime,
                    1 if has_cover else 0,
                    lastfm_playcount,
                    replaygain_db,
                    bitrate_kbps,
                    now_ts,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, 'library', ?)",
                (track_hash, artist_display, title, now_ts),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tracks WHERE track_hash = ?", (track_hash,)).fetchone()
            return self._row_to_track(row)

    def update_track_row(
        self,
        track_id: int,
        *,
        track_hash: str,
        title: str,
        artist_display: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int,
        release_year: str,
        original_year: str,
        file_path: str,
        lrc_path: str,
    ) -> TrackEntity | None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                UPDATE tracks
                SET track_hash = ?, title = ?, artist_display = ?, artists_json = ?,
                    album_artist = ?, album = ?, track_number = ?, release_year = ?,
                    original_year = ?, file_path = ?, lrc_path = ?
                WHERE id = ?
                """,
                (
                    track_hash,
                    title,
                    artist_display,
                    json.dumps(artists, ensure_ascii=False),
                    album_artist,
                    album,
                    int(track_number or 0),
                    release_year,
                    original_year,
                    file_path,
                    lrc_path,
                    track_id,
                ),
            )
            conn.commit()
            return self.get_track(track_id)

    def delete_track(self, track_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            conn.commit()

    def list_tracks(
        self,
        search: str = "",
        artist: str = "",
        album: str = "",
        sort_by: str = "track_num",
    ) -> list[TrackEntity]:
        """
        List tracks with flexible ordering:
          - `track_num`: Album + Track # (`1..N`) + Title (ideal for Play by Album)
          - `alpha_asc`: Alphabetical by Title (`A -> Z`)
          - `alpha_desc`: Alphabetical by Title (`Z -> A`)
          - `plays_desc`: Most played (`lastfm_playcount + local_playcount DESC`)
          - `recent`: Recently added (`added_at DESC`)
        """
        conn = self._get_conn()
        clauses: list[str] = []
        params: list[Any] = []
        if search.strip():
            q = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(title) LIKE ? OR LOWER(artist_display) LIKE ? OR LOWER(album) LIKE ?)")
            params.extend([q, q, q])
        if artist.strip():
            a = f"%{artist.strip().lower()}%"
            clauses.append("(LOWER(artist_display) LIKE ? OR LOWER(album_artist) LIKE ?)")
            params.extend([a, a])
        if album.strip():
            clauses.append("LOWER(album) = ?")
            params.append(album.strip().lower())

        order_map = {
            "track_num": "LOWER(album) ASC, CASE WHEN track_number > 0 THEN track_number ELSE 9999 END ASC, LOWER(title) ASC",
            "alpha_asc": "LOWER(title) ASC, LOWER(artist_display) ASC",
            "alpha_desc": "LOWER(title) DESC, LOWER(artist_display) ASC",
            "plays_desc": "(lastfm_playcount + local_playcount) DESC, LOWER(title) ASC",
            "recent": "added_at DESC, LOWER(title) ASC",
        }
        order_sql = order_map.get(sort_by, order_map["track_num"])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM tracks {where_sql} ORDER BY {order_sql}",
            params,
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def get_track(self, track_id: int) -> TrackEntity | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def get_track_by_hash(self, track_hash: str) -> TrackEntity | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tracks WHERE track_hash = ?", (track_hash,)).fetchone()
        return self._row_to_track(row) if row else None

    def increment_local_playcount(self, track_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("UPDATE tracks SET local_playcount = local_playcount + 1 WHERE id = ?", (track_id,))
            conn.commit()

    def remove_missing_files(self) -> int:
        removed = 0
        with self._write_lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT id, file_path FROM tracks").fetchall()
            for r in rows:
                if not Path(r["file_path"]).exists():
                    conn.execute("DELETE FROM tracks WHERE id = ?", (r["id"],))
                    removed += 1
            if removed:
                conn.commit()
        return removed

    # ---- Scrobble Queue Operations ----

    def enqueue_scrobble(self, artist: str, title: str, album: str, duration: int, timestamp: int, error: str = "") -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR IGNORE INTO scrobble_queue (artist, title, album, duration, timestamp, synced, last_error)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (artist, title, album, duration, timestamp, error),
            )
            conn.commit()

    def get_pending_scrobbles(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM scrobble_queue WHERE synced = 0 ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_scrobbles_synced(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(f"UPDATE scrobble_queue SET synced = 1 WHERE id IN ({placeholders})", ids)
            conn.commit()

    # ---- Custom Song Transitions ----

    def upsert_transition(self, from_track_id: int, to_track_id: int, probability: float, crossfade_seconds: float) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO transitions (from_track_id, to_track_id, probability, crossfade_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(from_track_id, to_track_id) DO UPDATE SET
                    probability = excluded.probability,
                    crossfade_seconds = excluded.crossfade_seconds
                """,
                (from_track_id, to_track_id, probability, crossfade_seconds),
            )
            conn.commit()
        return {
            "from_track_id": from_track_id,
            "to_track_id": to_track_id,
            "probability": probability,
            "crossfade_seconds": crossfade_seconds,
        }

    def get_transitions_for_track(self, from_track_id: int) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT tr.id, tr.from_track_id, tr.to_track_id, tr.probability, tr.crossfade_seconds,
                   t.title AS to_title, t.artist_display AS to_artist
            FROM transitions tr
            JOIN tracks t ON t.id = tr.to_track_id
            WHERE tr.from_track_id = ?
            """,
            (from_track_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_transitions(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT tr.id, tr.from_track_id, tr.to_track_id, tr.probability, tr.crossfade_seconds,
                   t1.title AS from_title, t1.artist_display AS from_artist,
                   t2.title AS to_title, t2.artist_display AS to_artist
            FROM transitions tr
            JOIN tracks t1 ON t1.id = tr.from_track_id
            JOIN tracks t2 ON t2.id = tr.to_track_id
            ORDER BY tr.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_transition(self, transition_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM transitions WHERE id = ?", (transition_id,))
            conn.commit()

    # ---- Playlist CRUD & Track Management ----

    def list_playlists(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COUNT(pt.id) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            GROUP BY p.id
            ORDER BY p.name COLLATE NOCASE ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_playlist(self, playlist_id: int) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, name, description, created_at, updated_at FROM playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        track_rows = conn.execute(
            """
            SELECT t.*, pt.position, pt.added_at AS playlist_added_at
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC, pt.id ASC
            """,
            (playlist_id,),
        ).fetchall()
        data["tracks"] = [self._row_to_track(r).to_dict() for r in track_rows]
        data["track_count"] = len(data["tracks"])
        return data

    def create_playlist(self, name: str, description: str = "", track_ids: list[int] | None = None) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Playlist name cannot be empty.")
        now = int(time.time())
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO playlists (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (clean_name, description.strip(), now, now),
            )
            playlist_id = int(cursor.lastrowid)
            if track_ids:
                for pos, tid in enumerate(track_ids, start=1):
                    conn.execute(
                        "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                        (playlist_id, tid, pos, now),
                    )
            conn.commit()
            return self.get_playlist(playlist_id) or {"id": playlist_id, "name": clean_name}

    def update_playlist(self, playlist_id: int, name: str | None = None, description: str | None = None, track_ids: list[int] | None = None) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            now = int(time.time())
            if name is not None and name.strip():
                conn.execute(
                    "UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
                    (name.strip(), now, playlist_id),
                )
            if description is not None:
                conn.execute(
                    "UPDATE playlists SET description = ?, updated_at = ? WHERE id = ?",
                    (description.strip(), now, playlist_id),
                )
            if track_ids is not None:
                conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
                for pos, tid in enumerate(track_ids, start=1):
                    conn.execute(
                        "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                        (playlist_id, tid, pos, now),
                    )
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
            conn.commit()
            updated = self.get_playlist(playlist_id)
            if not updated:
                raise ValueError(f"Playlist ID {playlist_id} not found.")
            return updated

    def add_tracks_to_playlist(self, playlist_id: int, track_ids: list[int]) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            now = int(time.time())
            max_pos_row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS max_pos FROM playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            current_pos = int(max_pos_row["max_pos"]) if max_pos_row else 0
            for tid in track_ids:
                current_pos += 1
                conn.execute(
                    "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                    (playlist_id, tid, current_pos, now),
                )
            conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
            conn.commit()
            return self.get_playlist(playlist_id) or {"id": playlist_id}

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            )
            conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (int(time.time()), playlist_id))
            conn.commit()

    def delete_playlist(self, playlist_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            conn.commit()

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> TrackEntity:
        try:
            artists = json.loads(row["artists_json"])
        except Exception:
            artists = [row["artist_display"]]
        keys = row.keys()
        return TrackEntity(
            id=int(row["id"]),
            track_hash=str(row["track_hash"]),
            title=str(row["title"]),
            artist_display=str(row["artist_display"]),
            artists=artists if isinstance(artists, list) else [str(row["artist_display"])],
            album_artist=str(row["album_artist"]),
            album=str(row["album"]),
            track_number=int(row["track_number"]) if "track_number" in keys else 0,
            release_year=str(row["release_year"]),
            original_year=str(row["original_year"]),
            duration=int(row["duration"]),
            file_path=str(row["file_path"]),
            lrc_path=str(row["lrc_path"]),
            lyric_sync_mode=str(row["lyric_sync_mode"]) if "lyric_sync_mode" in keys else "none",
            cover_mime=str(row["cover_mime"]),
            has_cover=bool(row["has_cover"]),
            lastfm_playcount=int(row["lastfm_playcount"]),
            local_playcount=int(row["local_playcount"]),
            replaygain_db=float(row["replaygain_db"]),
            bitrate_kbps=int(row["bitrate_kbps"]),
            added_at=int(row["added_at"]),
        )


db_repo = DatabaseRepository()