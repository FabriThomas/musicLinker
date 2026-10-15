"""MusicBrainz & Cover Art Archive client resolving original release years (TDOR), track numbers, and full album tracklists."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ReleaseMetadata
from musiclinker.infrastructure.hasher import clean_album_name

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
MB_USER_AGENT = "MusicLinker/3.0 ( https://github.com/FabriThomas/musicLink )"


class MusicBrainzClient:
    """Rate-limited (1 req/sec) MusicBrainz metadata and full album tracklist resolver."""

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

    def fetch_album_tracklist(self, artist: str, album: str) -> dict[str, Any]:
        """
        Resolve a complete studio album's ordered tracklist (`1..N`), release year, and front cover URL from MusicBrainz.
        """
        clean_alb = clean_album_name(album)
        result: dict[str, Any] = {
            "artist": artist.strip(),
            "album": clean_alb,
            "release_year": "",
            "release_mbid": "",
            "cover_url": "",
            "tracks": [],
        }
        try:
            self._throttle()
            query = f'artist:"{artist.strip()}" AND release:"{clean_alb}" AND status:official'
            resp = self.session.get(
                f"{MB_BASE_URL}/release",
                params={"query": query, "limit": 5, "fmt": "json"},
                timeout=10.0,
            )
            if not resp.ok:
                return result
            releases = resp.json().get("releases", [])
            if not releases:
                return result

            chosen = releases[0]
            release_mbid = str(chosen.get("id") or "")
            result["release_mbid"] = release_mbid
            result["album"] = clean_album_name(str(chosen.get("title") or clean_alb))
            rel_date = str(chosen.get("date") or "")
            if len(rel_date) >= 4:
                result["release_year"] = rel_date[:4]
            if release_mbid:
                result["cover_url"] = f"{CAA_BASE_URL}/release/{release_mbid}/front-500"

            # Fetch full medium/track listing for the chosen release
            self._throttle()
            detail_resp = self.session.get(
                f"{MB_BASE_URL}/release/{release_mbid}",
                params={"inc": "recordings+artist-credits", "fmt": "json"},
                timeout=10.0,
            )
            if detail_resp.ok:
                media = detail_resp.json().get("media", [])
                tracks_out: list[dict[str, Any]] = []
                running_num = 1
                for medium in media:
                    for tr in medium.get("tracks", []) or []:
                        title = str(tr.get("title") or "").strip()
                        if not title:
                            continue
                        pos = int(tr.get("position") or running_num)
                        length_ms = int(tr.get("length") or 0)
                        tracks_out.append(
                            {
                                "track_number": pos,
                                "title": title,
                                "artist": artist.strip(),
                                "duration": round(length_ms / 1000) if length_ms > 0 else 0,
                                "youtube_url": "",
                            }
                        )
                        running_num += 1
                result["tracks"] = tracks_out
        except Exception as exc:
            logger.warning("MusicBrainz album tracklist lookup failed for %s - %s: %s", artist, album, exc)
        return result

    def fetch_album_cover(self, artist: str, album: str) -> tuple[bytes | None, str]:
        """Fetch canonical album artwork bytes and mime type for an artist and album release group."""
        try:
            tracklist_info = self.fetch_album_tracklist(artist, album)
            cover_url = tracklist_info.get("cover_url") if isinstance(tracklist_info, dict) else ""
            if cover_url:
                return self._download_cover_art(cover_url)
        except Exception as exc:
            logger.debug("Failed fetching album cover for %s - %s: %s", artist, album, exc)
        return None, "image/jpeg"

    def resolve_release_metadata(
        self,
        artist: str,
        title: str,
        fallback_album: str = "",
        fallback_thumb_url: str = "",
        fallback_track_number: int = 0,
    ) -> ReleaseMetadata:
        cache_key = f"{artist.lower().strip()}::{title.lower().strip()}::{fallback_album.lower().strip()}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if fallback_track_number > 0 and cached.track_number == 0:
                cached.track_number = fallback_track_number
            return cached

        meta = ReleaseMetadata(
            album=clean_album_name(fallback_album, title),
            album_artist=artist,
            track_number=fallback_track_number,
        )
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
                        chosen = self._pick_canonical_release(releases, target_album=fallback_album)
                        raw_mb_album = str(chosen.get("title") or meta.album)
                        if fallback_album and fallback_album.strip().lower() != "singles":
                            # Keep explicit album hint if provided, unless MusicBrainz exact match is found
                            meta.album = clean_album_name(fallback_album, title)
                        else:
                            meta.album = clean_album_name(raw_mb_album, title)
                        meta.release_mbid = str(chosen.get("id") or "")
                        rel_date = str(chosen.get("date") or "")
                        if len(rel_date) >= 4:
                            meta.release_year = rel_date[:4]
                            if not meta.original_year:
                                meta.original_year = meta.release_year

                        # Extract track number from media -> track[0].number if present
                        if meta.track_number == 0:
                            media_list = chosen.get("media") or []
                            if media_list and isinstance(media_list[0], dict):
                                tr_list = media_list[0].get("track") or []
                                if tr_list and isinstance(tr_list[0], dict):
                                    num_str = str(tr_list[0].get("number") or "")
                                    if num_str.isdigit():
                                        meta.track_number = int(num_str)
        except Exception as exc:
            logger.debug("MusicBrainz lookup skipped for %s - %s: %s", artist, title, exc)

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
    def _pick_canonical_release(releases: list[dict[str, Any]], target_album: str = "") -> dict[str, Any]:
        norm_target = target_album.strip().lower() if target_album else ""
        if norm_target and norm_target != "singles":
            for rel in releases:
                title = str(rel.get("title") or "").strip().lower()
                if title == norm_target:
                    return rel
        for rel in releases:
            status = str(rel.get("status", "")).lower()
            rg = rel.get("release-group", {})
            primary_type = str(rg.get("primary-type", "")).lower()
            secondary = rg.get("secondary-types") or []
            if status == "official" and primary_type == "album" and not secondary:
                return rel
        for rel in releases:
            status = str(rel.get("status", "")).lower()
            rg = rel.get("release-group", {})
            if status == "official" and str(rg.get("primary-type", "")).lower() == "album":
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