"""3-Tier Synced & Rough Lyrics Resolver (LRCLIB Exact -> YouTube Auto-Captions -> Proportional Rough Interpolation)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import requests

from musiclinker.core.logging_config import logger

LRCLIB_API_URL = "https://lrclib.net/api"
_LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\](.*)")
_META_MODE_RE = re.compile(r"^\[x-ml-sync-mode:([a-z_]+)\]$", re.IGNORECASE)


class LyricsClient:
    """
    Resolves lyrics across three tiers so the user always sees where the song is at:
      1. `exact_lrc`: Official timestamped `.lrc` lines from LRCLIB.
      2. `youtube_captions`: Timestamped subtitle cues extracted from the downloaded YouTube stream.
      3. `rough_interpolated`: Plain lyrics automatically distributed across the vocal window (`8%`–`92%` of duration).
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MusicLinker/2.1 (https://github.com/FabriThomas/musicLink)"})

    def fetch_and_save_lrc(
        self,
        artist: str,
        title: str,
        album: str,
        duration: int,
        target_lrc_path: Path,
        youtube_lrc_fallback: str = "",
    ) -> tuple[str, str, str]:
        """
        Fetch lyrics, write `.lrc` file with embedded sync-mode header, and return `(lrc_text, sync_mode, summary_msg)`.
        """
        synced_text, plain_text = self.fetch_lyrics(artist, title, album, duration)
        sync_mode = "none"
        final_body = ""
        summary = "No lyrics found"

        if synced_text.strip():
            sync_mode = "exact_lrc"
            final_body = synced_text.strip()
            line_count = len([l for l in final_body.splitlines() if _LRC_LINE_RE.match(l.strip())])
            summary = f"Exact LRC synced lyrics ({line_count} timestamped lines via LRCLIB)"
        elif youtube_lrc_fallback.strip():
            sync_mode = "youtube_captions"
            final_body = youtube_lrc_fallback.strip()
            line_count = len(final_body.splitlines())
            summary = f"Rough synced captions from YouTube stream ({line_count} cues)"
        elif plain_text.strip():
            sync_mode = "rough_interpolated"
            final_body = self.interpolate_plain_lyrics_to_lrc(plain_text.strip(), duration)
            line_count = len(final_body.splitlines())
            summary = f"Rough interpolated sync ({line_count} plain lines mapped across {duration or 180}s)"

        if final_body:
            full_text = f"[x-ml-sync-mode:{sync_mode}]\n{final_body}\n"
            try:
                target_lrc_path.write_text(full_text, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed writing LRC file %s: %s", target_lrc_path, exc)
            return full_text, sync_mode, summary

        return "", "none", summary

    @staticmethod
    def interpolate_plain_lyrics_to_lrc(plain_text: str, duration: int) -> str:
        """Assign rough proportional `[mm:ss.xx]` timestamps to plain lyrics across 8%..92% of song duration."""
        raw_lines = [l.strip() for l in plain_text.splitlines() if l.strip()]
        if not raw_lines:
            return ""
        effective_dur = max(60, duration if duration > 0 else 200)
        start_sec = effective_dur * 0.08
        end_sec = effective_dur * 0.92
        span = max(10.0, end_sec - start_sec)
        step = span / max(1, len(raw_lines))

        out: list[str] = []
        for idx, text in enumerate(raw_lines):
            t = start_sec + idx * step
            mins = int(t // 60)
            secs = t % 60
            out.append(f"[{mins:02d}:{secs:05.2f}] {text}")
        return "\n".join(out)

    def fetch_lyrics(self, artist: str, title: str, album: str = "", duration: int = 0) -> tuple[str, str]:
        try:
            params: dict[str, Any] = {"artist_name": artist, "track_name": title}
            if album:
                params["album_name"] = album
            if duration > 0:
                params["duration"] = duration
            resp = self.session.get(f"{LRCLIB_API_URL}/get", params=params, timeout=8.0)
            if resp.status_code == 404:
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

    @classmethod
    def parse_lrc_content(cls, raw_lrc: str, fallback_duration: int = 200) -> dict[str, Any]:
        """
        Parse `.lrc` content into structured lines with `sync_mode` (`exact_lrc`, `youtube_captions`, `rough_interpolated`).
        If a file contains plain lines without timestamps, automatically interpolates rough timestamps.
        """
        if not raw_lrc.strip():
            return {"synced": False, "sync_mode": "none", "lines": []}

        detected_mode = "exact_lrc"
        synced_lines: list[dict[str, Any]] = []
        plain_lines: list[str] = []

        for raw_line in raw_lrc.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            mode_match = _META_MODE_RE.match(line)
            if mode_match:
                detected_mode = mode_match.group(1).lower()
                continue
            match = _LRC_LINE_RE.match(line)
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                text = match.group(3).strip()
                total_sec = round(minutes * 60.0 + seconds, 2)
                synced_lines.append({"time": total_sec, "text": text or "♪"})
            elif not line.startswith("["):
                plain_lines.append(line)

        if synced_lines:
            synced_lines.sort(key=lambda item: float(item["time"]))
            return {
                "synced": True,
                "sync_mode": detected_mode,
                "lines": synced_lines,
            }

        if plain_lines:
            interpolated_lrc = cls.interpolate_plain_lyrics_to_lrc("\n".join(plain_lines), fallback_duration)
            return cls.parse_lrc_content(f"[x-ml-sync-mode:rough_interpolated]\n{interpolated_lrc}", fallback_duration)

        return {"synced": False, "sync_mode": "none", "lines": []}


lyrics_client = LyricsClient()