"""Keyless yt-dlp search engine, playlist/album expander, FFmpeg MP3 downloader, and VTT subtitle extractor."""

from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp

from musiclinker.core.exceptions import YouTubeResolutionError
from musiclinker.core.logging_config import logger

_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class YouTubeClient:
    """Provides cached flat-search queries, playlist/album expansion, MP3 extraction, and VTT subtitle extraction."""

    def __init__(self) -> None:
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            logger.warning("imageio_ffmpeg lookup failed (%s); falling back to system ffmpeg", exc)
            self.ffmpeg_exe = "ffmpeg"

    def search_videos(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
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

    def expand_playlist_or_album_url(self, playlist_url: str, default_artist: str = "") -> list[dict[str, Any]]:
        """Extract all tracks in order (`1..N`) from a YouTube Album or Playlist URL."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": False,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
        except Exception as exc:
            raise YouTubeResolutionError(f"Failed expanding playlist/album URL '{playlist_url}': {exc}") from exc

        entries = info.get("entries", []) if isinstance(info, dict) else []
        tracks: list[dict[str, Any]] = []
        for idx, entry in enumerate(entries, start=1):
            if not entry:
                continue
            vid_id = entry.get("id", "")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
            raw_title = str(entry.get("title") or f"Track {idx}").strip()
            artist = default_artist or str(entry.get("uploader") or "Unknown Artist").replace(" - Topic", "").strip()
            if " - " in raw_title and not default_artist:
                artist, raw_title = raw_title.split(" - ", 1)
            tracks.append(
                {
                    "track_number": idx,
                    "title": raw_title.strip(),
                    "artist": artist.strip(),
                    "duration": int(entry.get("duration") or 0),
                    "youtube_url": url,
                }
            )
        return tracks

    def resolve_best_audio_match(self, artist: str, title: str) -> dict[str, Any]:
        """
        Resolve the best official studio audio release for a track:
          1. Queries YouTube Music directly (`https://music.youtube.com/search?q=...`) to prioritize
             official catalog studio tracks and filter out music video skits / sound effects.
          2. Falls back to YouTube with Topic / Official Audio ranking if YouTube Music yields no direct stream.
        """
        import urllib.parse

        # 1. Try YouTube Music search first
        clean_q = f"{artist} {title}".strip()
        encoded_q = urllib.parse.quote_plus(clean_q)
        ytm_search_url = f"https://music.youtube.com/search?q={encoded_q}"
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(ytm_search_url, download=False)
            entries = info.get("entries", []) if isinstance(info, dict) else []
            for e in entries:
                if not e:
                    continue
                url = str(e.get("url") or "")
                vid_id = str(e.get("id") or "")
                if "watch?v=" in url or vid_id:
                    final_url = url if "http" in url else f"https://music.youtube.com/watch?v={vid_id}"
                    dur = int(e.get("duration") or 0)
                    if dur == 0 or (30 <= dur <= 1200):
                        thumbnails = e.get("thumbnails") or []
                        thumb_url = thumbnails[-1].get("url", "") if thumbnails else e.get("thumbnail", "")
                        return {
                            "id": vid_id or final_url.split("v=")[-1].split("&")[0],
                            "title": e.get("title") or f"{artist} - {title}",
                            "uploader": e.get("uploader") or e.get("channel") or f"{artist} - Topic",
                            "duration": dur,
                            "url": final_url,
                            "thumbnail": thumb_url,
                            "source": "youtube_music",
                        }
        except Exception as exc:
            logger.debug(
                "YouTube Music direct search failed for '%s - %s' (%s); falling back to ytsearch",
                artist,
                title,
                exc,
            )

        # 2. Fallback: Search YouTube with Topic & Official Audio priority
        query = f"{artist} - {title} Topic"
        matches = self.search_videos(query, max_results=5)
        if not matches:
            matches = self.search_videos(f"{artist} - {title} official audio", max_results=5)

        if not matches:
            raise YouTubeResolutionError(f"No YouTube audio match found for '{artist} - {title}'")

        # Score candidates to prefer Topic channels and official audio
        def score_candidate(cand: dict[str, Any]) -> int:
            score = 0
            uploader = str(cand.get("uploader") or "").lower()
            cand_title = str(cand.get("title") or "").lower()
            dur = cand.get("duration", 0)

            if "topic" in uploader:
                score += 50
            if "vevo" in uploader or "official" in uploader:
                score += 20
            if "official audio" in cand_title:
                score += 30
            elif "audio" in cand_title:
                score += 15

            # Penalize likely live recordings or fan edits
            if "live" in cand_title and "live" not in title.lower():
                score -= 30
            if "remix" in cand_title and "remix" not in title.lower():
                score -= 20
            if "cover" in cand_title and "cover" not in title.lower():
                score -= 40
            if 40 <= dur <= 900:
                score += 10
            return score

        best = max(matches, key=score_candidate)
        return best


    def download_mp3_to_staging(
        self,
        url_or_query: str,
        staging_dir: Path,
        stem_name: str,
        bitrate_kbps: int = 320,
        progress_cb: Callable[[float], None] | None = None,
    ) -> tuple[Path, dict[str, Any], str]:
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
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "pt.*", "es.*", "orig"],
            "subtitlesformat": "vtt",
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
            candidates = list(staging_dir.glob(f"{stem_name}*.mp3"))
            if not candidates:
                raise YouTubeResolutionError("FFmpeg post-processing did not produce an MP3 file.")
            mp3_path = candidates[0]

        yt_lrc_fallback = ""
        vtt_files = list(staging_dir.glob(f"{stem_name}*.vtt"))
        if vtt_files:
            try:
                raw_vtt = vtt_files[0].read_text(encoding="utf-8", errors="replace")
                yt_lrc_fallback = self.convert_vtt_to_lrc(raw_vtt)
            except Exception as exc:
                logger.debug("VTT caption conversion skipped: %s", exc)

        return mp3_path, (info if isinstance(info, dict) else {}), yt_lrc_fallback

    @staticmethod
    def convert_vtt_to_lrc(raw_vtt: str) -> str:
        lrc_lines: list[str] = []
        last_text = ""
        lines = raw_vtt.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = _VTT_TIMESTAMP_RE.search(line)
            if match:
                hours = int(match.group(1).rstrip(":")) if match.group(1) else 0
                minutes = int(match.group(2)) + hours * 60
                seconds = int(match.group(3))
                millis = int(match.group(4))
                hundredths = millis // 10
                i += 1
                cue_parts: list[str] = []
                while i < len(lines) and lines[i].strip():
                    cleaned = _HTML_TAG_RE.sub("", lines[i]).strip()
                    if cleaned and not cleaned.startswith("WEBVTT"):
                        cue_parts.append(cleaned)
                    i += 1
                text = " ".join(cue_parts).strip()
                if text and text.lower() != last_text.lower():
                    lrc_lines.append(f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}] {text}")
                    last_text = text
            i += 1
        return "\n".join(lrc_lines)


youtube_client = YouTubeClient()