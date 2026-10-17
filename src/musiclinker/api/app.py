"""FastAPI server delivering HTTP 206 streaming, SSE jobs, Layered Hierarchy, Album Downloader, and LLM Curation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
from typing import Any, AsyncIterator

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import build_pairing_url, detect_lan_ip, verify_request_access
from musiclinker.domain.models import (
    AgentCommandRequest,
    AlbumBulkRenamePayload,
    AlbumDownloadRequest,
    AlbumMatchRequest,
    BatchScrobblePayload,
    BatchTrackDeletePayload,
    BulkAssignAlbumPayload,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    LLMCurationApplyPayload,
    PlaylistCreatePayload,
    PlaylistUpdatePayload,
    ReplaceTrackAudioPayload,
    ScrobblePayload,
    SettingsUpdatePayload,
    TrackMetadataUpdatePayload,
    TransitionRulePayload,
)
from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.curation_service import curation_service
from musiclinker.services.job_service import job_service
from musiclinker.services.library_service import library_service
from musiclinker.services.scrobble_service import scrobble_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        library_service.scan_and_sync_downloads()
        scrobble_service.flush_pending_queue()
    except Exception as exc:
        logger.warning("Startup sync warning: %s", exc)
    yield


def require_paired_client(request: Request) -> None:
    cfg = db_repo.get_settings()
    verify_request_access(
        request=request,
        expected_token=str(cfg.get("lan_pair_token", "")),
        require_auth_on_lan=bool(cfg.get("require_lan_token", True)),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="MusicLinker Personal Server",
        version="3.0.6",
        lifespan=lifespan,
    )


    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Library, Sorting, Layered Hierarchy & Album Endpoints ----

    @app.get("/api/tracks", dependencies=[Depends(require_paired_client)])
    async def list_tracks(
        search: str = "",
        artist: str = "",
        album: str = "",
        sort_by: str = "track_num",
    ) -> dict[str, Any]:
        tracks = db_repo.list_tracks(search=search, artist=artist, album=album, sort_by=sort_by)
        return {"tracks": [t.to_dict() for t in tracks], "total": len(tracks)}

    @app.get("/api/artists", dependencies=[Depends(require_paired_client)])
    async def list_artists() -> dict[str, Any]:
        return {"artists": db_repo.list_artists_summary()}

    @app.get("/api/albums", dependencies=[Depends(require_paired_client)])
    async def list_albums() -> dict[str, Any]:
        return {
            "albums": db_repo.list_albums_summary(),
            "aliases": db_repo.list_album_aliases(),
        }

    @app.get("/api/albums/lookup", dependencies=[Depends(require_paired_client)])
    def lookup_external_album(
        artist: str = Query(default=""),
        album: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        """Resolve an album or playlist's tracklist (`1..N`) from Spotify, YouTube, MusicBrainz, or Last.fm prior to downloading."""
        try:
            return library_service.lookup_external_album_tracklist(artist=artist, album_or_url=album)
        except Exception as exc:
            logger.error("Error looking up external album '%s' / '%s': %s", artist, album, exc)
            return {"artist": artist or "Unknown Artist", "album": album, "tracks": [], "error": str(exc)}


    @app.post("/api/albums/rename", dependencies=[Depends(require_paired_client)])
    def rename_album_endpoint(payload: AlbumBulkRenamePayload) -> dict[str, Any]:
        return library_service.bulk_rename_album(payload)

    @app.put("/api/tracks/{track_id}/metadata", dependencies=[Depends(require_paired_client)])
    def update_track_metadata_endpoint(track_id: int, payload: TrackMetadataUpdatePayload) -> dict[str, Any]:
        try:
            updated = library_service.update_track_metadata(track_id, payload)
            return {"track": updated.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tracks/{track_id}/candidates", dependencies=[Depends(require_paired_client)])
    def get_track_audio_candidates_endpoint(
        track_id: int,
        q: str = Query(default=""),
        max_results: int = Query(default=6, ge=1, le=15),
    ) -> dict[str, Any]:
        """Search YouTube for alternative audio versions of a track with duration comparison."""
        track = db_repo.get_track(track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found.")
        candidates_data = library_service.get_track_audio_candidates(
            track_id=track_id, custom_query=q, max_results=max_results
        )
        if isinstance(candidates_data, list):
            candidates_list = candidates_data
        elif isinstance(candidates_data, dict):
            candidates_list = candidates_data.get("candidates", [])
        else:
            candidates_list = []
        return {
            "track_id": track_id,
            "track": track.to_dict(),
            "candidates": candidates_list,
        }

    @app.get("/api/tracks/candidate-preview/{video_id}")
    async def get_candidate_preview_endpoint(video_id: str) -> FileResponse:
        """Stream a 15-second MP3 snippet of a candidate YouTube video for instant verification."""
        try:
            snippet_path = await asyncio.to_thread(library_service.generate_audio_snippet, video_id=video_id)
            return FileResponse(
                path=str(snippet_path),
                media_type="audio/mpeg",
                filename=f"preview_{video_id}.mp3",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except Exception as exc:
            logger.error("Failed to generate preview snippet for '%s': %s", video_id, exc)
            raise HTTPException(status_code=500, detail=f"Failed to generate preview snippet: {exc}") from exc

    @app.post("/api/tracks/{track_id}/replace-audio", dependencies=[Depends(require_paired_client)])
    async def replace_track_audio_endpoint(track_id: int, payload: ReplaceTrackAudioPayload) -> dict[str, Any]:
        """Replace a track's audio with an alternative version while retaining tags, lyrics and metadata."""
        try:
            updated = await asyncio.to_thread(
                library_service.replace_track_audio,
                track_id=track_id,
                youtube_url=payload.youtube_url,
                bitrate_kbps=payload.bitrate_kbps,
            )
            return {"status": "success", "track": updated.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to replace audio for track %d: %s", track_id, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/albums/match-tracklist", dependencies=[Depends(require_paired_client)])
    async def match_album_tracklist_endpoint(payload: AlbumMatchRequest) -> dict[str, Any]:
        """Fuzzy match selected local tracks against an official album tracklist."""
        try:
            return await asyncio.to_thread(
                library_service.match_tracks_to_album,
                album=payload.album,
                artist=payload.artist,
                track_ids=payload.track_ids,
            )
        except Exception as exc:
            logger.error("Failed to match album tracklist: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/tracks/bulk-assign-album", dependencies=[Depends(require_paired_client)])
    async def bulk_assign_album_endpoint(payload: BulkAssignAlbumPayload) -> dict[str, Any]:
        """Group and tag selected tracks into an album, update track numbers, and relocate files."""
        try:
            return await asyncio.to_thread(
                library_service.bulk_assign_album,
                album=payload.album,
                album_artist=payload.album_artist,
                release_year=payload.release_year,
                cover_url=payload.cover_url,
                track_assignments=payload.track_assignments,
            )
        except Exception as exc:
            logger.error("Failed to bulk assign album: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc


    @app.delete("/api/tracks/{track_id}", dependencies=[Depends(require_paired_client)])
    def delete_single_track(
        track_id: int,
        delete_file: bool = Query(default=True),
        unmark_hash: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Delete a track from SQLite and optionally remove .mp3 & .lrc from disk and unmark hash."""
        success = library_service.delete_track(track_id, delete_file=delete_file, unmark_hash=unmark_hash)
        if not success:
            raise HTTPException(status_code=404, detail="Track not found.")
        return {"status": "deleted", "track_id": track_id}

    @app.post("/api/tracks/batch-delete", dependencies=[Depends(require_paired_client)])
    def batch_delete_tracks(payload: BatchTrackDeletePayload) -> dict[str, Any]:
        """Batch delete multiple tracks from SQLite and optionally remove files from disk."""
        deleted = library_service.delete_tracks_batch(
            payload.track_ids,
            delete_files=payload.delete_files,
            unmark_hashes=payload.unmark_hashes,
        )
        return {"status": "deleted", "count": deleted}

    @app.delete("/api/albums/{album_name:path}", dependencies=[Depends(require_paired_client)])
    def delete_entire_album(
        album_name: str,
        artist: str = Query(default=""),
        delete_files: bool = Query(default=True),
    ) -> dict[str, Any]:
        """Delete all tracks belonging to an album from SQLite and disk."""
        deleted = library_service.delete_album(album_name, artist=artist, delete_files=delete_files)
        return {"status": "deleted", "count": deleted}

    @app.delete("/api/albums/aliases/{alias_id}", dependencies=[Depends(require_paired_client)])
    async def remove_album_alias(alias_id: int) -> dict[str, str]:
        """Remove a saved album canonicalization alias rule."""
        library_service.delete_album_alias(alias_id)
        return {"status": "deleted", "alias_id": str(alias_id)}

    @app.post("/api/library/rescan", dependencies=[Depends(require_paired_client)])
    def rescan_library() -> dict[str, int]:
        return library_service.scan_and_sync_downloads()

    # ---- LLM Smart Curator & Deduplication Endpoints ----

    @app.post("/api/curation/analyze", dependencies=[Depends(require_paired_client)])
    def analyze_library_with_llm() -> dict[str, Any]:
        return curation_service.analyze_library()

    @app.post("/api/curation/agent", dependencies=[Depends(require_paired_client)])
    def run_curation_agent(req: AgentCommandRequest) -> dict[str, Any]:
        """Interactive AI Curator Agent: executes natural language instructions, answers queries, and proposes fixes."""
        return curation_service.run_agent_command(req.instruction, history=req.history)

    @app.post("/api/curation/apply", dependencies=[Depends(require_paired_client)])
    def apply_llm_curation(payload: LLMCurationApplyPayload) -> dict[str, int]:
        return curation_service.apply_curation_actions(payload.actions)

    # ---- Reverse Proxy Endpoints ----

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    @app.api_route("/v1", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def reverse_proxy_v1(request: Request, path: str = "") -> Response:
        """Reverse proxy forwarding requests to the configured target server URL with authentication."""
        cfg = db_repo.get_settings()
        target_server_url = str(cfg.get("proxy_server_url", "")).strip().rstrip("/")
        if not target_server_url:
            raise HTTPException(status_code=502, detail="Reverse proxy server URL is not configured.")

        # Authenticate incoming caller via Bearer token, X-Proxy-Password, or query param
        auth_header = request.headers.get("Authorization", "")
        provided_password = ""
        if auth_header.startswith("Bearer "):
            provided_password = auth_header[7:].strip()
        elif "X-Proxy-Password" in request.headers:
            provided_password = request.headers["X-Proxy-Password"].strip()
        elif "password" in request.query_params:
            provided_password = request.query_params["password"].strip()

        configured_password = str(cfg.get("proxy_password", "")).strip()
        if configured_password and not hmac.compare_digest(provided_password, configured_password):
            raise HTTPException(status_code=401, detail="Invalid proxy password.")

        # Construct target URL
        suffix = f"/{path}" if path else ""
        if target_server_url.endswith("/v1"):
            target_url = f"{target_server_url}{suffix}"
        else:
            target_url = f"{target_server_url}/v1{suffix}"

        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        # Forward body and headers
        body = await request.body()
        req_headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        proxy_name = str(cfg.get("proxy_name", "default"))
        req_headers["X-Forwarded-For"] = request.client.host if request.client else "127.0.0.1"
        req_headers["X-Proxy-Name"] = proxy_name

        try:
            target_resp = await asyncio.to_thread(
                requests.request,
                method=request.method,
                url=target_url,
                headers=req_headers,
                data=body,
                timeout=60.0,
                allow_redirects=False,
            )
            # Filter hop-by-hop headers
            excluded = {"content-encoding", "transfer-encoding", "connection"}
            resp_headers = {k: v for k, v in target_resp.headers.items() if k.lower() not in excluded}
            resp_headers["X-Proxy-By"] = "MusicLinker"
            resp_headers["X-Proxy-Name"] = proxy_name
            return Response(
                content=target_resp.content,
                status_code=target_resp.status_code,
                headers=resp_headers,
                media_type=target_resp.headers.get("content-type"),
            )
        except requests.RequestException as exc:
            logger.error("Reverse proxy error forwarding to %s: %s", target_url, exc)
            raise HTTPException(status_code=502, detail=f"Proxy error: {exc}") from exc

    # ---- HTTP 206 Audio Streaming, Cover Art & 3-Tier Lyrics ----

    @app.get("/api/tracks/{track_id}/stream", dependencies=[Depends(require_paired_client)])
    async def stream_track(track_id: int, request: Request) -> Response:
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
    def get_track_lyrics(track_id: int) -> dict[str, Any]:
        track = db_repo.get_track(track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found.")
        return library_service.get_track_lyrics(track)

    @app.post("/api/tracks/{track_id}/lyrics/offset", dependencies=[Depends(require_paired_client)])
    def adjust_track_lyrics_offset(track_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        shift_sec = float(payload.get("shift_seconds", 0.0))
        if abs(shift_sec) < 0.001:
            return {"status": "noop", "message": "Zero shift requested"}
        return library_service.permanently_shift_track_lyrics(track_id, shift_sec)

    @app.get("/api/system/storage-info", dependencies=[Depends(require_paired_client)])
    def get_storage_info() -> dict[str, Any]:
        return {
            "downloads_dir": str(PATHS.downloads_dir.resolve()),
            "app_data_dir": str(PATHS.app_data_dir.resolve()),
            "root_dir": str(PATHS.root_dir.resolve()),
            "total_tracks": len(db_repo.list_tracks()),
        }

    @app.post("/api/system/open-storage", dependencies=[Depends(require_paired_client)])
    def open_storage_folder() -> dict[str, Any]:
        try:
            target = PATHS.downloads_dir
            target.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(target))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(target)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
            return {"success": True, "opened_path": str(target)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ---- Player, Scrobbling & Custom Transitions ----

    @app.post("/api/player/now-playing", dependencies=[Depends(require_paired_client)])
    def player_now_playing(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.notify_now_playing(payload)

    @app.post("/api/player/scrobble", dependencies=[Depends(require_paired_client)])
    def player_scrobble(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.record_scrobble(payload)

    @app.post("/api/player/scrobble/batch", dependencies=[Depends(require_paired_client)])
    def player_scrobble_batch(payload: BatchScrobblePayload) -> dict[str, Any]:
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

    # ---- Customizable Playlists ----

    @app.get("/api/playlists", dependencies=[Depends(require_paired_client)])
    async def get_playlists() -> dict[str, Any]:
        return {"playlists": db_repo.list_playlists()}

    @app.post("/api/playlists", dependencies=[Depends(require_paired_client)])
    async def create_new_playlist(payload: PlaylistCreatePayload) -> dict[str, Any]:
        try:
            return db_repo.create_playlist(
                name=payload.name,
                description=payload.description,
                track_ids=payload.track_ids,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/playlists/{playlist_id}", dependencies=[Depends(require_paired_client)])
    async def get_playlist_details(playlist_id: int) -> dict[str, Any]:
        pl = db_repo.get_playlist(playlist_id)
        if not pl:
            raise HTTPException(status_code=404, detail="Playlist not found.")
        return pl

    @app.put("/api/playlists/{playlist_id}", dependencies=[Depends(require_paired_client)])
    async def update_playlist_details(playlist_id: int, payload: PlaylistUpdatePayload) -> dict[str, Any]:
        try:
            return db_repo.update_playlist(
                playlist_id=playlist_id,
                name=payload.name,
                description=payload.description,
                track_ids=payload.track_ids,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/playlists/{playlist_id}", dependencies=[Depends(require_paired_client)])
    async def delete_playlist_by_id(playlist_id: int) -> dict[str, str]:
        db_repo.delete_playlist(playlist_id)
        return {"status": "deleted"}

    @app.post("/api/playlists/{playlist_id}/tracks", dependencies=[Depends(require_paired_client)])
    async def add_tracks_to_playlist_route(playlist_id: int, payload: dict[str, list[int]]) -> dict[str, Any]:
        track_ids = payload.get("track_ids", [])
        return db_repo.add_tracks_to_playlist(playlist_id, track_ids)

    @app.delete("/api/playlists/{playlist_id}/tracks/{track_id}", dependencies=[Depends(require_paired_client)])
    async def remove_track_from_playlist_route(playlist_id: int, track_id: int) -> dict[str, Any]:
        db_repo.remove_track_from_playlist(playlist_id, track_id)
        return db_repo.get_playlist(playlist_id) or {"status": "removed"}

    # ---- YouTube / Spotify / Direct Link Search, Last.fm Extraction & Full Album Download Jobs ----

    @app.get("/api/youtube/search", dependencies=[Depends(require_paired_client)])
    def search_youtube(q: str = Query(..., min_length=2)) -> dict[str, Any]:
        results = library_service.preview_single_track_or_link(q)
        return {"results": results}

    @app.post("/api/jobs/extract", dependencies=[Depends(require_paired_client)])
    async def start_extract_job(req: ExtractionJobRequest) -> dict[str, Any]:
        job = job_service.start_lastfm_extraction_job(req)
        return job.to_dict()

    @app.post("/api/jobs/download", dependencies=[Depends(require_paired_client)])
    async def start_direct_download(req: DirectDownloadRequest) -> dict[str, Any]:
        try:
            job = job_service.start_direct_download_job(req)
            return job.to_dict()
        except Exception as exc:
            logger.error("Failed to start direct download job: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/download-album", dependencies=[Depends(require_paired_client)])
    async def start_album_download(req: AlbumDownloadRequest) -> dict[str, Any]:
        try:
            job = job_service.start_album_download_job(req)
            return job.to_dict()
        except Exception as exc:
            logger.error("Failed to start album download job: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc


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

    @app.post("/api/jobs/{job_id}/pause", dependencies=[Depends(require_paired_client)])
    async def pause_job(job_id: str) -> dict[str, Any]:
        if not job_service.pause_job(job_id):
            raise HTTPException(status_code=400, detail="Job cannot be paused.")
        return {"status": "paused"}

    @app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(require_paired_client)])
    async def resume_job(job_id: str) -> dict[str, Any]:
        if not job_service.resume_job(job_id):
            raise HTTPException(status_code=400, detail="Job cannot be resumed.")
        return {"status": "resumed"}

    @app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(require_paired_client)])
    async def retry_job(job_id: str) -> dict[str, Any]:
        if not job_service.retry_job(job_id):
            raise HTTPException(status_code=400, detail="Job cannot be retried.")
        return {"status": "requeued"}

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_paired_client)])
    async def stream_job_events(job_id: str) -> StreamingResponse:
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        async def _event_generator() -> AsyncIterator[str]:
            while True:
                payload = json.dumps(job.to_dict())
                yield f"data: {payload}\n\n"
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(_event_generator(), media_type="text/event-stream")

    # ---- Phone / APK LAN Pairing & Settings Endpoints ----

    @app.get("/api/sync/info", dependencies=[Depends(require_paired_client)])
    async def get_sync_info(request: Request) -> dict[str, Any]:
        cfg = db_repo.get_settings()
        port = request.url.port or 8101
        token = str(cfg.get("lan_pair_token", ""))
        pairing_url = build_pairing_url(port=port, pair_token=token)
        return {
            "lan_ip": detect_lan_ip(),
            "port": port,
            "server_origin": f"http://{detect_lan_ip()}:{port}",
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
            "lastfm_scrobble_enabled": bool(cfg.get("lastfm_scrobble_enabled", True)),
            "lastfm_nowplaying_enabled": bool(cfg.get("lastfm_nowplaying_enabled", True)),
            "default_bitrate_kbps": cfg.get("default_bitrate_kbps", 320),
            "default_min_plays": cfg.get("default_min_plays", 10),
            "default_download_cap": cfg.get("default_download_cap", 25),
            "require_lan_token": cfg.get("require_lan_token", True),
            "normalize_audio": cfg.get("normalize_audio", True),
            "auto_clean_albums": cfg.get("auto_clean_albums", True),
            "llm_base_url": cfg.get("llm_base_url", "https://api.openai.com/v1"),
            "llm_api_key_configured": bool(cfg.get("llm_api_key")),
            "llm_model": cfg.get("llm_model", "gpt-4o-mini"),
            "proxy_name": cfg.get("proxy_name", "default"),
            "proxy_server_url": cfg.get("proxy_server_url", ""),
            "proxy_password_configured": bool(cfg.get("proxy_password")),
            "proxy_password": cfg.get("proxy_password", ""),
            "preferred_download_method": cfg.get("preferred_download_method", "auto"),
        }

    @app.put("/api/settings", dependencies=[Depends(require_paired_client)])
    async def save_settings(payload: SettingsUpdatePayload) -> dict[str, Any]:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        db_repo.update_settings(updates)
        return await read_settings()

    @app.post("/api/lastfm/auth/start", dependencies=[Depends(require_paired_client)])
    def start_lastfm_web_auth() -> dict[str, str]:
        cfg = db_repo.get_settings()
        client = LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
        )
        return client.get_auth_token()

    @app.post("/api/lastfm/auth/complete", dependencies=[Depends(require_paired_client)])
    def complete_lastfm_web_auth(token: str = Query(...)) -> dict[str, str]:
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

    @app.post("/api/lastfm/disconnect", dependencies=[Depends(require_paired_client)])
    def disconnect_lastfm_endpoint() -> dict[str, Any]:
        return scrobble_service.disconnect_lastfm()

    @app.post("/api/lastfm/scrobble-pending", dependencies=[Depends(require_paired_client)])
    def flush_pending_scrobbles_endpoint() -> dict[str, Any]:
        flushed = scrobble_service.flush_pending_queue()
        return {"flushed": flushed, "pending_count": len(db_repo.get_pending_scrobbles(limit=500))}

    # ---- PWA / APK Static Files & Root Entrypoint ----

    if (PATHS.web_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(PATHS.web_dir / "js")), name="js")
    if (PATHS.web_dir / "css").exists():
        app.mount("/css", StaticFiles(directory=str(PATHS.web_dir / "css")), name="css")

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