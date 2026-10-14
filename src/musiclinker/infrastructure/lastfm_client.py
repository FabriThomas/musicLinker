"""Bi-directional Last.fm API client supporting chart extraction, date-range aggregation, and signed scrobbling."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import time
from typing import Any, Callable

import requests

from musiclinker.core.exceptions import LastFMAuthError, LastFMError
from musiclinker.core.logging_config import logger
from musiclinker.domain.models import CandidateTrack
from musiclinker.infrastructure.hasher import compute_track_hash

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "MusicLinker/2.0 (+https://github.com/FabriThomas/musicLink)"


class LastFMClient:
    """Handles Last.fm read queries (Top Tracks, Calendar Year Scrobbles) and write calls (Now Playing, Scrobble)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str = "",
        session_key: str = "",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.session_key = session_key.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ---- Signature & Request Helpers ----

    def _sign_params(self, params: dict[str, Any]) -> str:
        """Compute official Last.fm MD5 api_sig: sort keys alphabetically, concatenate <k><v>, append secret."""
        if not self.api_secret:
            raise LastFMAuthError("Last.fm API Secret is required to sign authentication and scrobble calls.")
        excluded = {"format", "callback"}
        payload = "".join(f"{k}{params[k]}" for k in sorted(params.keys()) if k not in excluded)
        payload += self.api_secret
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _call(self, method: str, params: dict[str, Any], signed: bool = False, http_post: bool = False) -> dict[str, Any]:
        if not self.api_key:
            raise LastFMError("Last.fm API Key is not configured. Open Settings to add your key.")
        req_params: dict[str, Any] = {
            "method": method,
            "api_key": self.api_key,
            **params,
        }
        if signed:
            req_params["api_sig"] = self._sign_params(req_params)
        req_params["format"] = "json"

        try:
            if http_post:
                resp = self.session.post(LASTFM_API_URL, data=req_params, timeout=self.timeout)
            else:
                resp = self.session.get(LASTFM_API_URL, params=req_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise LastFMError(f"Last.fm network request failed ({method}): {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            err_code = data.get("error")
            err_msg = data.get("message", "Unknown Last.fm error")
            if err_code in (4, 9, 14):
                raise LastFMAuthError(f"Last.fm authentication error ({err_code}): {err_msg}")
            raise LastFMError(f"Last.fm API error ({err_code}): {err_msg}")
        return data

    # ---- Web Authentication Flow ----

    def get_auth_token(self) -> dict[str, str]:
        """Request an unauthorized token and return the browser authorization URL."""
        data = self._call("auth.getToken", {}, signed=True, http_post=False)
        token = str(data.get("token", ""))
        if not token:
            raise LastFMAuthError("Failed to obtain Last.fm auth token.")
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"
        return {"token": token, "auth_url": auth_url}

    def exchange_token_for_session(self, token: str) -> dict[str, str]:
        """Exchange an authorized user token for a permanent Last.fm session key."""
        data = self._call("auth.getSession", {"token": token.strip()}, signed=True, http_post=False)
        session_obj = data.get("session", {})
        key = str(session_obj.get("key", ""))
        name = str(session_obj.get("name", ""))
        if not key:
            raise LastFMAuthError("User has not authorized the token in the browser yet.")
        self.session_key = key
        return {"session_key": key, "username": name}

    # ---- Extraction Operations ----

    def fetch_top_tracks_by_period(
        self,
        username: str,
        period: str = "12month",
        min_plays: int = 10,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[CandidateTrack]:
        """Fetch all user top tracks for a preset period (`7day`, `1month`, `3month`, `6month`, `12month`, `overall`) with playcount >= min_plays."""
        candidates: list[CandidateTrack] = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            if progress_cb:
                progress_cb(f"Fetching Last.fm top tracks page {page}/{total_pages} (period={period})...")
            data = self._call(
                "user.getTopTracks",
                {"user": username, "period": period, "limit": 200, "page": page},
            )
            toptracks = data.get("toptracks", {})
            attr = toptracks.get("@attr", {})
            total_pages = max(1, int(attr.get("totalPages", 1)))
            tracks = toptracks.get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            reached_threshold = False
            for item in tracks:
                playcount = int(item.get("playcount", 0))
                if playcount < min_plays:
                    reached_threshold = True
                    break
                artist_obj = item.get("artist", {})
                artist_name = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                title = str(item.get("name", "")).strip()
                if not artist_name or not title:
                    continue
                candidates.append(
                    CandidateTrack(
                        artist=artist_name.strip(),
                        title=title,
                        playcount=playcount,
                        track_hash=compute_track_hash(artist_name, title),
                        mbid=str(item.get("mbid", "")),
                    )
                )

            if reached_threshold or not tracks:
                break
            page += 1
            time.sleep(0.2)

        return candidates

    def fetch_tracks_by_calendar_year(
        self,
        username: str,
        year: int,
        min_plays: int = 10,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[CandidateTrack]:
        """Aggregate raw scrobbles across an entire calendar year and return tracks with >= min_plays."""
        start_ts = int(datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())

        counts: Counter[str] = Counter()
        exemplars: dict[str, tuple[str, str, str]] = {}  # hash -> (artist, title, album)
        page = 1
        total_pages = 1

        while page <= total_pages:
            if progress_cb:
                progress_cb(f"Scanning {year} scrobble history page {page}/{total_pages}...")
            data = self._call(
                "user.getRecentTracks",
                {
                    "user": username,
                    "from": start_ts,
                    "to": end_ts,
                    "limit": 200,
                    "page": page,
                },
            )
            recent = data.get("recenttracks", {})
            attr = recent.get("@attr", {})
            total_pages = max(1, int(attr.get("totalPages", 1)))
            tracks = recent.get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            for item in tracks:
                if isinstance(item.get("@attr"), dict) and item["@attr"].get("nowplaying") == "true":
                    continue
                artist_obj = item.get("artist", {})
                artist_name = artist_obj.get("#text", "") if isinstance(artist_obj, dict) else str(artist_obj)
                album_obj = item.get("album", {})
                album_name = album_obj.get("#text", "") if isinstance(album_obj, dict) else str(album_obj)
                title = str(item.get("name", "")).strip()
                if not artist_name or not title:
                    continue
                thash = compute_track_hash(artist_name, title)
                counts[thash] += 1
                if thash not in exemplars:
                    exemplars[thash] = (artist_name.strip(), title, album_name.strip())

            if not tracks:
                break
            page += 1
            time.sleep(0.2)

        results: list[CandidateTrack] = []
        for thash, playcount in counts.most_common():
            if playcount < min_plays:
                break
            artist_name, title, album_name = exemplars[thash]
            results.append(
                CandidateTrack(
                    artist=artist_name,
                    title=title,
                    album=album_name,
                    playcount=playcount,
                    track_hash=thash,
                )
            )
        return results

    # ---- Bi-Directional Scrobbling Operations ----

    def update_now_playing(self, artist: str, title: str, album: str = "", duration: int = 0) -> None:
        if not self.session_key:
            return
        params: dict[str, Any] = {
            "artist": artist,
            "track": title,
            "sk": self.session_key,
        }
        if album:
            params["album"] = album
        if duration > 0:
            params["duration"] = duration
        self._call("track.updateNowPlaying", params, signed=True, http_post=True)

    def submit_scrobbles(self, scrobbles: list[dict[str, Any]]) -> bool:
        """Submit up to 50 scrobbles in a single batch call per official Last.fm API spec."""
        if not scrobbles:
            return True
        if not self.session_key:
            raise LastFMAuthError("Cannot submit scrobbles without an active Last.fm session_key.")

        batch = scrobbles[:50]
        params: dict[str, Any] = {"sk": self.session_key}
        for idx, item in enumerate(batch):
            params[f"artist[{idx}]"] = item["artist"]
            params[f"track[{idx}]"] = item["title"]
            params[f"timestamp[{idx}]"] = int(item["timestamp"])
            if item.get("album"):
                params[f"album[{idx}]"] = item["album"]
            if int(item.get("duration", 0)) > 0:
                params[f"duration[{idx}]"] = int(item["duration"])

        self._call("track.scrobble", params, signed=True, http_post=True)
        logger.info("Successfully scrobbled %d track(s) to Last.fm", len(batch))
        return True