import time
import hashlib
import requests
from typing import List, Dict, Any, Optional, Callable
from collections import defaultdict

import db

BASE_URL = "https://ws.audioscrobbler.com/2.0/"

class LastFMError(Exception):
    pass

class LastFMClient:
    def __init__(self, api_key: str, api_secret: str = "", session_key: str = ""):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.session_key = session_key.strip()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MusicLinker/2.0 (https://github.com/)"
        })

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """
        Generate MD5 api_sig for authenticated Last.fm methods.
        Sort parameters alphabetically by key, append secret, and compute MD5.
        """
        if not self.api_secret:
            raise LastFMError("API Secret is required for authenticated Last.fm requests (scrobbling).")

        filtered = {k: str(v) for k, v in params.items() if k not in ("format", "callback")}
        sorted_keys = sorted(filtered.keys())
        sig_str = "".join(f"{k}{filtered[k]}" for k in sorted_keys) + self.api_secret
        return hashlib.md5(sig_str.encode("utf-8")).hexdigest()

    def _request(self, params: Dict[str, Any], method: str = "GET", authenticated: bool = False, retries: int = 3) -> Dict[str, Any]:
        params["api_key"] = self.api_key
        params["format"] = "json"

        if authenticated:
            if not self.session_key and "token" not in params:
                raise LastFMError("Session key is missing. Please authorize with Last.fm first.")
            if self.session_key and "sk" not in params:
                params["sk"] = self.session_key
            params["api_sig"] = self._generate_signature(params)

        for attempt in range(retries):
            try:
                if method.upper() == "POST":
                    resp = self.session.post(BASE_URL, data=params, timeout=15)
                else:
                    resp = self.session.get(BASE_URL, params=params, timeout=15)

                data = resp.json()

                if "error" in data:
                    err_code = data.get("error")
                    err_msg = data.get("message", "Unknown Last.fm error")
                    if err_code == 10:
                        raise LastFMError("Invalid API key! Please check your credentials.")
                    elif err_code == 4:
                        raise LastFMError("Authentication failed: Invalid session key.")
                    elif err_code == 29:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    else:
                        raise LastFMError(f"Last.fm Error {err_code}: {err_msg}")

                return data
            except (requests.RequestException, ValueError) as e:
                if attempt == retries - 1:
                    raise LastFMError(f"Network error connecting to Last.fm: {e}")
                time.sleep(1.0 * (attempt + 1))

        raise LastFMError("Max retries exceeded while querying Last.fm.")

    # Authentication Handshake
    def get_auth_token(self) -> str:
        """Fetch request token for user web authorization."""
        data = self._request({"method": "auth.getToken"})
        return data.get("token", "")

    def get_auth_url(self, token: str) -> str:
        """URL user visits in browser to authorize the app."""
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"

    def create_session(self, token: str) -> Dict[str, str]:
        """Exchange authorized token for a permanent session key."""
        params = {
            "method": "auth.getSession",
            "token": token
        }
        data = self._request(params, method="GET", authenticated=True)
        session = data.get("session", {})
        self.session_key = session.get("key", "")
        return {
            "username": session.get("name", ""),
            "session_key": self.session_key
        }

    # Scrobbling API
    def update_now_playing(self, artist: str, track: str, album: str = "") -> bool:
        """Post 'Now Playing' status to Last.fm."""
        if not self.session_key or not self.api_secret:
            return False
        try:
            params = {
                "method": "track.updateNowPlaying",
                "artist": artist,
                "track": track
            }
            if album:
                params["album"] = album
            self._request(params, method="POST", authenticated=True)
            return True
        except Exception as e:
            print(f"[Last.fm Now Playing Warning]: {e}")
            return False

    def scrobble_track(self, artist: str, track: str, album: str = "", timestamp: Optional[int] = None) -> bool:
        """Submit an official scrobble to Last.fm."""
        if not self.session_key or not self.api_secret:
            # Queue offline
            db.queue_scrobble(artist, track, album, timestamp)
            return False

        ts = timestamp or int(time.time())
        try:
            params = {
                "method": "track.scrobble",
                "artist": artist,
                "track": track,
                "timestamp": ts
            }
            if album:
                params["album"] = album
            self._request(params, method="POST", authenticated=True)
            return True
        except Exception as e:
            # Queue offline on failure
            db.queue_scrobble(artist, track, album, ts)
            return False

    def flush_offline_scrobbles(self) -> int:
        """Flush all pending offline scrobbles to Last.fm."""
        if not self.session_key or not self.api_secret:
            return 0

        pending = db.get_pending_scrobbles()
        if not pending:
            return 0

        flushed = 0
        for item in pending:
            qid = item["id"]
            try:
                params = {
                    "method": "track.scrobble",
                    "artist": item["artist"],
                    "track": item["track"],
                    "timestamp": item["played_at"]
                }
                if item.get("album"):
                    params["album"] = item["album"]
                self._request(params, method="POST", authenticated=True)
                db.mark_scrobble_synced(qid)
                flushed += 1
            except Exception as e:
                db.mark_scrobble_failed(qid, str(e))

        return flushed

    # Track Extraction (Read API)
    def get_top_tracks(
        self,
        username: str,
        period: str = "12month",
        min_plays: int = 10,
        max_pages: int = 50,
        progress_cb: Optional[Callable[[int, int, str], None]] = None
    ) -> List[Dict[str, Any]]:
        tracks_result: List[Dict[str, Any]] = []
        page = 1
        total_pages = 1

        while page <= total_pages and page <= max_pages:
            if progress_cb:
                progress_cb(page, total_pages, f"Fetching page {page} of top tracks...")

            data = self._request({
                "method": "user.gettoptracks",
                "user": username,
                "period": period,
                "limit": 100,
                "page": page
            })

            toptracks = data.get("toptracks", {})
            attr = toptracks.get("@attr", {})
            total_pages = int(attr.get("totalPages", 1))

            raw_tracks = toptracks.get("track", [])
            if isinstance(raw_tracks, dict):
                raw_tracks = [raw_tracks]

            if not raw_tracks:
                break

            stop_paging = False
            for item in raw_tracks:
                try:
                    playcount = int(item.get("playcount", 0))
                except (ValueError, TypeError):
                    playcount = 0

                if playcount <= min_plays:
                    stop_paging = True
                    break

                artist_data = item.get("artist", {})
                artist_name = artist_data.get("name") if isinstance(artist_data, dict) else str(artist_data)
                track_name = item.get("name", "")

                images = item.get("image", [])
                image_url = ""
                if isinstance(images, list) and images:
                    for img in reversed(images):
                        if isinstance(img, dict) and img.get("#text"):
                            image_url = img["#text"]
                            break

                tracks_result.append({
                    "artist": artist_name.strip(),
                    "title": track_name.strip(),
                    "plays": playcount,
                    "lastfm_url": item.get("url", ""),
                    "image_url": image_url
                })

            if stop_paging:
                break

            page += 1
            time.sleep(0.15)

        return tracks_result

    def get_tracks_custom_range(
        self,
        username: str,
        from_ts: int,
        to_ts: int,
        min_plays: int = 10,
        max_pages: int = 100,
        progress_cb: Optional[Callable[[int, int, str], None]] = None
    ) -> List[Dict[str, Any]]:
        scrobble_counts: Dict[tuple, int] = defaultdict(int)
        track_meta: Dict[tuple, Dict[str, Any]] = {}
        page = 1
        total_pages = 1

        while page <= total_pages and page <= max_pages:
            if progress_cb:
                progress_cb(page, total_pages, f"Reading scrobbles page {page}/{total_pages}...")

            data = self._request({
                "method": "user.getrecenttracks",
                "user": username,
                "from": from_ts,
                "to": to_ts,
                "limit": 200,
                "page": page
            })

            recent = data.get("recenttracks", {})
            attr = recent.get("@attr", {})
            total_pages = int(attr.get("totalPages", 1))

            raw_tracks = recent.get("track", [])
            if isinstance(raw_tracks, dict):
                raw_tracks = [raw_tracks]

            if not raw_tracks:
                break

            for item in raw_tracks:
                if isinstance(item.get("@attr"), dict) and item["@attr"].get("nowplaying") == "true":
                    continue

                artist_data = item.get("artist", {})
                artist_name = artist_data.get("#text") if isinstance(artist_data, dict) else str(artist_data)
                track_name = item.get("name", "")
                album_data = item.get("album", {})
                album_name = album_data.get("#text") if isinstance(album_data, dict) else ""

                if not artist_name or not track_name:
                    continue

                key = (artist_name.strip(), track_name.strip())
                scrobble_counts[key] += 1

                if key not in track_meta:
                    images = item.get("image", [])
                    image_url = ""
                    if isinstance(images, list) and images:
                        for img in reversed(images):
                            if isinstance(img, dict) and img.get("#text"):
                                image_url = img["#text"]
                                break

                    track_meta[key] = {
                        "album": album_name.strip(),
                        "image_url": image_url,
                        "lastfm_url": item.get("url", "")
                    }

            page += 1
            time.sleep(0.15)

        results: List[Dict[str, Any]] = []
        for (artist, title), count in scrobble_counts.items():
            if count > min_plays:
                meta = track_meta.get((artist, title), {})
                results.append({
                    "artist": artist,
                    "title": title,
                    "plays": count,
                    "album": meta.get("album", ""),
                    "image_url": meta.get("image_url", ""),
                    "lastfm_url": meta.get("lastfm_url", "")
                })

        results.sort(key=lambda x: x["plays"], reverse=True)
        return results

    def get_track_info(self, artist: str, track: str) -> Dict[str, Any]:
        try:
            data = self._request({
                "method": "track.getInfo",
                "artist": artist,
                "track": track
            })
            track_info = data.get("track", {})
            album_info = track_info.get("album", {})

            album_name = ""
            cover_url = ""
            if isinstance(album_info, dict):
                album_name = album_info.get("title", "")
                images = album_info.get("image", [])
                if isinstance(images, list) and images:
                    for img in reversed(images):
                        if isinstance(img, dict) and img.get("#text"):
                            cover_url = img["#text"]
                            break

            return {
                "album": album_name.strip(),
                "cover_url": cover_url,
                "duration": track_info.get("duration", 0),
            }
        except Exception:
            return {"album": "", "cover_url": "", "duration": 0}
