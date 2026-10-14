"""FastAPI server delivering HTTP 206 Partial Content audio streaming, SSE job events, LAN pairing, and PWA hosting."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import build_pairing_url, detect_lan_ip, verify_request_access
from musiclinker.domain.models import (
    BatchScrobblePayload,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    ScrobblePayload,
    SettingsUpdatePayload,
    TransitionRulePayload,
)
from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.job_service import job_service
from musiclinker.services.library_service import library_service
from musiclinker.services.scrobble_service import scrobble_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Synchronize `downloads/` into `library.db` and flush pending offline scrobbles on server boot."""
    try:
        library_service.scan_and_sync_downloads()
        scrobble_service.flush_pending_queue()
    except Exception as exc:
        logger.warning("Startup sync warning: %s", exc)
    yield


def require_paired_client(request: Request) -> None:
    """Dependency verifying loopback origin or valid LAN pairing token."""
    cfg = db_repo.get_settings()
    verify_request_access(
        request=request,
        expected_token=str(cfg.get("lan_pair_token", "")),
        require_auth_on_lan=bool(cfg.get("require_lan_token", True)),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="MusicLinker Personal Server",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Library & HTTP 206 Audio Streaming Endpoints ----

    @app.get("/api/tracks", dependencies=[Depends(require_paired_client)])
    async def list_tracks(search: str = "", artist: str = "") -> dict[str, Any]:
        tracks = db_repo.list_tracks(search=search, artist=artist)
        return {"tracks": [t.to_dict() for t in tracks], "total": len(tracks)}

    @app.post("/api/library/rescan", dependencies=[Depends(require_paired_client)])
    async def rescan_library() -> dict[str, int]:
        return library_service.scan_and_sync_downloads()

    @app.get("/api/tracks/{track_id}/stream", dependencies=[Depends(require_paired_client)])
    async def stream_track(track_id: int, request: Request) -> Response:
        """Serve audio stream with full RFC 7233 HTTP 206 Partial Content byte-range support for instant scrubbing."""
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track audio file not found on disk.")

        file_path = Path(track.file_path)
        file_size = file_path.stat().st_size
        range_header = request.headers.get("range") or request.headers.get("Range")

        if not range_header:
            return FileResponse(
                path=str(file_path),
                media_type="audio/mpeg",
                headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
            )

        try:
            range_spec = range_header.strip().lower().replace("bytes=", "")
            start_str, end_str = range_spec.split("-", 1)
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
        except ValueError:
            start, end = 0, file_size - 1

        chunk_length = (end - start) + 1

        def _file_iterator(path: Path, offset: int, length: int) -> Any:
            with path.open("rb") as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(
            _file_iterator(file_path, start, chunk_length),
            status_code=206,
            media_type="audio/mpeg",
            headers=headers,
        )

    @app.get("/api/tracks/{track_id}/file", dependencies=[Depends(require_paired_client)])
    async def download_track_file(track_id: int) -> FileResponse:
        """Full MP3 blob endpoint used by the Android PWA Service Worker for 'Save for Offline' caching."""
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track file not found.")
        return FileResponse(
            path=track.file_path,
            media_type="audio/mpeg",
            filename=Path(track.file_path).name,
        )

    @app.get("/api/tracks/{track_id}/cover")
    async def get_track_cover(track_id: int) -> Response:
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track not found.")
        cover_bytes, mime = audio_tagger.extract_cover_bytes(Path(track.file_path))
        if not cover_bytes:
            raise HTTPException(status_code=404, detail="No embedded cover art.")
        return Response(content=cover_bytes, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/tracks/{track_id}/lyrics", dependencies=[Depends(require_paired_client)])
    async def get_track_lyrics(track_id: int) -> dict[str, Any]:
        track = db_repo.get_track(track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found.")
        return library_service.get_track_lyrics(track)

    # ---- Player, Scrobbling & Custom Transitions ----

    @app.post("/api/player/now-playing", dependencies=[Depends(require_paired_client)])
    async def player_now_playing(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.notify_now_playing(payload)

    @app.post("/api/player/scrobble", dependencies=[Depends(require_paired_client)])
    async def player_scrobble(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.record_scrobble(payload)

    @app.post("/api/player/scrobble/batch", dependencies=[Depends(require_paired_client)])
    async def player_scrobble_batch(payload: BatchScrobblePayload) -> dict[str, Any]:
        return scrobble_service.record_batch_scrobbles(payload.scrobbles)

    @app.get("/api/transitions", dependencies=[Depends(require_paired_client)])
    async def list_transitions() -> dict[str, Any]:
        return {"transitions": db_repo.list_all_transitions()}

    @app.post("/api/transitions", dependencies=[Depends(require_paired_client)])
    async def create_transition(payload: TransitionRulePayload) -> dict[str, Any]:
        return db_repo.upsert_transition(
            from_track_id=payload.from_track_id,
            to_track_id=payload.to_track_id,
            probability=payload.probability,
            crossfade_seconds=payload.crossfade_seconds,
        )

    @app.delete("/api/transitions/{transition_id}", dependencies=[Depends(require_paired_client)])
    async def remove_transition(transition_id: int) -> dict[str, str]:
        db_repo.delete_transition(transition_id)
        return {"status": "deleted"}

    @app.get("/api/tracks/{track_id}/next-transition", dependencies=[Depends(require_paired_client)])
    async def resolve_transition(track_id: int) -> dict[str, Any]:
        resolved = library_service.resolve_next_transition(track_id)
        return {"transition": resolved}

    # ---- YouTube Search & Background Extraction Jobs ----

    @app.get("/api/youtube/search", dependencies=[Depends(require_paired_client)])
    async def search_youtube(q: str = Query(..., min_length=2)) -> dict[str, Any]:
        results = youtube_client.search_videos(q, max_results=6)
        return {"results": results}

    @app.post("/api/jobs/extract", dependencies=[Depends(require_paired_client)])
    async def start_extract_job(req: ExtractionJobRequest) -> dict[str, Any]:
        job = job_service.start_lastfm_extraction_job(req)
        return job.to_dict()

    @app.post("/api/jobs/download", dependencies=[Depends(require_paired_client)])
    async def start_direct_download(req: DirectDownloadRequest) -> dict[str, Any]:
        job = job_service.start_direct_download_job(req)
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[Depends(require_paired_client)])
    async def get_all_jobs() -> dict[str, Any]:
        return {"jobs": job_service.list_jobs()}

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require_paired_client)])
    async def get_job_status(job_id: str) -> dict[str, Any]:
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_paired_client)])
    async def cancel_job(job_id: str) -> dict[str, Any]:
        if not job_service.cancel_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"status": "cancelling"}

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_paired_client)])
    async def stream_job_events(job_id: str) -> StreamingResponse:
        """Server-Sent Events (SSE) stream pushing real-time job progress to the UI."""
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        async def _event_generator() -> AsyncIterator[str]:
            while True:
                payload = json.dumps(job.to_dict())
                yield f"data: {payload}\n\n"
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
                await asyncio.sleep(0.6)

        return StreamingResponse(_event_generator(), media_type="text/event-stream")

    # ---- Phone LAN Pairing & Settings Endpoints ----

    @app.get("/api/sync/info", dependencies=[Depends(require_paired_client)])
    async def get_sync_info(request: Request) -> dict[str, Any]:
        cfg = db_repo.get_settings()
        port = request.url.port or 8101
        token = str(cfg.get("lan_pair_token", ""))
        pairing_url = build_pairing_url(port=port, pair_token=token)
        return {
            "lan_ip": detect_lan_ip(),
            "port": port,
            "pairing_url": pairing_url,
            "pair_token": token,
            "pending_scrobbles": len(db_repo.get_pending_scrobbles(limit=500)),
        }

    @app.get("/api/settings", dependencies=[Depends(require_paired_client)])
    async def read_settings() -> dict[str, Any]:
        cfg = db_repo.get_settings()
        return {
            "lastfm_api_key": cfg.get("lastfm_api_key", ""),
            "lastfm_api_secret_configured": bool(cfg.get("lastfm_api_secret")),
            "lastfm_username": cfg.get("lastfm_username", ""),
            "lastfm_session_connected": bool(cfg.get("lastfm_session_key")),
            "default_bitrate_kbps": cfg.get("default_bitrate_kbps", 320),
            "default_min_plays": cfg.get("default_min_plays", 10),
            "default_download_cap": cfg.get("default_download_cap", 25),
            "require_lan_token": cfg.get("require_lan_token", True),
            "normalize_audio": cfg.get("normalize_audio", True),
        }

    @app.put("/api/settings", dependencies=[Depends(require_paired_client)])
    async def save_settings(payload: SettingsUpdatePayload) -> dict[str, Any]:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        db_repo.update_settings(updates)
        return await read_settings()

    @app.post("/api/lastfm/auth/start", dependencies=[Depends(require_paired_client)])
    async def start_lastfm_web_auth() -> dict[str, str]:
        cfg = db_repo.get_settings()
        client = LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
        )
        return client.get_auth_token()

    @app.post("/api/lastfm/auth/complete", dependencies=[Depends(require_paired_client)])
    async def complete_lastfm_web_auth(token: str = Query(...)) -> dict[str, str]:
        cfg = db_repo.get_settings()
        client = LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
        )
        session_info = client.exchange_token_for_session(token)
        db_repo.update_settings(
            {
                "lastfm_session_key": session_info["session_key"],
                "lastfm_username": session_info["username"] or cfg.get("lastfm_username", ""),
            }
        )
        return session_info

    # ---- PWA Static Files & Root Entrypoint ----

    if (PATHS.web_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(PATHS.web_dir / "js")), name="js")

    @app.get("/manifest.json")
    async def serve_manifest() -> FileResponse:
        return FileResponse(str(PATHS.web_dir / "manifest.json"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def serve_service_worker() -> FileResponse:
        return FileResponse(
            str(PATHS.web_dir / "sw.js"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/icon.svg")
    async def serve_icon_svg() -> FileResponse:
        return FileResponse(str(PATHS.web_dir / "icon.svg"), media_type="image/svg+xml")

    @app.get("/")
    async def serve_spa_index(request: Request) -> Response:
        index_file = PATHS.web_dir / "index.html"
        html_content = index_file.read_text(encoding="utf-8")
        response = HTMLResponse(content=html_content)
        pair_token = request.query_params.get("pair_token")
        if pair_token:
            response.set_cookie(
                key="ml_pair_token",
                value=pair_token,
                max_age=365 * 86400,
                httponly=False,
                samesite="lax",
            )
        return response

    return app


app = create_app()