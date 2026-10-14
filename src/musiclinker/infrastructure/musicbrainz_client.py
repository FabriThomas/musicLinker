"""MusicBrainz & Cover Art Archive client resolving original release years (TDOR) and front cover art."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ReleaseMetadata

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
MB_USER_AGENT = "MusicLinker/2.0 ( https://github.com/FabriThomas/musicLink )"


class MusicBrainzClient:
    """Rate-limited (1 req/sec) MusicBrainz metadata resolver with in-memory caching."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": MB_USER_AGENT, "Accept": "application/json"})
        self._lock = threading.Lock()
        self._last_call_ts = 0.0
        self._cache: dict[str, ReleaseMetadata] = {}

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call_ts
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            self._last_call_ts = time.monotonic()

    def resolve_release_metadata(
        self,
        artist: str,
        title: str,
        fallback_album: str = "",
        fallback_thumb_url: str = "",
    ) -> ReleaseMetadata:
        """Look up recording on MusicBrainz to determine Original Release Year (TDOR), Release Year (TDRC), Album, and Cover Art."""
        cache_key = f"{artist.lower().strip()}::{title.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        meta = ReleaseMetadata(album=fallback_album or "Singles", album_artist=artist)
        try:
            self._throttle()
            query = f'artist:"{artist}" AND recording:"{title}"'
            resp = self.session.get(
                f"{MB_BASE_URL}/recording",
                params={"query": query, "limit": 5, "fmt": "json"},
                timeout=10.0,
            )
            if resp.ok:
                recordings = resp.json().get("recordings", [])
                if recordings:
                    best = recordings[0]
                    first_release_date = str(best.get("first-release-date", ""))
                    if len(first_release_date) >= 4:
                        meta.original_year = first_release_date[:4]
                        meta.release_year = first_release_date[:4]

                    releases = best.get("releases", [])
                    if releases:
                        chosen = self._pick_canonical_release(releases)
                        meta.album = str(chosen.get("title") or meta.album)
                        meta.release_mbid = str(chosen.get("id") or "")
                        rel_date = str(chosen.get("date") or "")
                        if len(rel_date) >= 4:
                            meta.release_year = rel_date[:4]
                            if not meta.original_year:
                                meta.original_year = meta.release_year
        except Exception as exc:
            logger.debug("MusicBrainz lookup skipped for %s - %s: %s", artist, title, exc)

        # Fetch Cover Art from Cover Art Archive first, fallback to YouTube thumbnail
        if meta.release_mbid:
            cover_bytes, mime = self._download_cover_art(f"{CAA_BASE_URL}/release/{meta.release_mbid}/front-500")
            if cover_bytes:
                meta.cover_bytes = cover_bytes
                meta.cover_mime = mime

        if not meta.cover_bytes and fallback_thumb_url:
            cover_bytes, mime = self._download_cover_art(fallback_thumb_url)
            if cover_bytes:
                meta.cover_bytes = cover_bytes
                meta.cover_mime = mime

        self._cache[cache_key] = meta
        return meta

    @staticmethod
    def _pick_canonical_release(releases: list[dict[str, Any]]) -> dict[str, Any]:
        for rel in releases:
            status = str(rel.get("status", "")).lower()
            rg = rel.get("release-group", {})
            primary_type = str(rg.get("primary-type", "")).lower()
            if status == "official" and primary_type == "album":
                return rel
        return releases[0]

    def _download_cover_art(self, url: str) -> tuple[bytes | None, str]:
        try:
            resp = self.session.get(url, timeout=8.0)
            if resp.ok and resp.content:
                ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                if ctype.startswith("image/"):
                    return resp.content, ctype
        except Exception:
            pass
        return None, "image/jpeg"


musicbrainz_client = MusicBrainzClient()