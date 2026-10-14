"""Background extraction & audio acquisition pipeline with SSE event streaming and atomic file staging."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from typing import Any

from musiclinker.core.exceptions import JobCancelledError
from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import (
    CandidateTrack,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    TrackEntity,
)
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.musicbrainz_client import musicbrainz_client
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.library_service import sanitize_filename


@dataclass
class JobRecord:
    """In-memory state of an active or historical extraction/download job."""

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    current_step: str = "Queued..."
    total_candidates: int = 0
    downloaded_count: int = 0
    skipped_seen_count: int = 0
    excess_saved_path: str = ""
    logs: list[str] = field(default_factory=list)
    tracks_added: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    created_at: int = field(default_factory=lambda: int(time.time()))

    def append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 250:
            self.logs = self.logs[-250:]
        self.current_step = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress_pct": round(self.progress_pct, 1),
            "current_step": self.current_step,
            "total_candidates": self.total_candidates,
            "downloaded_count": self.downloaded_count,
            "skipped_seen_count": self.skipped_seen_count,
            "excess_saved_path": self.excess_saved_path,
            "logs": self.logs[-60:],
            "tracks_added": self.tracks_added,
            "created_at": self.created_at,
        }


class JobService:
    """Executes extraction and download pipelines in a bounded worker pool with atomic staging."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ml_job")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def get_job(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)]

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancel_requested = True
        job.append_log("Cancellation requested by user...")
        return True

    def start_lastfm_extraction_job(self, req: ExtractionJobRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="lastfm_extract")
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run_extraction_pipeline, record, req)
        return record

    def start_direct_download_job(self, req: DirectDownloadRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="direct_download")
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run_direct_download, record, req)
        return record

    # ---- Pipeline Implementations ----

    def _run_extraction_pipeline(self, job: JobRecord, req: ExtractionJobRequest) -> None:
        job.status = JobStatus.RUNNING
        job_staging = PATHS.staging_dir / job.job_id
        try:
            cfg = self.repo.get_settings()
            api_key = str(cfg.get("lastfm_api_key", ""))
            api_secret = str(cfg.get("lastfm_api_secret", ""))
            lfm = LastFMClient(api_key=api_key, api_secret=api_secret)

            if req.period == "custom_year" and req.year:
                job.append_log(f"Extracting Last.fm scrobbles for {req.username} in year {req.year} (min plays: {req.min_plays})...")
                raw_candidates = lfm.fetch_tracks_by_calendar_year(
                    username=req.username,
                    year=req.year,
                    min_plays=req.min_plays,
                    progress_cb=job.append_log,
                )
            else:
                job.append_log(f"Extracting Last.fm top tracks for {req.username} (period: {req.period}, min plays: {req.min_plays})...")
                raw_candidates = lfm.fetch_top_tracks_by_period(
                    username=req.username,
                    period=req.period,
                    min_plays=req.min_plays,
                    progress_cb=job.append_log,
                )

            job.total_candidates = len(raw_candidates)
            job.append_log(f"Found {len(raw_candidates)} track(s) with >= {req.min_plays} plays.")

            unseen: list[CandidateTrack] = []
            for cand in raw_candidates:
                if req.skip_seen_hashes and self.repo.is_hash_seen(cand.track_hash):
                    job.skipped_seen_count += 1
                else:
                    unseen.append(cand)

            job.append_log(
                f"After SHA-256 deduplication: {len(unseen)} new track(s), {job.skipped_seen_count} previously seen."
            )

            # Enforce download cap and save excess tracks to text file
            selected = unseen[: req.download_cap]
            excess = unseen[req.download_cap :]
            if excess or not req.download_enabled:
                export_list = unseen if not req.download_enabled else excess
                if export_list:
                    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                    label = "full_list" if not req.download_enabled else "excess_tracks"
                    list_path = PATHS.excess_lists_dir / f"{req.username}_{req.period}_{label}_{ts_str}.txt"
                    lines = [f"{c.artist} - {c.title} ({c.playcount} plays)" for c in export_list]
                    list_path.write_text("\n".join(lines), encoding="utf-8")
                    job.excess_saved_path = str(list_path)
                    job.append_log(f"Saved {len(export_list)} track(s) to {list_path.name}")

            if not req.download_enabled or not selected:
                job.progress_pct = 100.0
                job.status = JobStatus.COMPLETED
                job.append_log("Extraction finished.")
                return

            total_to_dl = len(selected)
            for idx, cand in enumerate(selected, start=1):
                if job.cancel_requested:
                    raise JobCancelledError("Job cancelled by user.")
                base_pct = ((idx - 1) / total_to_dl) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total_to_dl}] Processing: {cand.artist} - {cand.title} ({cand.playcount} plays)")
                try:
                    entity = self._acquire_and_tag_single_track(
                        artist_raw=cand.artist,
                        title_raw=cand.title,
                        album_hint=cand.album,
                        query_or_url="",
                        playcount=cand.playcount,
                        bitrate_kbps=req.bitrate_kbps,
                        staging_dir=job_staging,
                        progress_cb=lambda p, b=base_pct, s=total_to_dl: setattr(
                            job, "progress_pct", b + (p / s)
                        ),
                    )
                    job.downloaded_count += 1
                    job.tracks_added.append(entity.to_dict())
                    job.append_log(f"Completed: {entity.artist_display} - {entity.title} [{entity.album}]")
                except Exception as track_exc:
                    logger.warning("Failed processing track %s - %s: %s", cand.artist, cand.title, track_exc)
                    job.append_log(f"Skipped '{cand.artist} - {cand.title}': {track_exc}")

            job.progress_pct = 100.0
            job.status = JobStatus.COMPLETED
            job.append_log(f"Job complete: {job.downloaded_count}/{total_to_dl} track(s) downloaded and indexed.")
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Extraction job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _run_direct_download(self, job: JobRecord, req: DirectDownloadRequest) -> None:
        job.status = JobStatus.RUNNING
        job_staging = PATHS.staging_dir / job.job_id
        try:
            job.total_candidates = 1
            job.append_log(f"Resolving stream for: {req.query_or_url}")
            entity = self._acquire_and_tag_single_track(
                artist_raw=req.artist_override,
                title_raw=req.title_override,
                album_hint=req.album_override,
                query_or_url=req.query_or_url,
                playcount=0,
                bitrate_kbps=req.bitrate_kbps,
                staging_dir=job_staging,
                progress_cb=lambda p: setattr(job, "progress_pct", p),
            )
            job.downloaded_count = 1
            job.tracks_added.append(entity.to_dict())
            job.progress_pct = 100.0
            job.status = JobStatus.COMPLETED
            job.append_log(f"Downloaded & tagged: {entity.artist_display} - {entity.title}")
        except Exception as exc:
            logger.exception("Direct download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _acquire_and_tag_single_track(
        self,
        *,
        artist_raw: str,
        title_raw: str,
        album_hint: str,
        query_or_url: str,
        playcount: int,
        bitrate_kbps: int,
        staging_dir: Path,
        progress_cb: Any = None,
    ) -> TrackEntity:
        """Full atomic pipeline: YouTube resolve -> FFmpeg MP3 stage -> MusicBrainz -> LRCLIB -> ID3v2.4 -> Atomic move -> SQLite."""
        if not query_or_url:
            yt_match = youtube_client.resolve_best_audio_match(artist_raw, title_raw)
            target_url = yt_match["url"]
            thumb_url = yt_match.get("thumbnail", "")
        else:
            target_url = query_or_url
            thumb_url = ""

        stem = uuid.uuid4().hex[:8]
        staged_mp3, yt_info = youtube_client.download_mp3_to_staging(
            url_or_query=target_url,
            staging_dir=staging_dir,
            stem_name=stem,
            bitrate_kbps=bitrate_kbps,
            progress_cb=progress_cb,
        )

        if not thumb_url and isinstance(yt_info, dict):
            thumb_url = str(yt_info.get("thumbnail") or "")

        # Determine artist & title if downloaded from raw YouTube query without overrides
        if not artist_raw or not title_raw:
            yt_title = str(yt_info.get("title") or "Unknown Track")
            if " - " in yt_title:
                inferred_artist, inferred_title = yt_title.split(" - ", 1)
            else:
                inferred_artist = str(yt_info.get("uploader") or "Unknown Artist")
                inferred_title = yt_title
            artist_raw = artist_raw or inferred_artist
            title_raw = title_raw or inferred_title

        album_artist, artists_list, clean_title = split_artists(artist_raw, title_raw)
        artist_display = ", ".join(artists_list)
        track_hash = compute_track_hash(album_artist, clean_title)

        # Query MusicBrainz for Original Year (TDOR), Release Year (TDRC), Album, and Front Cover
        mb_meta = musicbrainz_client.resolve_release_metadata(
            artist=album_artist,
            title=clean_title,
            fallback_album=album_hint or "Singles",
            fallback_thumb_url=thumb_url,
        )
        final_album = album_hint or mb_meta.album or "Singles"

        # Destination folder: downloads/<AlbumArtist>/<Album>/<Artist> - <Title>.mp3
        dest_dir = (
            PATHS.downloads_dir
            / sanitize_filename(album_artist, "Unknown Artist")
            / sanitize_filename(final_album, "Singles")
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        base_filename = f"{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
        final_mp3_path = dest_dir / f"{base_filename}.mp3"
        final_lrc_path = dest_dir / f"{base_filename}.lrc"

        # Fetch synced lyrics via LRCLIB
        approx_duration = int(yt_info.get("duration") or 0)
        lyrics_text = lyrics_client.fetch_and_save_lrc(
            artist=album_artist,
            title=clean_title,
            album=final_album,
            duration=approx_duration,
            target_lrc_path=final_lrc_path,
        )

        # Embed ID3v2.4 multi-artist tags, release years, ReplayGain, and front cover art
        duration, actual_bitrate = audio_tagger.write_id3v24_tags(
            staged_mp3,
            title=clean_title,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            lastfm_playcount=playcount,
            replaygain_db=-1.5,
            lyrics_text=lyrics_text,
            cover_bytes=mb_meta.cover_bytes,
            cover_mime=mb_meta.cover_mime,
        )

        # Atomic move from staging to final library location
        os.replace(str(staged_mp3), str(final_mp3_path))

        entity = self.repo.upsert_track(
            track_hash=track_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            duration=duration or approx_duration,
            file_path=str(final_mp3_path.resolve()),
            lrc_path=str(final_lrc_path.resolve()) if final_lrc_path.exists() else "",
            cover_mime=mb_meta.cover_mime if mb_meta.cover_bytes else "",
            has_cover=bool(mb_meta.cover_bytes),
            lastfm_playcount=playcount,
            replaygain_db=-1.5,
            bitrate_kbps=actual_bitrate,
        )
        self.repo.mark_hash_seen(track_hash, artist_display, clean_title, source="download")
        return entity


job_service = JobService()