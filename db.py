import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_connection()
    cursor = conn.cursor()

    # Tracks table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        primary_artist TEXT NOT NULL,
        all_artists TEXT NOT NULL,
        album TEXT DEFAULT 'Singles',
        year INTEGER DEFAULT NULL,
        original_year INTEGER DEFAULT NULL,
        genre TEXT DEFAULT '',
        duration INTEGER DEFAULT 0,
        plays INTEGER DEFAULT 0,
        filepath TEXT NOT NULL,
        youtube_url TEXT DEFAULT '',
        cover_url TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """)

    # Offline Scrobble Queue table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scrobble_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        artist TEXT NOT NULL,
        track TEXT NOT NULL,
        album TEXT DEFAULT '',
        played_at INTEGER NOT NULL,
        synced INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        error_msg TEXT DEFAULT ''
    );
    """)

    # Custom Transitions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_track_hash TEXT NOT NULL,
        to_track_hash TEXT NOT NULL,
        from_title TEXT DEFAULT '',
        to_title TEXT DEFAULT '',
        probability INTEGER DEFAULT 50, -- 1 to 100 (%)
        crossfade_duration REAL DEFAULT 4.0,
        custom_audio_path TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    );
    """)

    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()

# Track Operations
def add_or_update_track(track_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().isoformat()
    all_artists_json = json.dumps(track_data.get("all_artists", [track_data.get("primary_artist", "")]))

    cursor.execute("""
    INSERT INTO tracks (hash, title, primary_artist, all_artists, album, year, original_year, genre, duration, plays, filepath, youtube_url, cover_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(hash) DO UPDATE SET
        title = excluded.title,
        primary_artist = excluded.primary_artist,
        all_artists = excluded.all_artists,
        album = excluded.album,
        year = excluded.year,
        original_year = excluded.original_year,
        genre = excluded.genre,
        duration = excluded.duration,
        plays = excluded.plays,
        filepath = excluded.filepath,
        youtube_url = excluded.youtube_url,
        cover_url = excluded.cover_url
    """, (
        track_data["hash"],
        track_data["title"],
        track_data["primary_artist"],
        all_artists_json,
        track_data.get("album", "Singles"),
        track_data.get("year"),
        track_data.get("original_year"),
        track_data.get("genre", ""),
        track_data.get("duration", 0),
        track_data.get("plays", 0),
        track_data["filepath"],
        track_data.get("youtube_url", ""),
        track_data.get("cover_url", ""),
        now_str
    ))

    track_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return track_id

def get_all_tracks() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tracks ORDER BY primary_artist, album, title")
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        try:
            d["all_artists"] = json.loads(d["all_artists"])
        except Exception:
            d["all_artists"] = [d["primary_artist"]]
        results.append(d)
    return results

def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["all_artists"] = json.loads(d["all_artists"])
    except Exception:
        d["all_artists"] = [d["primary_artist"]]
    return d

def get_track_by_hash(track_hash: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tracks WHERE hash = ?", (track_hash,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_artists_summary() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT primary_artist, COUNT(id) as track_count, SUM(plays) as total_plays
    FROM tracks
    GROUP BY primary_artist
    ORDER BY primary_artist COLLATE NOCASE ASC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_albums_summary() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT album, primary_artist, COUNT(id) as track_count, year, cover_url
    FROM tracks
    GROUP BY album, primary_artist
    ORDER BY primary_artist COLLATE NOCASE ASC, album COLLATE NOCASE ASC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# Scrobble Queue Operations
def queue_scrobble(artist: str, track: str, album: str = "", timestamp: Optional[int] = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    ts = timestamp or int(datetime.now().timestamp())
    cursor.execute("""
    INSERT INTO scrobble_queue (artist, track, album, played_at, synced)
    VALUES (?, ?, ?, ?, 0)
    """, (artist, track, album, ts))
    qid = cursor.lastrowid
    conn.commit()
    conn.close()
    return qid

def get_pending_scrobbles() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scrobble_queue WHERE synced = 0 ORDER BY played_at ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def mark_scrobble_synced(queue_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE scrobble_queue SET synced = 1 WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()

def mark_scrobble_failed(queue_id: int, error_msg: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE scrobble_queue SET attempts = attempts + 1, error_msg = ? WHERE id = ?", (error_msg, queue_id))
    conn.commit()
    conn.close()

# Transition Operations
def add_transition(from_hash: str, to_hash: str, from_title: str, to_title: str, probability: int = 50, duration: float = 4.0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO transitions (from_track_hash, to_track_hash, from_title, to_title, probability, crossfade_duration, enabled)
    VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (from_hash, to_hash, from_title, to_title, probability, duration))
    tid = cursor.lastrowid
    conn.commit()
    conn.close()
    return tid

def get_all_transitions() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transitions ORDER BY id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def delete_transition(trans_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transitions WHERE id = ?", (trans_id,))
    conn.commit()
    conn.close()

# Settings Operations
def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()

init_db()
