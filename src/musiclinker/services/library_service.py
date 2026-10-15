"""Library synchronizer, external album tracklist resolver, internal album/track renamer, and 3-tier lyrics loader."""

from __future__ import annotations

import os
from pathlib import Path
import random
import re
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import AlbumBulkRenamePayload, TrackEntity, TrackMetadataUpdatePayload
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.musicbrainz_client import musicbrainz_client
from musiclinker.infrastructure.spotify_client import spotify_client
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client


_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or fallback


class LibraryService:
    """Manages local music library indexing, external album lookups, internal renaming, and song transitions."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def scan_and_sync_downloads(self) -> dict[str, int]:
        pruned = self.repo.remove_missing_files()
        indexed = 0

        for mp3_path in PATHS.downloads_dir.rglob("*.mp3"):
            if ".staging" in mp3_path.parts:
                continue
            try:
                meta = audio_tagger.read_mp3_metadata(mp3_path)
                canonical_album = self.repo.resolve_album_alias(
                    meta["album_artist"], meta["album"], meta["title"]
                )
                lrc_candidate = mp3_path.with_suffix(".lrc")
                lrc_path = str(lrc_candidate.resolve()) if lrc_candidate.exists() else ""
                sync_mode = "none"
                if lrc_candidate.exists():
                    parsed_lrc = lyrics_client.parse_lrc_content(
                        lrc_candidate.read_text(encoding="utf-8", errors="replace"),
                        fallback_duration=meta["duration"],
                    )
                    sync_mode = str(parsed_lrc.get("sync_mode", "exact_lrc"))

                self.repo.upsert_track(
                    track_hash=meta["track_hash"],
                    title=meta["title"],
                    artist_display=meta["artist_display"],
                    artists=meta["artists"],
                    album_artist=meta["album_artist"],
                    album=canonical_album,
                    track_number=int(meta.get("track_number") or 0),
                    release_year=meta["release_year"],
                    original_year=meta["original_year"],
                    duration=meta["duration"],
                    file_path=str(mp3_path.resolve()),
                    lrc_path=lrc_path,
                    lyric_sync_mode=sync_mode,
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

    def lookup_external_album_tracklist(self, artist: str = "", album_or_url: str = "") -> dict[str, Any]:
        """
        Resolve the full tracklist (`1..N`) for an album so the user can preview and download the entire album:
          1. If `album_or_url` or `artist` is a Spotify Album/Playlist/Track URL or URI, scrape Spotify embed metadata.
          2. If `album_or_url` is a YouTube playlist/album URL, expand via `yt-dlp`.
          3. Otherwise query MusicBrainz for the official release tracklist.
          4. Fallback to Last.fm `album.getInfo` if MusicBrainz has no match.
          5. Final fallback to YouTube audio search.
        """
        artist_clean = artist.strip()
        album_clean = album_or_url.strip()
        if (artist_clean.startswith(("http://", "https://", "spotify:")) and not album_clean.startswith(("http://", "https://", "spotify:"))):
            album_clean, artist_clean = artist_clean, album_clean

        # 1. Spotify Album, Playlist, or Track URL/URI
        if spotify_client.is_spotify_url(album_clean) or spotify_client.is_spotify_url(artist_clean):
            target_sp = album_clean if spotify_client.is_spotify_url(album_clean) else artist_clean
            try:
                sp_meta = spotify_client.fetch_spotify_metadata(target_sp)
                if sp_meta and sp_meta.get("tracks"):
                    return sp_meta
            except Exception as exc:
                logger.warning("Spotify embed lookup failed for '%s': %s", target_sp, exc)

        # 2. YouTube Album / Playlist / Video URL
        if album_clean.startswith(("http://", "https://")):
            try:
                yt_album = youtube_client.lookup_youtube_album(album_clean, default_artist=artist_clean)
                if yt_album.get("tracks"):
                    return yt_album
            except Exception as exc:
                logger.warning("YouTube album expansion failed for '%s': %s", album_clean, exc)
            return {
                "artist": artist_clean or "Unknown Artist",
                "album": "Downloaded Album",
                "release_year": "",
                "source": "url",
                "tracks": [],
            }

        # 3. MusicBrainz release lookup
        try:
            mb_data = musicbrainz_client.fetch_album_tracklist(artist, album_or_url)
            if mb_data.get("tracks"):
                mb_data["source"] = "musicbrainz"
                return mb_data
        except Exception as exc:
            logger.warning("MusicBrainz lookup failed: %s", exc)

        # 4. Last.fm album lookup
        try:
            cfg = self.repo.get_settings()
            lfm = LastFMClient(api_key=str(cfg.get("lastfm_api_key", "")))
            lfm_tracks = lfm.fetch_album_tracklist(artist, album_or_url)
            if lfm_tracks:
                return {
                    "artist": artist.strip(),
                    "album": album_or_url.strip(),
                    "release_year": "",
                    "source": "lastfm",
                    "tracks": lfm_tracks,
                }
        except Exception as exc:
            logger.warning("Last.fm album lookup failed: %s", exc)

        # 5. Final fallback: search YouTube for tracks matching "<Artist> <Album>"
        try:
            yt_matches = youtube_client.search_videos(f"{artist} {album_or_url} audio", max_results=10)
            fallback_tracks = [
                {
                    "track_number": idx,
                    "title": m["title"],
                    "artist": artist.strip() or "Unknown Artist",
                    "duration": int(m.get("duration") or 0),
                    "youtube_url": m["url"],
                }
                for idx, m in enumerate(yt_matches, start=1)
            ]
            return {
                "artist": artist.strip(),
                "album": album_or_url.strip(),
                "release_year": "",
                "source": "youtube_search",
                "tracks": fallback_tracks,
            }
        except Exception as exc:
            logger.warning("YouTube search fallback failed: %s", exc)
            return {
                "artist": artist.strip(),
                "album": album_or_url.strip(),
                "release_year": "",
                "source": "none",
                "tracks": [],
            }


    def update_track_metadata(self, track_id: int, payload: TrackMetadataUpdatePayload) -> TrackEntity:
        track = self.repo.get_track(track_id)
        if not track:
            raise ValueError(f"Track ID {track_id} not found.")

        old_album = track.album
        album_artist, artists_list, clean_title = split_artists(payload.artist_display, payload.title)
        artist_display = ", ".join(artists_list)
        new_album = payload.album.strip() or "Singles"
        new_track_num = payload.track_number if payload.track_number > 0 else track.track_number
        new_hash = compute_track_hash(album_artist, clean_title)

        if payload.remember_album_alias and old_album.lower() != new_album.lower():
            self.repo.save_album_alias(album_artist, old_album, new_album)

        old_mp3 = Path(track.file_path)
        new_mp3 = old_mp3
        new_lrc_str = track.lrc_path

        if old_mp3.exists():
            audio_tagger.update_existing_mp3_tags(
                old_mp3,
                title=clean_title,
                artists=artists_list,
                album_artist=album_artist,
                album=new_album,
                track_number=new_track_num,
                release_year=payload.release_year or track.release_year,
                original_year=payload.original_year or track.original_year,
            )
            dest_dir = (
                PATHS.downloads_dir
                / sanitize_filename(album_artist, "Unknown Artist")
                / sanitize_filename(new_album, "Singles")
            )
            dest_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{new_track_num:02d} - " if new_track_num > 0 else ""
            base_name = f"{prefix}{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
            target_mp3 = dest_dir / f"{base_name}.mp3"

            if target_mp3.resolve() != old_mp3.resolve():
                os.replace(str(old_mp3), str(target_mp3))
                new_mp3 = target_mp3
                old_lrc = Path(track.lrc_path) if track.lrc_path else old_mp3.with_suffix(".lrc")
                if old_lrc.exists():
                    target_lrc = dest_dir / f"{base_name}.lrc"
                    os.replace(str(old_lrc), str(target_lrc))
                    new_lrc_str = str(target_lrc.resolve())

        updated = self.repo.update_track_row(
            track_id,
            track_hash=new_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=new_album,
            track_number=new_track_num,
            release_year=payload.release_year or track.release_year,
            original_year=payload.original_year or track.original_year,
            file_path=str(new_mp3.resolve()),
            lrc_path=new_lrc_str,
        )
        return updated or track

    def bulk_rename_album(self, payload: AlbumBulkRenamePayload) -> dict[str, Any]:
        tracks = self.repo.list_tracks(album=payload.old_album)
        if payload.album_artist.strip():
            tracks = [
                t for t in tracks if t.album_artist.lower() == payload.album_artist.strip().lower()
            ]

        if payload.remember_alias:
            self.repo.save_album_alias(payload.album_artist, payload.old_album, payload.new_album)

        updated_count = 0
        for tr in tracks:
            self.update_track_metadata(
                tr.id,
                TrackMetadataUpdatePayload(
                    title=tr.title,
                    artist_display=tr.artist_display,
                    album=payload.new_album,
                    track_number=tr.track_number,
                    release_year=tr.release_year,
                    original_year=tr.original_year,
                    remember_album_alias=payload.remember_alias,
                ),
            )
            updated_count += 1

        return {
            "old_album": payload.old_album,
            "new_album": payload.new_album,
            "updated_tracks": updated_count,
        }

    def get_track_lyrics(self, track: TrackEntity) -> dict[str, Any]:
        if track.lrc_path and Path(track.lrc_path).exists():
            raw = Path(track.lrc_path).read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw, fallback_duration=track.duration)

        mp3_file = Path(track.file_path)
        lrc_file = mp3_file.with_suffix(".lrc")
        if lrc_file.exists():
            raw = lrc_file.read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw, fallback_duration=track.duration)

        fetched_text, sync_mode, _summary = lyrics_client.fetch_and_save_lrc(
            artist=track.album_artist or track.artist_display,
            title=track.title,
            album=track.album,
            duration=track.duration,
            target_lrc_path=lrc_file,
        )
        if fetched_text:
            self.repo.upsert_track(
                track_hash=track.track_hash,
                title=track.title,
                artist_display=track.artist_display,
                artists=track.artists,
                album_artist=track.album_artist,
                album=track.album,
                track_number=track.track_number,
                release_year=track.release_year,
                original_year=track.original_year,
                duration=track.duration,
                file_path=track.file_path,
                lrc_path=str(lrc_file.resolve()),
                lyric_sync_mode=sync_mode,
                cover_mime=track.cover_mime,
                has_cover=track.has_cover,
                lastfm_playcount=track.lastfm_playcount,
                replaygain_db=track.replaygain_db,
                bitrate_kbps=track.bitrate_kbps,
            )
            return lyrics_client.parse_lrc_content(fetched_text, fallback_duration=track.duration)
        return {"synced": False, "sync_mode": "none", "lines": []}

    def resolve_next_transition(self, current_track_id: int) -> dict[str, Any] | None:
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