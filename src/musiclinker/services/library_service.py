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

        scan_dirs = [PATHS.downloads_dir]
        legacy_dir = getattr(PATHS, "legacy_downloads_dir", None)
        if legacy_dir and legacy_dir.exists() and legacy_dir.resolve() != PATHS.downloads_dir.resolve():
            scan_dirs.append(legacy_dir)

        seen_paths: set[str] = set()
        for sdir in scan_dirs:
            if not sdir.exists():
                continue
            for mp3_path in sdir.rglob("*.mp3"):
                if ".staging" in mp3_path.parts:
                    continue
                resolved_str = str(mp3_path.resolve())
                if resolved_str in seen_paths:
                    continue
                seen_paths.add(resolved_str)
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
                        file_path=resolved_str,
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
            if mb_data and mb_data.get("tracks"):
                mb_data["source"] = "musicbrainz"
                mb_data["entity_type"] = "album"
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
                    "entity_type": "album",
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
                "entity_type": "album",
                "tracks": fallback_tracks,
            }
        except Exception as exc:
            logger.warning("YouTube search fallback failed: %s", exc)
            return {
                "artist": artist.strip(),
                "album": album_or_url.strip(),
                "release_year": "",
                "source": "none",
                "entity_type": "album",
                "tracks": [],
            }

    def preview_single_track_or_link(self, query_or_url: str) -> list[dict[str, Any]]:
        """
        Preview a single track or search query before downloading:
          - Supports Spotify Track/Album URLs
          - Supports direct YouTube / YouTube Music video URLs
          - Supports plain text search queries
        """
        cleaned = query_or_url.strip()
        if not cleaned:
            return []

        if spotify_client.is_spotify_url(cleaned):
            try:
                sp_meta = spotify_client.fetch_spotify_metadata(cleaned)
                tracks = sp_meta.get("tracks") or []
                if tracks:
                    results: list[dict[str, Any]] = []
                    for tr in tracks[:12]:
                        t_artist = str(tr.get("artist") or sp_meta.get("artist") or "Unknown Artist")
                        t_title = str(tr.get("title") or "Unknown Track")
                        results.append(
                            {
                                "id": f"sp_{tr.get('track_number', 1)}",
                                "title": f"{t_artist} - {t_title}",
                                "track_title": t_title,
                                "artist": t_artist,
                                "album": str(sp_meta.get("album") or ""),
                                "track_number": int(tr.get("track_number") or 0),
                                "uploader": f"Spotify • {sp_meta.get('album') or 'Single'}",
                                "duration": int(tr.get("duration") or 0),
                                "url": cleaned if len(tracks) == 1 else "",
                                "thumbnail": str(sp_meta.get("cover_url") or ""),
                                "source": "spotify",
                            }
                        )
                    return results
            except Exception as exc:
                logger.warning("Spotify track preview failed for '%s': %s", cleaned, exc)

        return youtube_client.preview_track_or_link(cleaned, max_results=6)



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

    def permanently_shift_track_lyrics(self, track_id: int, delta_seconds: float) -> dict[str, Any]:
        """Permanently alters the LRC file and embedded ID3 USLT frame for a track by delta_seconds."""
        track = self.repo.get_track(track_id)
        if not track:
            raise ValueError(f"Track ID {track_id} not found")

        lrc_path: Path | None = None
        if track.lrc_path and Path(track.lrc_path).exists():
            lrc_path = Path(track.lrc_path)
        else:
            mp3_cand = Path(track.file_path).with_suffix(".lrc")
            if mp3_cand.exists():
                lrc_path = mp3_cand

        raw_lrc = ""
        if lrc_path and lrc_path.exists():
            raw_lrc = lrc_path.read_text(encoding="utf-8", errors="replace")
        else:
            # Try fetching if not present yet
            self.get_track_lyrics(track)
            refreshed = self.repo.get_track(track_id) or track
            if refreshed.lrc_path and Path(refreshed.lrc_path).exists():
                lrc_path = Path(refreshed.lrc_path)
                raw_lrc = lrc_path.read_text(encoding="utf-8", errors="replace")
            else:
                lrc_cand = Path(track.file_path).with_suffix(".lrc")
                if lrc_cand.exists():
                    lrc_path = lrc_cand
                    raw_lrc = lrc_path.read_text(encoding="utf-8", errors="replace")

        if not raw_lrc or not lrc_path:
            return {"synced": False, "sync_mode": "none", "lines": [], "message": "No lyrics file found to alter"}

        shifted_lrc = lyrics_client.shift_lrc_timestamps(raw_lrc, delta_seconds)
        lrc_path.write_text(shifted_lrc, encoding="utf-8")

        # Permanently alter MP3 file ID3 tag
        mp3_file = Path(track.file_path)
        if mp3_file.exists():
            try:
                audio_tagger.update_mp3_lyrics(mp3_file, shifted_lrc)
            except Exception as exc:
                logger.warning("Could not rewrite ID3 USLT tag on %s: %s", mp3_file.name, exc)

        parsed = lyrics_client.parse_lrc_content(shifted_lrc, fallback_duration=track.duration)
        parsed["permanently_saved"] = True
        parsed["shift_applied"] = delta_seconds
        return parsed

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

    def delete_track(self, track_id: int, delete_file: bool = True, unmark_hash: bool = False) -> bool:
        track = self.repo.get_track(track_id)
        if not track:
            return False

        if delete_file:
            try:
                mp3_path = Path(track.file_path)
                if mp3_path.exists():
                    mp3_path.unlink(missing_ok=True)
                lrc_path = Path(track.lrc_path) if track.lrc_path else mp3_path.with_suffix(".lrc")
                if lrc_path.exists():
                    lrc_path.unlink(missing_ok=True)

                # Clean up parent album folder if now empty
                album_dir = mp3_path.parent
                if album_dir.exists() and not any(album_dir.iterdir()):
                    album_dir.rmdir()
                    # Clean up artist folder if now empty
                    artist_dir = album_dir.parent
                    if artist_dir.exists() and not any(artist_dir.iterdir()) and artist_dir != PATHS.downloads_dir:
                        artist_dir.rmdir()
            except Exception as exc:
                logger.warning("Error deleting files for track %s (%s): %s", track_id, track.title, exc)

        if unmark_hash:
            self.repo.unmark_hash(track.track_hash)

        self.repo.delete_track(track_id)
        return True

    def delete_tracks_batch(self, track_ids: list[int], delete_files: bool = True, unmark_hashes: bool = False) -> int:
        deleted = 0
        for tid in track_ids:
            if self.delete_track(tid, delete_file=delete_files, unmark_hash=unmark_hashes):
                deleted += 1
        return deleted

    def delete_album(self, album_name: str, artist: str = "", delete_files: bool = True) -> int:
        tracks = self.repo.list_tracks(album=album_name)
        if artist.strip():
            artist_clean = artist.strip().lower()
            tracks = [t for t in tracks if t.album_artist.lower() == artist_clean or t.artist_display.lower() == artist_clean]
        return self.delete_tracks_batch([t.id for t in tracks], delete_files=delete_files, unmark_hashes=False)

    def delete_album_alias(self, alias_id: int) -> bool:
        self.repo.delete_album_alias(alias_id)
        return True

    def get_track_audio_candidates(self, track_id: int, custom_query: str = "", max_results: int = 6) -> dict[str, Any]:
        track = self.repo.get_track(track_id)
        if not track:
            raise ValueError(f"Track ID {track_id} not found.")

        raw_results: list[dict[str, Any]] = []
        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", track.title).strip()

        if custom_query.strip():
            cq = custom_query.strip()
            try:
                raw_results.extend(youtube_client.search_videos(cq, max_results=max_results))
            except Exception as exc:
                logger.debug("Custom candidate search failed for '%s': %s", cq, exc)

        if not raw_results:
            strategies = [
                f"{track.artist_display} {clean_title} official audio",
                f"{track.artist_display} {clean_title}",
                f"{clean_title} {track.artist_display}",
                clean_title,
            ]
            for q in strategies:
                if len(raw_results) >= max_results:
                    break
                try:
                    found = youtube_client.search_videos(q, max_results=max_results)
                    raw_results.extend(found)
                except Exception as exc:
                    logger.debug("Candidate search fallback failed for '%s': %s", q, exc)

        seen_ids = set()
        candidates = []
        for r in raw_results:
            vid_id = r.get("id")
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            dur = int(r.get("duration") or 0)
            diff = dur - track.duration if track.duration > 0 and dur > 0 else 0
            sign = "+" if diff > 0 else ""
            diff_label = f"{sign}{diff}s" if dur > 0 and track.duration > 0 else ""
            candidates.append({
                "id": vid_id,
                "video_id": vid_id,
                "title": r.get("title", "Unknown"),
                "uploader": r.get("uploader", "YouTube"),
                "channel": r.get("uploader", "YouTube"),
                "album": r.get("album") or track.album or "",
                "duration": dur,
                "duration_diff": diff,
                "duration_diff_sec": diff,
                "duration_diff_label": diff_label,
                "url": r.get("url") or f"https://www.youtube.com/watch?v={vid_id}",
                "thumbnail": r.get("thumbnail", ""),
                "preview_url": f"/api/tracks/candidate-preview/{vid_id}",
            })
        return {
            "track": track.to_dict(),
            "candidates": candidates[:max_results],
        }

    def generate_audio_snippet(self, video_id: str, duration_sec: float = 15.0) -> Path:
        import subprocess
        import yt_dlp

        cache_dir = PATHS.app_data_dir / "preview_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_mp3 = cache_dir / f"{video_id}_snippet.mp3"
        if out_mp3.exists() and out_mp3.stat().st_size > 5000:
            return out_mp3

        target_url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            **youtube_client._get_base_ydl_opts(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
        audio_stream_url = info.get("url") if isinstance(info, dict) else None
        if not audio_stream_url:
            raise ValueError(f"Could not extract audio stream URL for video {video_id}")

        cmd = [
            youtube_client.ffmpeg_exe,
            "-y",
            "-ss", "0",
            "-i", audio_stream_url,
            "-t", str(duration_sec),
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            str(out_mp3),
        ]
        p = subprocess.run(cmd, capture_output=True, timeout=25)
        if p.returncode != 0 or not out_mp3.exists():
            raise RuntimeError(f"FFmpeg snippet generation failed: {p.stderr.decode('utf-8', errors='ignore')}")
        return out_mp3

    def replace_track_audio(self, track_id: int, youtube_url: str, bitrate_kbps: int = 320) -> TrackEntity:
        import gc
        import shutil
        import time

        track = self.repo.get_track(track_id)
        if not track:
            raise ValueError(f"Track ID {track_id} not found.")

        target_mp3 = Path(track.file_path)
        backup_mp3 = target_mp3.with_suffix(".mp3.bak")
        staging_dir = PATHS.app_data_dir / "swap_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Extract cover art BEFORE overwriting the original MP3
        existing_cover = None
        if target_mp3.exists():
            try:
                existing_cover, _ = audio_tagger.extract_cover_bytes(target_mp3)
            except Exception as cov_exc:
                logger.debug("Could not pre-extract cover from target MP3: %s", cov_exc)

        # 2. Download replacement audio from YouTube into staging
        staged_mp3, info, _ = youtube_client.download_mp3_to_staging(
            url_or_query=youtube_url,
            staging_dir=staging_dir,
            stem_name=f"swap_{track_id}_{random.randint(1000, 9999)}",
            bitrate_kbps=bitrate_kbps,
        )

        # 3. Safely replace the file on disk (resilient against Windows locks)
        final_mp3_path = target_mp3
        replaced_in_place = False

        for attempt in range(4):
            try:
                if target_mp3.exists() and not backup_mp3.exists():
                    shutil.copy2(target_mp3, backup_mp3)
                shutil.move(str(staged_mp3), str(target_mp3))
                replaced_in_place = True
                break
            except (PermissionError, OSError) as exc:
                gc.collect()
                time.sleep(0.35)
                if attempt == 3:
                    logger.warning("Target MP3 '%s' locked on Windows (%s); writing versioned audio file", target_mp3, exc)

        if not replaced_in_place:
            # Fallback for Windows file locks: write to a versioned filename in same folder
            version_stem = f"{target_mp3.stem}_v{int(time.time()) % 10000}"
            version_mp3 = target_mp3.with_name(f"{version_stem}.mp3")
            shutil.move(str(staged_mp3), str(version_mp3))
            final_mp3_path = version_mp3
            try:
                # Attempt to remove locked file if lock released
                target_mp3.unlink(missing_ok=True)
            except Exception:
                pass

        if backup_mp3.exists():
            try:
                backup_mp3.unlink(missing_ok=True)
            except Exception:
                pass

        # 4. Tag the new MP3 with all track metadata and preserved original cover art
        artists_list = [a.strip() for a in track.artist_display.split(",") if a.strip()] or [track.artist_display]
        audio_tagger.tag_mp3(
            mp3_path=final_mp3_path,
            title=track.title,
            artists=artists_list,
            album_artist=track.album_artist,
            album=track.album,
            track_number=track.track_number,
            release_year=track.release_year,
            original_year=track.original_year,
            cover_bytes=existing_cover,
        )

        new_duration = int(info.get("duration") or 0)
        if not new_duration:
            from mutagen.mp3 import MP3
            try:
                audio = MP3(str(final_mp3_path))
                new_duration = int(audio.info.length)
            except Exception:
                new_duration = track.duration

        updated_track = self.repo.update_track_audio_info(
            track_id=track_id,
            duration=new_duration,
            bitrate_kbps=bitrate_kbps,
            file_path=str(final_mp3_path) if final_mp3_path != target_mp3 else None,
        )
        return updated_track or track

    def match_tracks_to_album(self, album: str, artist: str, track_ids: list[int]) -> dict[str, Any]:
        import difflib

        official_data = self.resolve_album_tracklist(artist, album)
        official_tracks = official_data.get("tracks") or []
        if not official_tracks:
            raise ValueError(f"Could not resolve tracklist for '{artist}' - '{album}'.")

        local_tracks = []
        for tid in track_ids:
            t = self.repo.get_track(tid)
            if t:
                local_tracks.append(t)

        if not local_tracks:
            raise ValueError("No valid tracks selected for matching.")

        def clean_title_for_diff(title: str) -> str:
            t = re.sub(r"\(.*?\)|\[.*?\]", "", title.lower())
            t = re.sub(r"[^a-z0-9\s]", "", t)
            return t.strip()

        proposed_mappings: list[dict[str, Any]] = []
        used_local_ids = set()

        for off in official_tracks:
            off_num = int(off.get("track_number") or len(proposed_mappings) + 1)
            off_title = off.get("title", "")
            clean_off = clean_title_for_diff(off_title)

            best_track = None
            best_score = 0.0

            for lt in local_tracks:
                if lt.id in used_local_ids:
                    continue
                clean_lt = clean_title_for_diff(lt.title)
                score = difflib.SequenceMatcher(None, clean_off, clean_lt).ratio()
                if score > best_score:
                    best_score = score
                    best_track = lt

            if best_track and best_score >= 0.40:
                used_local_ids.add(best_track.id)
                proposed_mappings.append({
                    "official_track_number": off_num,
                    "official_title": off_title,
                    "official_artist": off.get("artist") or official_data.get("artist") or "",
                    "official_duration": int(off.get("duration") or 0),
                    "matched_track_id": best_track.id,
                    "matched_track_title": best_track.title,
                    "matched_track_duration": best_track.duration,
                    "confidence": round(best_score * 100, 1),
                })
            else:
                proposed_mappings.append({
                    "official_track_number": off_num,
                    "official_title": off_title,
                    "official_artist": off.get("artist") or official_data.get("artist") or "",
                    "official_duration": int(off.get("duration") or 0),
                    "matched_track_id": None,
                    "matched_track_title": "",
                    "matched_track_duration": 0,
                    "confidence": 0.0,
                })

        unmatched = [t.to_dict() for t in local_tracks if t.id not in used_local_ids]

        return {
            "official_album": official_data.get("album") or album,
            "official_artist": official_data.get("artist") or artist,
            "official_year": official_data.get("release_year") or "",
            "official_cover_url": official_data.get("cover_url") or "",
            "mappings": proposed_mappings,
            "unmatched_tracks": unmatched,
            "all_selected_tracks": [t.to_dict() for t in local_tracks],
        }

    def bulk_assign_album(
        self,
        album: str,
        album_artist: str,
        release_year: str,
        cover_url: str,
        track_assignments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import shutil

        updated_count = 0
        dest_dir = (
            PATHS.downloads_dir
            / sanitize_filename(album_artist or "Various Artists", "Various Artists")
            / sanitize_filename(album or "Collection", "Collection")
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        cover_bytes = None
        if cover_url:
            try:
                cover_bytes, _ = spotify_client.fetch_cover_bytes(cover_url)
            except Exception as e:
                logger.warning("Could not fetch album cover bytes from %s: %s", cover_url, e)

        for assign in track_assignments:
            track_id = int(assign.get("track_id") or 0)
            if not track_id:
                continue
            track = self.repo.get_track(track_id)
            if not track:
                continue

            track_num = int(assign.get("track_number") or track.track_number or 0)
            title = assign.get("title") or track.title

            old_file = Path(track.file_path)
            new_file = old_file

            if old_file.exists():
                artists_list = [a.strip() for a in track.artist_display.split(",") if a.strip()] or [track.artist_display]
                audio_tagger.tag_mp3(
                    mp3_path=old_file,
                    title=title,
                    artists=artists_list,
                    album_artist=album_artist or track.album_artist,
                    album=album,
                    track_number=track_num,
                    release_year=release_year or track.release_year,
                    original_year=track.original_year,
                    cover_bytes=cover_bytes,
                )
                prefix = f"{track_num:02d} - " if track_num > 0 else ""
                new_name = f"{prefix}{sanitize_filename(track.artist_display)} - {sanitize_filename(title)}.mp3"
                target_dest = dest_dir / new_name
                if str(old_file.resolve()) != str(target_dest.resolve()):
                    try:
                        shutil.move(str(old_file), str(target_dest))
                        new_file = target_dest
                        old_lrc = Path(track.lrc_path) if track.lrc_path else old_file.with_suffix(".lrc")
                        if old_lrc.exists():
                            shutil.move(str(old_lrc), str(target_dest.with_suffix(".lrc")))
                    except Exception as mv_e:
                        logger.warning("Could not move track file %s: %s", old_file, mv_e)

            new_hash = compute_track_hash(album_artist or track.album_artist, title)
            self.repo.update_track_row(
                track_id=track.id,
                track_hash=new_hash,
                title=title,
                artist_display=track.artist_display,
                artists=[a.strip() for a in track.artist_display.split(",") if a.strip()],
                album_artist=album_artist or track.album_artist,
                album=album,
                track_number=track_num,
                release_year=release_year or track.release_year,
                original_year=track.original_year,
                file_path=str(new_file),
                lrc_path=str(new_file.with_suffix(".lrc")) if new_file.with_suffix(".lrc").exists() else track.lrc_path,
            )
            updated_count += 1

        return {
            "status": "ok",
            "updated_count": updated_count,
            "album": album,
            "album_artist": album_artist,
        }


library_service = LibraryService()