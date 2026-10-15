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
from musiclinker.core.paths import PATHS

_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


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

    def _get_base_ydl_opts(self) -> dict[str, Any]:
        """Provides resilient default yt-dlp arguments including anti-bot clients and optional cookies."""
        opts: dict[str, Any] = {
            "extractor_args": {"youtube": {"player_client": ["mweb", "android", "ios"]}},
        }
        for cand in (PATHS.app_data_dir / "cookies.txt", PATHS.root_dir / "cookies.txt"):
            if cand.exists():
                opts["cookiefile"] = str(cand)
                break
        return opts

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
            **self._get_base_ydl_opts(),
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

    def _normalize_playlist_url(self, playlist_url: str) -> str:
        """Resolve browse IDs or ensure proper playlist endpoints for YouTube Music and YouTube."""
        clean = playlist_url.strip()
        if "music.youtube.com/browse/MPREb_" in clean:
            try:
                import urllib.request
                req = urllib.request.Request(clean, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                match = re.search(r"OLAK5uy_[a-zA-Z0-9_-]+", html)
                if match:
                    return f"https://music.youtube.com/playlist?list={match.group(0)}"
            except Exception as exc:
                logger.debug("Failed resolving MPREb browse ID (%s); proceeding with original URL", exc)

        # Do not convert YouTube Music album playlists (OLAK5uy_) to www.youtube.com
        if "OLAK5uy_" in clean:
            return clean

        return clean.replace("music.youtube.com", "www.youtube.com")

    def expand_playlist_or_album_url(self, playlist_url: str, default_artist: str = "") -> list[dict[str, Any]]:
        """Extract all tracks in order (`1..N`) from a YouTube Album or Playlist URL, or full-album timestamped video."""
        album_data = self.lookup_youtube_album(playlist_url, default_artist=default_artist)
        return album_data.get("tracks", [])

    def lookup_youtube_album(self, playlist_url: str, default_artist: str = "") -> dict[str, Any]:
        """
        Extract complete album metadata (title, artist, cover art, tracklist) from a YouTube
        Album/Playlist URL or a single Full Album video with chapter markers/description timestamps.
        """
        clean_url = self._normalize_playlist_url(playlist_url)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "noplaylist": False,
            **self._get_base_ydl_opts(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
        except Exception as exc:
            logger.warning("Failed expanding playlist/album URL '%s': %s", playlist_url, exc)
            return {"artist": default_artist or "Unknown Artist", "album": "Downloaded Album", "cover_url": "", "source": "url", "tracks": []}

        if not info:
            return {"artist": default_artist or "Unknown Artist", "album": "Downloaded Album", "cover_url": "", "source": "url", "tracks": []}

        # Resolve album cover
        thumbnails = info.get("thumbnails") or []
        cover_url = thumbnails[-1].get("url", "") if thumbnails else info.get("thumbnail", "")

        # Resolve album title & artist
        from musiclinker.infrastructure.hasher import clean_album_name
        raw_album_title = str(info.get("title") or "Downloaded Album").strip()
        if raw_album_title.lower().startswith("album - "):
            raw_album_title = raw_album_title[8:].strip()
        album_title = clean_album_name(raw_album_title)

        album_artist = default_artist or str(info.get("uploader") or info.get("channel") or "Unknown Artist").replace(" - Topic", "").strip()

        entries = info.get("entries", [])
        if not entries and isinstance(info, dict):
            entries = [info]

        tracks: list[dict[str, Any]] = []

        # If entries is a single video, check for chapter markers or description timestamps
        if len(entries) == 1 and entries[0]:
            single_entry = entries[0]
            chapters = single_entry.get("chapters") or []
            description = str(single_entry.get("description") or "")
            vid_id = str(single_entry.get("id") or "")
            vid_url = single_entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}" if vid_id else clean_url

            # 1. Video chapters
            if chapters:
                for c_idx, ch in enumerate(chapters, start=1):
                    ch_title = str(ch.get("title") or f"Track {c_idx}").strip()
                    ch_title = re.sub(r"^\d+\s*[\.\-\)]\s*", "", ch_title).strip()
                    ch_start = int(ch.get("start_time") or 0)
                    ch_end = int(ch.get("end_time") or ch_start)
                    ch_dur = max(0, ch_end - ch_start)
                    range_suffix = f"&t={ch_start}s" + (f"&end={ch_end}s" if ch_end > ch_start else "")
                    tracks.append(
                        {
                            "track_number": c_idx,
                            "title": ch_title,
                            "artist": album_artist,
                            "duration": ch_dur,
                            "youtube_url": f"{vid_url}{range_suffix}" if vid_id else vid_url,
                        }
                    )
            # 2. Timestamp lines in description (e.g. "01. Song 03:45" or "0:00 Song")
            elif description and re.search(r"\d{1,2}:\d{2}", description):
                time_re = re.compile(r"\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]?")
                parsed_stamps: list[tuple[int, str]] = []
                for line in description.splitlines():
                    m = time_re.search(line)
                    if m:
                        t_clean = time_re.sub("", line).strip(" -:|.#")
                        t_clean = re.sub(r"^\d+\s*[\.\-\)]\s*", "", t_clean).strip()
                        if t_clean and len(t_clean) >= 2:
                            parts = [int(p) for p in m.group(0).strip("[]").split(":")]
                            sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
                            parsed_stamps.append((sec, t_clean))

                if len(parsed_stamps) >= 2:
                    total_dur = int(single_entry.get("duration") or 0)
                    for s_idx, (start_sec, tr_name) in enumerate(parsed_stamps, start=1):
                        next_sec = parsed_stamps[s_idx][0] if s_idx < len(parsed_stamps) else total_dur
                        dur = max(0, next_sec - start_sec)
                        range_suffix = f"&t={start_sec}s" + (f"&end={next_sec}s" if next_sec > start_sec else "")
                        tracks.append(
                            {
                                "track_number": s_idx,
                                "title": tr_name,
                                "artist": album_artist,
                                "duration": dur,
                                "youtube_url": f"{vid_url}{range_suffix}" if vid_id else vid_url,
                            }
                        )

        # Standard playlist entries
        if not tracks:
            for idx, entry in enumerate(entries, start=1):
                if not entry:
                    continue
                vid_id = str(entry.get("id") or "")
                url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id and _VIDEO_ID_RE.match(vid_id) else "")
                if url and "music.youtube.com" in url:
                    url = url.replace("music.youtube.com", "www.youtube.com")
                raw_title = str(entry.get("title") or f"Track {idx}").strip()
                artist = default_artist or str(entry.get("uploader") or entry.get("channel") or album_artist).replace(" - Topic", "").strip()
                if " - " in raw_title and not default_artist:
                    artist, raw_title = raw_title.split(" - ", 1)
                raw_title = re.sub(r"^\d+\s*[\.\-\)]\s*", "", raw_title).strip()
                tracks.append(
                    {
                        "track_number": idx,
                        "title": raw_title.strip(),
                        "artist": artist.strip(),
                        "duration": int(entry.get("duration") or 0),
                        "youtube_url": url,
                    }
                )

        is_official_album = "OLAK5uy_" in clean_url or str(info.get("title") or "").lower().startswith("album - ")
        distinct_artists = {tr["artist"].lower() for tr in tracks if tr.get("artist")}
        entity_type = "album" if is_official_album or len(distinct_artists) <= 1 else "playlist"
        if entity_type == "playlist" and not default_artist and len(distinct_artists) > 2:
            album_artist = "Various Artists"

        return {
            "artist": album_artist,
            "album": album_title,
            "entity_type": entity_type,
            "release_year": str(info.get("release_year") or info.get("upload_date", "")[:4]),
            "cover_url": cover_url,
            "source": "youtube_playlist",
            "tracks": tracks,
        }

    def preview_track_or_link(self, query_or_url: str, max_results: int = 6) -> list[dict[str, Any]]:
        """Resolve either a direct YouTube/YouTube Music track URL or a search query into previewable track items."""
        clean = query_or_url.strip()
        if not clean:
            return []
        raw_results = self.search_videos(clean, max_results=1 if clean.startswith(("http://", "https://")) else max_results)
        from musiclinker.infrastructure.hasher import clean_track_title
        enriched: list[dict[str, Any]] = []
        for item in raw_results:
            raw_title = str(item.get("title") or "Unknown Title").strip()
            uploader = str(item.get("uploader") or "YouTube").replace(" - Topic", "").strip()
            if " - " in raw_title:
                inf_artist, inf_title = raw_title.split(" - ", 1)
            else:
                inf_artist, inf_title = uploader, raw_title
            enriched.append(
                {
                    **item,
                    "inferred_artist": inf_artist.strip(),
                    "inferred_title": clean_track_title(inf_title.strip()),
                    "is_topic": "topic" in str(item.get("uploader") or "").lower(),
                }
            )
        return enriched

    def resolve_best_audio_match(
        self,
        artist: str,
        title: str,
        preferred_method: str = "auto",
    ) -> dict[str, Any]:
        """
        Resolve the best official studio audio release for a track based on preferred_method:
          - 'auto': Queries YouTube Music official catalog first to avoid video skits;
            smoothly falls back to YouTube Topic / Official Audio ranking.
          - 'youtube_music': Strictly prioritizes YouTube Music studio catalog & Topic audio.
          - 'youtube_video': Searches standard YouTube prioritizing Official Music Videos / Official Audio.
          - 'spotify_match': Uses studio catalog matching (prioritizes official release streams).
        """
        import urllib.parse

        # 1. Try YouTube Music search first (unless user explicitly requests standard YouTube video)
        if preferred_method != "youtube_video":
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
                    **self._get_base_ydl_opts(),
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(ytm_search_url, download=False)
                entries = info.get("entries", []) if isinstance(info, dict) else []
                for e in entries:
                    if not e:
                        continue
                    url = str(e.get("url") or "")
                    vid_id = str(e.get("id") or "")
                    if not _VIDEO_ID_RE.match(vid_id):
                        if "v=" in url:
                            candidate_v = url.split("v=")[-1].split("&")[0]
                            if _VIDEO_ID_RE.match(candidate_v):
                                vid_id = candidate_v
                            else:
                                continue
                        else:
                            continue
                    final_url = f"https://www.youtube.com/watch?v={vid_id}"
                    dur = int(e.get("duration") or 0)
                    if dur == 0 or (30 <= dur <= 1200):
                        thumbnails = e.get("thumbnails") or []
                        thumb_url = thumbnails[-1].get("url", "") if thumbnails else e.get("thumbnail", "")
                        return {
                            "id": vid_id,
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

        # 2. Search YouTube with mode-appropriate query & scoring
        if preferred_method == "youtube_video":
            query = f"{artist} - {title} official video"
        else:
            query = f"{artist} - {title} Topic"
        matches = self.search_videos(query, max_results=5)
        if not matches:
            matches = self.search_videos(f"{artist} - {title} official audio", max_results=5)

        if not matches:
            raise YouTubeResolutionError(f"No YouTube audio match found for '{artist} - {title}'")

        def score_candidate(cand: dict[str, Any]) -> int:
            score = 0
            uploader = str(cand.get("uploader") or "").lower()
            cand_title = str(cand.get("title") or "").lower()
            dur = cand.get("duration", 0)

            if preferred_method == "youtube_video":
                if "official music video" in cand_title or "official video" in cand_title:
                    score += 45
                if "vevo" in uploader or "official" in uploader:
                    score += 30
                if "topic" in uploader:
                    score += 15
            else:
                if "topic" in uploader:
                    score += 65 if preferred_method == "youtube_music" else 50
                if "vevo" in uploader or "official" in uploader:
                    score += 20
                if "official audio" in cand_title:
                    score += 35
                elif "audio" in cand_title:
                    score += 15

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

    @staticmethod
    def _parse_timestamp_slice(url_or_query: str) -> tuple[str, float | None, float | None]:
        """Extract &t=<start>s and &end=<end>s from a URL and return (clean_url, start_sec, end_sec)."""
        clean_expr = url_or_query.replace("music.youtube.com", "www.youtube.com")
        start_sec: float | None = None
        end_sec: float | None = None

        if clean_expr.startswith(("http://", "https://")):
            t_match = re.search(r"[?&]t=(\d+(?:\.\d+)?)s?", clean_expr)
            end_match = re.search(r"[?&]end=(\d+(?:\.\d+)?)s?", clean_expr)
            if t_match:
                start_sec = float(t_match.group(1))
                clean_expr = re.sub(r"([?&])t=\d+(?:\.\d+)?s?&?", r"\1", clean_expr).rstrip("?&")
            if end_match:
                end_sec = float(end_match.group(1))
                clean_expr = re.sub(r"([?&])end=\d+(?:\.\d+)?s?&?", r"\1", clean_expr).rstrip("?&")
            clean_expr = clean_expr.replace("?&", "?")
        return clean_expr, start_sec, end_sec

    def preview_track_or_link(self, query_or_url: str, max_results: int = 6) -> list[dict[str, Any]]:
        """Preview a single YouTube/YTM video link or a search query for the Single Track / Link Downloader."""
        cleaned = query_or_url.strip()
        if not cleaned:
            return []

        if cleaned.startswith(("http://", "https://")):
            clean_url, start_s, end_s = self._parse_timestamp_slice(cleaned)
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "noplaylist": True,
                **self._get_base_ydl_opts(),
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(clean_url, download=False)
                if isinstance(info, dict):
                    entries = info.get("entries", [info]) if "entries" in info else [info]
                    results: list[dict[str, Any]] = []
                    for entry in entries[:max_results]:
                        if not entry:
                            continue
                        vid_id = entry.get("id", "")
                        url = entry.get("webpage_url") or entry.get("url") or cleaned
                        if vid_id and not str(url).startswith("http"):
                            url = f"https://www.youtube.com/watch?v={vid_id}"
                        thumbnails = entry.get("thumbnails") or []
                        thumb_url = thumbnails[-1].get("url", "") if thumbnails else entry.get("thumbnail", "")
                        dur = int(entry.get("duration") or 0)
                        if start_s is not None and end_s is not None and end_s > start_s:
                            dur = int(end_s - start_s)
                        raw_title = str(entry.get("title") or "YouTube Audio")
                        uploader = str(entry.get("uploader") or entry.get("channel") or "YouTube").replace(" - Topic", "")
                        results.append(
                            {
                                "id": vid_id or "yt_direct",
                                "title": raw_title,
                                "uploader": uploader,
                                "duration": dur,
                                "url": cleaned,
                                "thumbnail": thumb_url,
                                "source": "youtube_music" if "music.youtube.com" in cleaned else "youtube",
                            }
                        )
                    if results:
                        return results
            except Exception as exc:
                logger.warning("Direct YouTube link preview failed for '%s': %s", cleaned, exc)

        matches = self.search_videos(cleaned, max_results=max_results)
        for m in matches:
            m.setdefault("source", "youtube")
        return matches

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
        if bitrate_kbps >= 300:
            valid_bitrate = "320"
        elif bitrate_kbps >= 224:
            valid_bitrate = "256"
        else:
            valid_bitrate = "192"

        def _hook(status_dict: dict[str, Any]) -> None:
            if progress_cb and status_dict.get("status") == "downloading":
                total = status_dict.get("total_bytes") or status_dict.get("total_bytes_estimate") or 0
                downloaded = status_dict.get("downloaded_bytes") or 0
                if total > 0:
                    progress_cb(min(100.0, round((downloaded / total) * 100.0, 1)))

        clean_expr, start_sec, end_sec = self._parse_timestamp_slice(url_or_query)

        ydl_opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "ffmpeg_location": self.ffmpeg_exe,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            **self._get_base_ydl_opts(),
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

        if start_sec is not None:
            ff_args = ["-ss", str(start_sec)]
            if end_sec is not None and end_sec > start_sec:
                ff_args.extend(["-to", str(end_sec)])
            ydl_opts["postprocessor_args"] = {"FFmpegExtractAudio": ff_args}
            if hasattr(yt_dlp.utils, "download_range_func"):
                ydl_opts["download_ranges"] = yt_dlp.utils.download_range_func(
                    None, [(start_sec, end_sec if (end_sec and end_sec > start_sec) else float("inf"))]
                )

        target_expr = clean_expr if clean_expr.startswith(("http://", "https://")) else f"ytsearch1:{clean_expr}"
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