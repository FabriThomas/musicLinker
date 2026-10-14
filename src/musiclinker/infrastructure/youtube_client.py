"""Keyless yt-dlp search engine and FFmpeg audio stream downloader with atomic staging."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp

from musiclinker.core.exceptions import YouTubeResolutionError
from musiclinker.core.logging_config import logger


class YouTubeClient:
    """Provides cached flat-search queries and high-bitrate MP3 extraction using bundled FFmpeg."""

    def __init__(self) -> None:
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            logger.warning("imageio_ffmpeg binary lookup failed (%s); falling back to system ffmpeg", exc)
            self.ffmpeg_exe = "ffmpeg"

    def search_videos(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Execute a fast keyless yt-dlp search and cache results in memory."""
        cache_key = f"{max_results}:{query.strip().lower()}"
        with self._cache_lock:
            if cache_key in self._search_cache:
                return self._search_cache[cache_key]

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": True,
        }
        search_expr = query if query.startswith(("http://", "https://")) else f"ytsearch{max_results}:{query}"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_expr, download=False)
        except Exception as exc:
            raise YouTubeResolutionError(f"YouTube search failed for '{query}': {exc}") from exc

        entries = info.get("entries", [info]) if isinstance(info, dict) else []
        results: list[dict[str, Any]] = []
        for entry in entries:
            if not entry:
                continue
            vid_id = entry.get("id", "")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
            if not url:
                continue
            thumbnails = entry.get("thumbnails") or []
            thumb_url = thumbnails[-1].get("url", "") if thumbnails else entry.get("thumbnail", "")
            results.append(
                {
                    "id": vid_id,
                    "title": entry.get("title", "Unknown Title"),
                    "uploader": entry.get("uploader") or entry.get("channel") or "YouTube",
                    "duration": int(entry.get("duration") or 0),
                    "url": url,
                    "thumbnail": thumb_url,
                }
            )

        with self._cache_lock:
            self._search_cache[cache_key] = results
        return results

    def resolve_best_audio_match(self, artist: str, title: str) -> dict[str, Any]:
        """Find the cleanest official audio stream for an artist and track title."""
        query = f"{artist} - {title} official audio"
        matches = self.search_videos(query, max_results=3)
        if not matches:
            raise YouTubeResolutionError(f"No YouTube results found for '{artist} - {title}'")
        # Prefer tracks between 45 seconds and 15 minutes to avoid 1-hour loops or shorts
        for candidate in matches:
            dur = candidate.get("duration", 0)
            if dur == 0 or (45 <= dur <= 900):
                return candidate
        return matches[0]

    def download_mp3_to_staging(
        self,
        url_or_query: str,
        staging_dir: Path,
        stem_name: str,
        bitrate_kbps: int = 320,
        progress_cb: Callable[[float], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Download audio stream to staging directory and transcode to MP3 at 192k or 320k."""
        staging_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(staging_dir / f"{stem_name}.%(ext)s")
        valid_bitrate = "320" if bitrate_kbps >= 256 else "192"

        def _hook(status_dict: dict[str, Any]) -> None:
            if progress_cb and status_dict.get("status") == "downloading":
                total = status_dict.get("total_bytes") or status_dict.get("total_bytes_estimate") or 0
                downloaded = status_dict.get("downloaded_bytes") or 0
                if total > 0:
                    progress_cb(min(100.0, round((downloaded / total) * 100.0, 1)))

        ydl_opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "ffmpeg_location": self.ffmpeg_exe,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_hook],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": valid_bitrate,
                }
            ],
        }

        target_expr = url_or_query if url_or_query.startswith(("http://", "https://")) else f"ytsearch1:{url_or_query}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_expr, download=True)
                if isinstance(info, dict) and "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    info = entries[0] if entries else {}
        except Exception as exc:
            raise YouTubeResolutionError(f"Failed downloading audio from '{url_or_query}': {exc}") from exc

        mp3_path = staging_dir / f"{stem_name}.mp3"
        if not mp3_path.exists():
            # Fallback scan in staging_dir
            candidates = list(staging_dir.glob(f"{stem_name}*.mp3"))
            if not candidates:
                raise YouTubeResolutionError("FFmpeg post-processing did not produce an MP3 file.")
            mp3_path = candidates[0]

        return mp3_path, (info if isinstance(info, dict) else {})


youtube_client = YouTubeClient()