"""Filesystem-to-database library synchronizer, cover/lyrics loader, and transition resolver."""

from __future__ import annotations

from pathlib import Path
import random
import re
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import TrackEntity
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.tagger import audio_tagger

_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or fallback


class LibraryService:
    """Manages local music library indexing, metadata reading, and probabilistic song transitions."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def scan_and_sync_downloads(self) -> dict[str, int]:
        """Scan `downloads/` for MP3 files, index new/updated tracks into `library.db`, and prune missing rows."""
        pruned = self.repo.remove_missing_files()
        indexed = 0

        for mp3_path in PATHS.downloads_dir.rglob("*.mp3"):
            # Skip temporary staging files
            if ".staging" in mp3_path.parts:
                continue
            try:
                meta = audio_tagger.read_mp3_metadata(mp3_path)
                lrc_candidate = mp3_path.with_suffix(".lrc")
                lrc_path = str(lrc_candidate) if lrc_candidate.exists() else ""
                self.repo.upsert_track(
                    track_hash=meta["track_hash"],
                    title=meta["title"],
                    artist_display=meta["artist_display"],
                    artists=meta["artists"],
                    album_artist=meta["album_artist"],
                    album=meta["album"],
                    release_year=meta["release_year"],
                    original_year=meta["original_year"],
                    duration=meta["duration"],
                    file_path=str(mp3_path.resolve()),
                    lrc_path=lrc_path,
                    cover_mime=meta["cover_mime"],
                    has_cover=meta["has_cover"],
                    replaygain_db=meta["replaygain_db"],
                    bitrate_kbps=meta["bitrate_kbps"],
                )
                indexed += 1
            except Exception as exc:
                logger.warning("Skipping unreadable MP3 %s: %s", mp3_path, exc)

        logger.info("Library sync complete: %d track(s) indexed, %d missing entry(s) pruned", indexed, pruned)
        return {"indexed": indexed, "pruned": pruned}

    def get_track_lyrics(self, track: TrackEntity) -> dict[str, Any]:
        """Return parsed synced or plain lyrics for a track, fetching from LRCLIB on demand if missing."""
        if track.lrc_path and Path(track.lrc_path).exists():
            raw = Path(track.lrc_path).read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw)

        mp3_file = Path(track.file_path)
        lrc_file = mp3_file.with_suffix(".lrc")
        if lrc_file.exists():
            raw = lrc_file.read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw)

        fetched = lyrics_client.fetch_and_save_lrc(
            artist=track.album_artist or track.artist_display,
            title=track.title,
            album=track.album,
            duration=track.duration,
            target_lrc_path=lrc_file,
        )
        if fetched:
            self.repo.upsert_track(
                track_hash=track.track_hash,
                title=track.title,
                artist_display=track.artist_display,
                artists=track.artists,
                album_artist=track.album_artist,
                album=track.album,
                release_year=track.release_year,
                original_year=track.original_year,
                duration=track.duration,
                file_path=track.file_path,
                lrc_path=str(lrc_file),
                cover_mime=track.cover_mime,
                has_cover=track.has_cover,
                lastfm_playcount=track.lastfm_playcount,
                replaygain_db=track.replaygain_db,
                bitrate_kbps=track.bitrate_kbps,
            )
            return lyrics_client.parse_lrc_content(fetched)
        return {"synced": False, "lines": []}

    def resolve_next_transition(self, current_track_id: int) -> dict[str, Any] | None:
        """Evaluate probabilistic transition rules for `current_track_id`."""
        rules = self.repo.get_transitions_for_track(current_track_id)
        if not rules:
            return None
        for rule in rules:
            prob = float(rule.get("probability", 1.0))
            if random.random() <= prob:
                next_track = self.repo.get_track(int(rule["to_track_id"]))
                if next_track:
                    return {
                        "next_track": next_track.to_dict(),
                        "crossfade_seconds": float(rule.get("crossfade_seconds", 3.0)),
                    }
        return None


library_service = LibraryService()