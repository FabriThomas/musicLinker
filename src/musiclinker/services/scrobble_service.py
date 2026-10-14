"""Real-time Now Playing notification and offline-resilient Last.fm scrobble manager."""

from __future__ import annotations

from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ScrobblePayload
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient


class ScrobbleService:
    """Manages real-time Last.fm updates and flushes queued offline scrobbles when connectivity returns."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def _build_client(self) -> LastFMClient:
        cfg = self.repo.get_settings()
        return LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
            session_key=str(cfg.get("lastfm_session_key", "")),
        )

    def notify_now_playing(self, payload: ScrobblePayload) -> dict[str, Any]:
        client = self._build_client()
        if not client.session_key or not client.api_key:
            return {"status": "skipped", "reason": "Last.fm session not configured"}
        try:
            client.update_now_playing(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
            )
            return {"status": "ok"}
        except Exception as exc:
            logger.debug("Now Playing update skipped (offline or unauthenticated): %s", exc)
            return {"status": "offline", "detail": str(exc)}

    def record_scrobble(self, payload: ScrobblePayload) -> dict[str, Any]:
        """Record local play count, attempt immediate Last.fm scrobble, or queue to SQLite if offline."""
        if payload.track_id is not None:
            self.repo.increment_local_playcount(payload.track_id)

        client = self._build_client()
        item = {
            "artist": payload.artist,
            "title": payload.title,
            "album": payload.album,
            "duration": payload.duration,
            "timestamp": payload.timestamp,
        }

        if not client.session_key or not client.api_key:
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
                error="Unauthenticated session; queued locally",
            )
            return {"status": "queued", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}

        try:
            client.submit_scrobbles([item])
            # Piggyback flush of any previously queued offline scrobbles
            self.flush_pending_queue()
            return {"status": "scrobbled", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}
        except Exception as exc:
            logger.warning("Scrobble failed, queuing for later retry: %s", exc)
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
                error=str(exc),
            )
            return {"status": "queued", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}

    def record_batch_scrobbles(self, items: list[ScrobblePayload]) -> dict[str, Any]:
        """Ingest a batch of offline scrobbles from an Android PWA client and flush to Last.fm."""
        for payload in items:
            if payload.track_id is not None:
                self.repo.increment_local_playcount(payload.track_id)
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
            )
        flushed = self.flush_pending_queue()
        return {
            "ingested": len(items),
            "flushed": flushed,
            "pending_count": len(self.repo.get_pending_scrobbles(limit=500)),
        }

    def flush_pending_queue(self) -> int:
        """Flush pending SQLite `scrobble_queue` items to Last.fm in batches of 50."""
        pending = self.repo.get_pending_scrobbles(limit=50)
        if not pending:
            return 0
        client = self._build_client()
        if not client.session_key or not client.api_key:
            return 0
        try:
            client.submit_scrobbles(pending)
            ids = [int(row["id"]) for row in pending]
            self.repo.mark_scrobbles_synced(ids)
            return len(ids)
        except Exception as exc:
            logger.debug("Pending scrobble flush deferred: %s", exc)
            return 0


scrobble_service = ScrobbleService()