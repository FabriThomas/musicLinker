"""LRCLIB synced karaoke lyrics client and .lrc timestamp parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import requests

from musiclinker.core.logging_config import logger

LRCLIB_API_URL = "https://lrclib.net/api"
_LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\](.*)")


class LyricsClient:
    """Queries LRCLIB for timestamped `.lrc` lyrics and parses `.lrc` files for the karaoke UI."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MusicLinker/2.0 (https://github.com/FabriThomas/musicLink)"})

    def fetch_and_save_lrc(
        self,
        artist: str,
        title: str,
        album: str,
        duration: int,
        target_lrc_path: Path,
    ) -> str:
        """Query LRCLIB for synced lyrics, write sidecar `.lrc` file if found, and return the raw text."""
        synced_text, plain_text = self.fetch_lyrics(artist, title, album, duration)
        content_to_save = synced_text or plain_text
        if content_to_save:
            try:
                target_lrc_path.write_text(content_to_save, encoding="utf-8")
                return content_to_save
            except Exception as exc:
                logger.warning("Failed writing LRC file %s: %s", target_lrc_path, exc)
        return ""

    def fetch_lyrics(self, artist: str, title: str, album: str = "", duration: int = 0) -> tuple[str, str]:
        """Return `(synced_lyrics, plain_lyrics)` from LRCLIB."""
        try:
            params: dict[str, Any] = {"artist_name": artist, "track_name": title}
            if album:
                params["album_name"] = album
            if duration > 0:
                params["duration"] = duration
            resp = self.session.get(f"{LRCLIB_API_URL}/get", params=params, timeout=8.0)
            if resp.status_code == 404:
                # Fallback to LRCLIB search endpoint
                search_resp = self.session.get(
                    f"{LRCLIB_API_URL}/search",
                    params={"q": f"{artist} {title}"},
                    timeout=8.0,
                )
                if search_resp.ok:
                    items = search_resp.json()
                    if isinstance(items, list) and items:
                        first = items[0]
                        return str(first.get("syncedLyrics") or ""), str(first.get("plainLyrics") or "")
                return "", ""
            if resp.ok:
                data = resp.json()
                return str(data.get("syncedLyrics") or ""), str(data.get("plainLyrics") or "")
        except Exception as exc:
            logger.debug("LRCLIB lookup skipped for %s - %s: %s", artist, title, exc)
        return "", ""

    @staticmethod
    def parse_lrc_content(raw_lrc: str) -> dict[str, Any]:
        """Parse timestamped `[mm:ss.xx]` lines into structured JSON for smooth UI auto-scrolling."""
        if not raw_lrc.strip():
            return {"synced": False, "lines": []}

        synced_lines: list[dict[str, Any]] = []
        plain_lines: list[dict[str, Any]] = []

        for raw_line in raw_lrc.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _LRC_LINE_RE.match(line)
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                text = match.group(3).strip()
                total_sec = round(minutes * 60.0 + seconds, 2)
                synced_lines.append({"time": total_sec, "text": text or "♪"})
            elif not line.startswith("["):
                plain_lines.append({"time": None, "text": line})

        if synced_lines:
            synced_lines.sort(key=lambda item: float(item["time"]))
            return {"synced": True, "lines": synced_lines}
        return {"synced": False, "lines": plain_lines}


lyrics_client = LyricsClient()