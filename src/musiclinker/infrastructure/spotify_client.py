"""Zero-auth Spotify embed scraper for albums, playlists, tracks, and high-res cover art."""

from __future__ import annotations

import json
import re
from typing import Any
import urllib.parse
import urllib.request

from musiclinker.core.exceptions import MusicLinkerError, SpotifyResolutionError
from musiclinker.core.logging_config import logger


_SPOTIFY_PATTERN = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-zA-Z0-9_-]+/)?(?:embed/)?(album|playlist|track)/|spotify:(album|playlist|track):)([a-zA-Z0-9]+)"
)

_NEXT_DATA_PATTERN = re.compile(
    r'<script id=[\x22\x27]__NEXT_DATA__[\x22\x27] type=[\x22\x27]application/json[\x22\x27]>(.*?)</script>',
    re.DOTALL,
)

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class SpotifyClient:
    """Extracts album tracklists, playlists, track metadata, and high-res cover art via Spotify's public embed page without API keys."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def is_spotify_url(text: str) -> bool:
        if not text:
            return False
        clean = text.strip()
        return bool(_SPOTIFY_PATTERN.search(clean) or "spotify.link/" in clean or "open.spotify.com" in clean)

    @staticmethod
    def parse_spotify_uri(url_or_uri: str) -> tuple[str, str] | None:
        clean = url_or_uri.strip()
        # Handle shortened spotify.link URLs
        if "spotify.link/" in clean:
            try:
                req = urllib.request.Request(clean, headers={"User-Agent": _USER_AGENT})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    clean = resp.geturl()
            except Exception as exc:
                logger.warning("Failed resolving shortened spotify.link '%s': %s", url_or_uri, exc)

        if "?" in clean:
            clean = clean.split("?")[0]

        match = _SPOTIFY_PATTERN.search(clean)
        if not match:
            return None
        entity_type = match.group(1) or match.group(2)
        entity_id = match.group(3)
        return entity_type, entity_id

    def fetch_spotify_metadata(self, url_or_uri: str) -> dict[str, Any]:
        """
        Scrape Spotify's public embed page for the given album, playlist, or track.
        Returns a dictionary with canonical artist, title/album, cover_url, release_year, and tracklist.
        """
        parsed = self.parse_spotify_uri(url_or_uri)
        if not parsed:
            raise SpotifyResolutionError(f"Could not parse valid Spotify entity from '{url_or_uri}'")

        entity_type, entity_id = parsed
        cache_key = f"{entity_type}:{entity_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"
        req = urllib.request.Request(embed_url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                html = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise SpotifyResolutionError(f"Failed fetching Spotify embed page for {embed_url}: {exc}") from exc

        match = _NEXT_DATA_PATTERN.search(html)
        entity: dict[str, Any] = {}
        if match:
            try:
                data = json.loads(match.group(1))
                entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            except Exception as exc:
                logger.warning("Failed parsing Spotify embed __NEXT_DATA__: %s", exc)

        if not entity:
            # Fallback 1: Check initial-state script
            init_match = re.search(r'<script id=[\x22\x27]initial-state[\x22\x27][^>]*>(.*?)</script>', html, re.DOTALL)
            if init_match:
                try:
                    data = json.loads(init_match.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {}) if isinstance(data, dict) else {}
                except Exception:
                    pass

        if not entity:
            # Fallback 2: OpenGraph tags
            og_title_m = re.search(r'<meta property=[\x22\x27]og:title[\x22\x27] content=[\x22\x27](.*?)[\x22\x27]', html)
            og_desc_m = re.search(r'<meta property=[\x22\x27]og:description[\x22\x27] content=[\x22\x27](.*?)[\x22\x27]', html)
            og_img_m = re.search(r'<meta property=[\x22\x27]og:image[\x22\x27] content=[\x22\x27](.*?)[\x22\x27]', html)
            og_title = og_title_m.group(1) if og_title_m else ""
            og_desc = og_desc_m.group(1) if og_desc_m else ""
            og_img = og_img_m.group(1) if og_img_m else ""
            if og_title:
                clean_title = og_title.split(" - song and lyrics by")[0].split(" | Spotify")[0].strip()
                artist_guess = ""
                if "by " in og_desc:
                    artist_guess = og_desc.split("by ")[1].split(" on Spotify")[0].split(".")[0].strip()
                return {
                    "source": "spotify",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "album": clean_title,
                    "artist": artist_guess or "Spotify",
                    "release_year": "",
                    "cover_url": og_img,
                    "tracks": [
                        {"track_number": 1, "title": clean_title, "artist": artist_guess, "duration": 0, "spotify_uri": f"spotify:{entity_type}:{entity_id}", "youtube_url": ""}
                    ],
                }
            raise SpotifyResolutionError(f"Spotify embed page did not contain metadata for {embed_url}")

        # Extract best cover art URL from visualIdentity images
        images = entity.get("visualIdentity", {}).get("image", [])
        cover_url = ""
        if images:
            # Sort by width/height descending to pick highest resolution
            sorted_imgs = sorted(images, key=lambda x: max(int(x.get("maxWidth") or 0), int(x.get("maxHeight") or 0)), reverse=True)
            cover_url = sorted_imgs[0].get("url", "")

        raw_title = entity.get("title") or entity.get("name") or "Unknown"
        raw_subtitle = entity.get("subtitle") or ""
        release_date = entity.get("releaseDate", {})
        iso_str = release_date.get("isoString", "") if isinstance(release_date, dict) else str(release_date or "")
        release_year = iso_str[:4] if len(iso_str) >= 4 else ""

        tracks: list[dict[str, Any]] = []

        if entity_type in ("album", "playlist"):
            album_name = raw_title
            album_artist = raw_subtitle or ("Various Artists" if entity_type == "playlist" else "Unknown Artist")
            raw_tracklist = entity.get("trackList", [])
            for idx, tr in enumerate(raw_tracklist, start=1):
                tr_title = tr.get("title") or f"Track {idx}"
                tr_artist = tr.get("subtitle") or album_artist
                # Duration is in ms in Spotify embed
                duration_ms = int(tr.get("duration") or 0)
                duration_sec = duration_ms // 1000 if duration_ms > 0 else 0
                tracks.append(
                    {
                        "track_number": idx,
                        "title": tr_title.strip(),
                        "artist": tr_artist.strip(),
                        "duration": duration_sec,
                        "spotify_uri": tr.get("uri", ""),
                        "youtube_url": "",
                    }
                )

            res = {
                "source": "spotify",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "album": album_name.strip(),
                "artist": album_artist.strip(),
                "release_year": release_year,
                "cover_url": cover_url,
                "tracks": tracks,
            }
        elif entity_type == "track":
            artists_list = [a.get("name") for a in entity.get("artists", []) if a.get("name")]
            artist_name = ", ".join(artists_list) if artists_list else (raw_subtitle or "Unknown Artist")
            duration_ms = int(entity.get("duration") or 0)
            duration_sec = duration_ms // 1000 if duration_ms > 0 else 0
            album_info = entity.get("album", {})
            album_name = album_info.get("name") if isinstance(album_info, dict) else "Singles"

            tracks.append(
                {
                    "track_number": 1,
                    "title": raw_title.strip(),
                    "artist": artist_name.strip(),
                    "duration": duration_sec,
                    "spotify_uri": entity.get("uri", ""),
                    "youtube_url": "",
                }
            )

            res = {
                "source": "spotify",
                "entity_type": "track",
                "entity_id": entity_id,
                "album": (album_name or "Singles").strip(),
                "artist": artist_name.strip(),
                "release_year": release_year,
                "cover_url": cover_url,
                "tracks": tracks,
            }
        else:
            raise SpotifyResolutionError(f"Unsupported Spotify entity type: {entity_type}")

        self._cache[cache_key] = res
        return res

    def fetch_cover_bytes(self, cover_url: str) -> tuple[bytes | None, str]:
        """Download cover artwork bytes from Spotify's CDN. Returns (bytes, mime_type)."""
        if not cover_url:
            return None, "image/jpeg"
        try:
            req = urllib.request.Request(cover_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                mime = "image/png" if "png" in content_type.lower() else "image/jpeg"
                return content, mime
        except Exception as exc:
            logger.warning("Failed downloading Spotify cover from '%s': %s", cover_url, exc)
            return None, "image/jpeg"


spotify_client = SpotifyClient()
