"""Background extraction, direct download, and Full Album Download pipeline with SSE telemetry."""

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
    AlbumDownloadRequest,
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
from musiclinker.infrastructure.spotify_client import spotify_client
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client

from musiclinker.services.curation_service import curation_service
from musiclinker.services.library_service import library_service, sanitize_filename


import queue


@dataclass
class JobRecord:
    """In-memory state of an active or historical extraction, track download, or full album download job."""

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    current_step: str = "Queued..."
    current_stage: str = "idle"
    lyric_sync_status: str = "Waiting..."
    total_candidates: int = 0
    downloaded_count: int = 0
    skipped_seen_count: int = 0
    failed_count: int = 0
    excess_saved_path: str = ""
    logs: list[str] = field(default_factory=list)
    tracks_added: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    pause_requested: bool = False
    created_at: int = field(default_factory=lambda: int(time.time()))

    def __post_init__(self) -> None:
        self._pause_event = threading.Event()
        self._pause_event.set()

    def append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 250:
            self.logs = self.logs[-250:]
        self.current_step = message

    def pause(self) -> None:
        if self.status == JobStatus.RUNNING:
            self.pause_requested = True
            self.status = JobStatus.PAUSED
            self._pause_event.clear()
            self.append_log("Download job paused by user.")

    def resume(self) -> None:
        if self.status == JobStatus.PAUSED:
            self.pause_requested = False
            self.status = JobStatus.RUNNING
            self._pause_event.set()
            self.append_log("Download job resumed.")

    def check_paused_or_cancelled(self) -> None:
        if self.cancel_requested:
            raise JobCancelledError("Job cancelled by user.")
        while self.pause_requested and not self.cancel_requested:
            time.sleep(0.3)
        if self.cancel_requested:
            raise JobCancelledError("Job cancelled by user.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress_pct": round(self.progress_pct, 1),
            "current_step": self.current_step,
            "current_stage": self.current_stage,
            "lyric_sync_status": self.lyric_sync_status,
            "total_candidates": self.total_candidates,
            "downloaded_count": self.downloaded_count,
            "skipped_seen_count": self.skipped_seen_count,
            "failed_count": self.failed_count,
            "excess_saved_path": self.excess_saved_path,
            "logs": self.logs[-60:],
            "tracks_added": self.tracks_added,
            "is_paused": self.status == JobStatus.PAUSED,
            "created_at": self.created_at,
        }


class JobService:
    """Executes Last.fm extraction, single track downloads, and Full Album Downloads in a strict FIFO sequential queue."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo
        self._jobs: dict[str, JobRecord] = {}
        self._job_requests: dict[str, Any] = {}
        self._fifo_queue: queue.Queue[tuple[JobRecord, Any]] = queue.Queue()
        self._lock = threading.Lock()
        self._worker_thread = threading.Thread(target=self._fifo_worker_loop, daemon=True, name="ml_fifo_worker")
        self._worker_thread.start()

    def _fifo_worker_loop(self) -> None:
        """Sequential single-worker FIFO loop executing jobs one after another without collisions."""
        while True:
            try:
                record, req = self._fifo_queue.get()
                if record.cancel_requested:
                    record.status = JobStatus.CANCELLED
                    record.current_stage = "cancelled"
                    record.append_log("Cancelled before execution started.")
                    self._fifo_queue.task_done()
                    continue

                if record.job_type == "lastfm_extract":
                    self._run_extraction_pipeline(record, req)
                elif record.job_type == "direct_download":
                    self._run_direct_download(record, req)
                elif record.job_type == "album_download":
                    self._run_album_download_pipeline(record, req)
                self._fifo_queue.task_done()
            except Exception as exc:
                logger.error("Error in FIFO worker loop: %s", exc)
                time.sleep(1)

    def get_job(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)]

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancel_requested = True
        job.resume()  # Unblock if paused
        job.append_log("Cancellation requested by user...")
        return True

    def pause_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.RUNNING:
            return False
        job.pause()
        return True

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.PAUSED:
            return False
        job.resume()
        return True

    def retry_job(self, job_id: str) -> bool:
        """Retry a failed or completed job from where it left off, re-enqueuing into FIFO queue."""
        job = self._jobs.get(job_id)
        req = self._job_requests.get(job_id)
        if not job or not req:
            return False
        if job.status in (JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.PAUSED):
            return False  # Already active or queued
        job.status = JobStatus.QUEUED
        job.cancel_requested = False
        job.pause_requested = False
        job.current_stage = "requeued"
        job.append_log("Job re-enqueued for retry connection.")
        self._fifo_queue.put((job, req))
        return True

    def start_lastfm_extraction_job(self, req: ExtractionJobRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="lastfm_extract")
        with self._lock:
            self._jobs[job_id] = record
            self._job_requests[job_id] = req
        self._fifo_queue.put((record, req))
        return record

    def start_direct_download_job(self, req: DirectDownloadRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="direct_download")
        with self._lock:
            self._jobs[job_id] = record
            self._job_requests[job_id] = req
        self._fifo_queue.put((record, req))
        return record

    def start_album_download_job(self, req: AlbumDownloadRequest) -> JobRecord:
        """Start a background job that resolves and downloads an entire album in track # order (`1..N`) via FIFO queue."""
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="album_download")
        with self._lock:
            self._jobs[job_id] = record
            self._job_requests[job_id] = req
        self._fifo_queue.put((record, req))
        return record

    def _run_album_download_pipeline(self, job: JobRecord, req: AlbumDownloadRequest) -> None:
        job.status = JobStatus.RUNNING
        job.current_stage = "resolving_album"
        job_staging = PATHS.staging_dir / job.job_id
        try:
            tracks_to_dl = req.tracks
            album_artist = req.artist.strip()
            raw_album_name = req.album.strip()
            lookup: dict[str, Any] = {}

            if not tracks_to_dl:
                job.append_log(f"Resolving official tracklist for '{album_artist or 'URL'} — {raw_album_name}'...")
                lookup = library_service.lookup_external_album_tracklist(album_artist, raw_album_name)
                raw_list = lookup.get("tracks") or []
                if lookup.get("artist") and (not album_artist or album_artist.lower() in ("unknown artist", "unknown", "")):
                    album_artist = str(lookup["artist"]).strip()
                canonical_album = str(lookup.get("album") or raw_album_name)

                from musiclinker.domain.models import AlbumTrackItem

                tracks_to_dl = [
                    AlbumTrackItem(
                        track_number=int(item.get("track_number") or idx),
                        title=str(item.get("title") or f"Track {idx}"),
                        artist=str(item.get("artist") or album_artist or "Unknown Artist"),
                        duration=int(item.get("duration") or 0),
                        youtube_url=str(item.get("youtube_url") or ""),
                    )
                    for idx, item in enumerate(raw_list, start=1)
                ]
            else:
                canonical_album = raw_album_name

            canonical_album = self.repo.resolve_album_alias(album_artist, canonical_album)

            if not tracks_to_dl:
                raise ValueError(f"Could not find tracklist for '{album_artist} — {raw_album_name}'.")

            job.total_candidates = len(tracks_to_dl)
            job.append_log(f"Album '{canonical_album}' by '{album_artist}' resolved: {len(tracks_to_dl)} track(s) queued.")

            album_cover_bytes, album_cover_mime = None, "image/jpeg"
            album_release_year = str(lookup.get("release_year") or "").strip()

            if lookup and lookup.get("cover_url"):
                album_cover_bytes, album_cover_mime = spotify_client.fetch_cover_bytes(lookup["cover_url"])
                if album_cover_bytes:
                    job.append_log(f"Cached verified album artwork from Spotify for '{canonical_album}' ({len(album_cover_bytes)} bytes).")

            if not album_cover_bytes and (spotify_client.is_spotify_url(raw_album_name) or spotify_client.is_spotify_url(album_artist)):
                try:
                    sp_target = raw_album_name if spotify_client.is_spotify_url(raw_album_name) else album_artist
                    sp_meta = spotify_client.fetch_spotify_metadata(sp_target)
                    if sp_meta.get("release_year") and not album_release_year:
                        album_release_year = str(sp_meta["release_year"]).strip()
                    if sp_meta.get("cover_url"):
                        album_cover_bytes, album_cover_mime = spotify_client.fetch_cover_bytes(sp_meta["cover_url"])
                        if album_cover_bytes:
                            job.append_log(f"Cached verified album artwork from Spotify for '{canonical_album}' ({len(album_cover_bytes)} bytes).")
                except Exception as exc:
                    logger.debug("Failed fetching cover from Spotify: %s", exc)

            if not album_cover_bytes and album_artist:
                album_cover_bytes, album_cover_mime = musicbrainz_client.fetch_album_cover(album_artist, canonical_album)
                if album_cover_bytes:
                    job.append_log(f"Cached verified album artwork from MusicBrainz for '{canonical_album}' ({len(album_cover_bytes)} bytes).")

            if not album_release_year and album_artist:
                try:
                    mb_tracklist = musicbrainz_client.fetch_album_tracklist(album_artist, canonical_album)
                    if mb_tracklist and mb_tracklist.get("release_year"):
                        album_release_year = str(mb_tracklist["release_year"]).strip()
                except Exception as exc:
                    logger.debug("Failed resolving MusicBrainz release year: %s", exc)

            if album_release_year:
                job.append_log(f"Verified canonical album release year: {album_release_year}")

            total = len(tracks_to_dl)
            for idx, item in enumerate(tracks_to_dl, start=1):
                job.check_paused_or_cancelled()

                thash = compute_track_hash(item.artist, item.title)
                if req.skip_existing and self.repo.is_hash_seen(thash):
                    job.skipped_seen_count += 1
                    job.append_log(f"[{idx}/{total}] Skipping #{item.track_number:02d} {item.title} (already in library)")
                    continue

                base_pct = ((idx - 1) / total) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total}] Downloading Track #{item.track_number:02d}: {item.artist} - {item.title}")
                try:
                    entity = self._acquire_and_tag_single_track(
                        job=job,
                        artist_raw=item.artist,
                        title_raw=item.title,
                        album_hint=canonical_album,
                        track_number_hint=item.track_number,
                        album_cover_override=album_cover_bytes,
                        album_cover_mime=album_cover_mime,
                        album_year_override=album_release_year or None,
                        expected_duration=item.duration,
                        query_or_url=item.youtube_url,
                        playcount=0,
                        bitrate_kbps=req.bitrate_kbps,
                        download_method=req.download_method,
                        staging_dir=job_staging,
                        progress_cb=lambda p, b=base_pct, s=total: setattr(
                            job, "progress_pct", b + (p / s)
                        ),
                    )
                    job.downloaded_count += 1
                    job.tracks_added.append(entity.to_dict())
                    job.append_log(
                        f"Indexed #{entity.track_number:02d}: {entity.title} [{entity.album} | Year: {entity.release_year or 'N/A'} | Lyrics: {entity.lyric_sync_mode}]"
                    )
                except Exception as track_exc:
                    logger.warning("Album track download failed (%s): %s", item.title, track_exc)
                    job.failed_count += 1
                    job.append_log(f"Skipped #{item.track_number:02d} '{item.title}': {track_exc}")

            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(
                f"Album '{canonical_album}' complete: {job.downloaded_count}/{total} track(s) downloaded."
            )
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.current_stage = "cancelled"
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Album download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _run_extraction_pipeline(self, job: JobRecord, req: ExtractionJobRequest) -> None:
        job.status = JobStatus.RUNNING
        job.current_stage = "extracting_lastfm"
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

            if req.use_llm_filter and raw_candidates:
                job.current_stage = "llm_curation"
                raw_candidates = curation_service.filter_candidates_before_download(
                    raw_candidates, log_cb=job.append_log
                )

            job.total_candidates = len(raw_candidates)
            job.append_log(f"Found {len(raw_candidates)} qualifying track(s).")

            unseen: list[CandidateTrack] = []
            for cand in raw_candidates:
                if req.skip_seen_hashes and self.repo.is_hash_seen(cand.track_hash):
                    job.skipped_seen_count += 1
                else:
                    unseen.append(cand)

            job.append_log(
                f"Deduplication check: {len(unseen)} new track(s), {job.skipped_seen_count} already in library/seen."
            )

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
                    job.append_log(f"Saved {len(export_list)} excess track(s) to {list_path.name}")

            if not req.download_enabled or not selected:
                job.progress_pct = 100.0
                job.current_stage = "completed"
                job.status = JobStatus.COMPLETED
                job.append_log("Extraction finished.")
                return

            total_to_dl = len(selected)
            for idx, cand in enumerate(selected, start=1):
                job.check_paused_or_cancelled()
                base_pct = ((idx - 1) / total_to_dl) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total_to_dl}] Downloading: {cand.artist} - {cand.title} ({cand.playcount} plays)")
                try:
                    entity = self._acquire_and_tag_single_track(
                        job=job,
                        artist_raw=cand.artist,
                        title_raw=cand.title,
                        album_hint=cand.album,
                        track_number_hint=cand.track_number,
                        query_or_url="",
                        playcount=cand.playcount,
                        bitrate_kbps=req.bitrate_kbps,
                        download_method=req.preferred_download_method,
                        staging_dir=job_staging,
                        progress_cb=lambda p, b=base_pct, s=total_to_dl: setattr(
                            job, "progress_pct", b + (p / s)
                        ),
                    )
                    job.downloaded_count += 1
                    job.tracks_added.append(entity.to_dict())
                    job.append_log(
                        f"Indexed: {entity.artist_display} - {entity.title} [Album: {entity.album} | Lyrics: {entity.lyric_sync_mode}]"
                    )
                except Exception as track_exc:
                    logger.warning("Failed processing track %s - %s: %s", cand.artist, cand.title, track_exc)
                    job.append_log(f"Skipped '{cand.artist} - {cand.title}': {track_exc}")

            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(f"Complete: {job.downloaded_count}/{total_to_dl} track(s) downloaded and tagged.")
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.current_stage = "cancelled"
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Extraction job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _run_direct_download(self, job: JobRecord, req: DirectDownloadRequest) -> None:
        job.status = JobStatus.RUNNING
        job_staging = PATHS.staging_dir / job.job_id
        try:
            job.total_candidates = 1
            job.append_log(f"Resolving stream for: {req.query_or_url}")

            artist_override = req.artist_override
            title_override = req.title_override
            album_override = req.album_override
            track_num_override = req.track_number_override
            cover_override_bytes = None
            cover_override_mime = "image/jpeg"
            target_query_or_url = req.query_or_url

            expected_dur = 0
            if spotify_client.is_spotify_url(req.query_or_url):
                try:
                    job.append_log(f"Extracting Spotify metadata from: {req.query_or_url}")
                    sp_meta = spotify_client.fetch_spotify_metadata(req.query_or_url)
                    sp_tracks = sp_meta.get("tracks") or []
                    if len(sp_tracks) > 1 and not title_override:
                        job.append_log(
                            f"Detected multi-track Spotify {sp_meta.get('entity_type', 'playlist/album')} ({len(sp_tracks)} tracks) — switching to full collection pipeline..."
                        )
                        album_req = AlbumDownloadRequest(
                            artist=artist_override or str(sp_meta.get("artist") or "Various Artists"),
                            album=album_override or str(sp_meta.get("album") or req.query_or_url),
                            bitrate_kbps=req.bitrate_kbps,
                            download_method=req.download_method,
                            skip_existing=True,
                        )
                        self._run_album_download_pipeline(job, album_req)
                        return
                    artist_override = artist_override or sp_meta.get("artist", "")
                    if sp_tracks:
                        first_tr = sp_tracks[0]
                        title_override = title_override or first_tr.get("title", "")
                        artist_override = artist_override or first_tr.get("artist", "")
                        expected_dur = int(first_tr.get("duration") or 0)
                        if not track_num_override and first_tr.get("track_number"):
                            track_num_override = int(first_tr["track_number"])
                    album_override = album_override or sp_meta.get("album", "")
                    if sp_meta.get("cover_url"):
                        cover_override_bytes, cover_override_mime = spotify_client.fetch_cover_bytes(sp_meta["cover_url"])
                        if cover_override_bytes:
                            job.append_log(f"Fetched verified cover artwork from Spotify ({len(cover_override_bytes)} bytes)")
                    target_query_or_url = ""  # Let youtube_client resolve official YouTube Music studio track
                except Exception as sp_exc:
                    logger.warning("Failed extracting Spotify metadata from '%s': %s", req.query_or_url, sp_exc)

            entity = self._acquire_and_tag_single_track(
                job=job,
                artist_raw=artist_override,
                title_raw=title_override,
                album_hint=album_override,
                track_number_hint=track_num_override,
                album_cover_override=cover_override_bytes,
                album_cover_mime=cover_override_mime,
                expected_duration=expected_dur,
                query_or_url=target_query_or_url,
                playcount=0,
                bitrate_kbps=req.bitrate_kbps,
                download_method=req.download_method,
                staging_dir=job_staging,
                progress_cb=lambda p: setattr(job, "progress_pct", p),
            )
            job.downloaded_count = 1
            job.tracks_added.append(entity.to_dict())

            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(f"Downloaded & tagged: {entity.artist_display} - {entity.title} [{entity.album}]")
        except Exception as exc:
            logger.exception("Direct download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _acquire_and_tag_single_track(
        self,
        *,
        job: JobRecord,
        artist_raw: str,
        title_raw: str,
        album_hint: str,
        track_number_hint: int = 0,
        album_cover_override: bytes | None = None,
        album_cover_mime: str = "image/jpeg",
        album_year_override: str | None = None,
        expected_duration: int = 0,
        query_or_url: str,
        playcount: int,
        bitrate_kbps: int,
        download_method: str = "auto",
        staging_dir: Path,
        progress_cb: Any = None,
    ) -> TrackEntity:
        job.check_paused_or_cancelled()
        job.current_stage = "youtube_audio_and_captions"
        job.lyric_sync_status = "Checking YouTube stream & subtitle cues..."
        if not query_or_url:
            resolved_method = download_method or str(self.repo.get_settings().get("preferred_download_method", "auto"))
            yt_match = youtube_client.resolve_best_audio_match(
                artist_raw,
                title_raw,
                preferred_method=resolved_method,
                expected_duration=expected_duration,
            )
            target_url = yt_match["url"]
            thumb_url = yt_match.get("thumbnail", "")
        else:
            target_url = query_or_url
            thumb_url = ""

        stem = uuid.uuid4().hex[:8]
        staged_mp3, yt_info, yt_lrc_fallback = youtube_client.download_mp3_to_staging(
            url_or_query=target_url,
            staging_dir=staging_dir,
            stem_name=stem,
            bitrate_kbps=bitrate_kbps,
            progress_cb=progress_cb,
        )

        if not thumb_url and isinstance(yt_info, dict):
            thumb_url = str(yt_info.get("thumbnail") or "")

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

        job.current_stage = "musicbrainz_metadata"
        mb_meta = musicbrainz_client.resolve_release_metadata(
            artist=album_artist,
            title=clean_title,
            fallback_album=album_hint or "Singles",
            fallback_thumb_url=thumb_url,
            fallback_track_number=track_number_hint,
        )
        raw_album = album_hint or mb_meta.album or "Singles"
        final_album = self.repo.resolve_album_alias(album_artist, raw_album, clean_title)
        final_track_num = track_number_hint if track_number_hint > 0 else mb_meta.track_number

        # If album cover was explicitly provided (e.g. from canonical album release), use it
        if album_cover_override:
            cover_bytes_to_use = album_cover_override
            cover_mime_to_use = album_cover_mime
        else:
            cover_bytes_to_use = mb_meta.cover_bytes
            cover_mime_to_use = mb_meta.cover_mime

        dest_dir = (
            PATHS.downloads_dir
            / sanitize_filename(album_artist, "Unknown Artist")
            / sanitize_filename(final_album, "Singles")
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        num_prefix = f"{final_track_num:02d} - " if final_track_num > 0 else ""
        base_filename = f"{num_prefix}{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
        final_mp3_path = dest_dir / f"{base_filename}.mp3"
        final_lrc_path = dest_dir / f"{base_filename}.lrc"

        # Check if previous version of this track exists under a different filename or directory;
        # if so, clean up the obsolete files to avoid orphaned / mixed up tracks on disk
        existing_track = self.repo.get_track_by_hash(track_hash)
        if existing_track and existing_track.file_path:
            old_p = Path(existing_track.file_path)
            if old_p.resolve() != final_mp3_path.resolve():
                try:
                    if old_p.exists():
                        old_p.unlink(missing_ok=True)
                    old_lrc = Path(existing_track.lrc_path) if existing_track.lrc_path else old_p.with_suffix(".lrc")
                    if old_lrc.exists():
                        old_lrc.unlink(missing_ok=True)
                except Exception as clean_err:
                    logger.debug("Failed cleaning old track file %s: %s", old_p, clean_err)

        job.current_stage = "lyrics_sync"
        approx_duration = int(yt_info.get("duration") or 0)
        lyrics_text, sync_mode, sync_summary = lyrics_client.fetch_and_save_lrc(
            artist=album_artist,
            title=clean_title,
            album=final_album,
            duration=approx_duration,
            target_lrc_path=final_lrc_path,
            youtube_lrc_fallback=yt_lrc_fallback,
        )
        job.lyric_sync_status = sync_summary
        job.append_log(f"Lyric Sync: {sync_summary}")

        job.current_stage = "id3_tagging"
        raw_loudness = yt_info.get("loudness_db") if isinstance(yt_info, dict) else None
        try:
            replaygain_val = round(-float(raw_loudness), 2) if raw_loudness is not None else -1.5
        except (TypeError, ValueError):
            replaygain_val = -1.5

        final_release_year = str(album_year_override or mb_meta.release_year or "").strip()
        final_original_year = str(album_year_override or mb_meta.original_year or final_release_year).strip()

        duration, actual_bitrate = audio_tagger.write_id3v24_tags(
            staged_mp3,
            title=clean_title,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            track_number=final_track_num,
            release_year=final_release_year,
            original_year=final_original_year,
            lastfm_playcount=playcount,
            replaygain_db=replaygain_val,
            lyrics_text=lyrics_text,
            cover_bytes=cover_bytes_to_use,
            cover_mime=cover_mime_to_use,
        )

        os.replace(str(staged_mp3), str(final_mp3_path))

        entity = self.repo.upsert_track(
            track_hash=track_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            track_number=final_track_num,
            release_year=final_release_year,
            original_year=final_original_year,
            duration=duration or approx_duration,
            file_path=str(final_mp3_path.resolve()),
            lrc_path=str(final_lrc_path.resolve()) if final_lrc_path.exists() else "",
            lyric_sync_mode=sync_mode,
            cover_mime=cover_mime_to_use if cover_bytes_to_use else "",
            has_cover=bool(cover_bytes_to_use),
            lastfm_playcount=playcount,
            replaygain_db=replaygain_val,
            bitrate_kbps=actual_bitrate,
        )
        self.repo.mark_hash_seen(track_hash, artist_display, clean_title, source="download")
        return entity


job_service = JobService()