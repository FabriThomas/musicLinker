import os
import re
import urllib.request
import urllib.parse
import json
from typing import Dict, List, Any, Optional

USER_AGENT = "MusicLinker/2.0 ( https://github.com/ ; musiclinker@local )"

def parse_lrc_content(lrc_text: str) -> List[Dict[str, Any]]:
    """Parse LRC timestamped text into [{ 'time': seconds, 'text': line }]."""
    lines: List[Dict[str, Any]] = []
    pattern = r'\[(\d{2}):(\d{2}(?:\.\d{1,3})?)\](.*)'

    for raw_line in lrc_text.splitlines():
        match = re.match(pattern, raw_line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            total_seconds = round(minutes * 60 + seconds, 2)
            text = match.group(3).strip()
            if text:
                lines.append({"time": total_seconds, "text": text})

    lines.sort(key=lambda x: x["time"])
    return lines

def fetch_synced_lyrics(
    artist: str,
    title: str,
    album: str = "",
    duration: int = 0,
    save_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch synced lyrics from local .lrc file or LRCLIB API.
    Saves to save_path (.lrc) for offline playback if provided.
    """
    # 1. Check if local .lrc already exists
    if save_path and os.path.exists(save_path):
        try:
            with open(save_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                parsed = parse_lrc_content(content)
                if parsed:
                    return {
                        "synced": True,
                        "lines": parsed,
                        "raw": content,
                        "source": "local"
                    }
        except Exception:
            pass

    # 2. Query LRCLIB API
    params = {
        "artist_name": artist.strip(),
        "track_name": title.strip()
    }
    if album and album != "Singles":
        params["album_name"] = album.strip()
    if duration > 0:
        params["duration"] = str(duration)

    url = f"https://lrclib.net/api/get?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            synced_lrc = data.get("syncedLyrics")
            plain_lyrics = data.get("plainLyrics")

            if synced_lrc:
                parsed = parse_lrc_content(synced_lrc)
                # Save locally for offline use
                if save_path:
                    try:
                        with open(save_path, "w", encoding="utf-8") as f:
                            f.write(synced_lrc)
                    except Exception:
                        pass

                return {
                    "synced": True,
                    "lines": parsed,
                    "raw": synced_lrc,
                    "source": "lrclib"
                }

            if plain_lyrics:
                lines = [{"time": 0, "text": line} for line in plain_lyrics.splitlines() if line.strip()]
                return {
                    "synced": False,
                    "lines": lines,
                    "raw": plain_lyrics,
                    "source": "lrclib"
                }
    except Exception:
        # Fallback to general search on LRCLIB
        try:
            query = f"{artist} {title}"
            search_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
            s_req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(s_req, timeout=5) as s_resp:
                s_data = json.loads(s_resp.read().decode("utf-8"))
                if isinstance(s_data, list) and s_data:
                    first = s_data[0]
                    if first.get("syncedLyrics"):
                        parsed = parse_lrc_content(first["syncedLyrics"])
                        if save_path:
                            try:
                                with open(save_path, "w", encoding="utf-8") as f:
                                    f.write(first["syncedLyrics"])
                            except Exception:
                                pass
                        return {
                            "synced": True,
                            "lines": parsed,
                            "raw": first["syncedLyrics"],
                            "source": "lrclib_search"
                        }
        except Exception:
            pass

    return {
        "synced": False,
        "lines": [],
        "raw": "",
        "source": "none"
    }
