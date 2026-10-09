import time
from typing import Optional, Dict, Any, List
import yt_dlp

class YouTubeSearcher:
    """Searches YouTube for tracks and albums using yt-dlp."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'skip_download': True,
            'noplaylist': True,
            'default_search': 'ytsearch1',
        }

    def search_track(self, artist: str, title: str, retries: int = 2) -> Optional[Dict[str, Any]]:
        query_key = f"{artist} - {title}".lower().strip()
        if query_key in self._cache:
            return self._cache[query_key]

        search_query = f"ytsearch1:{artist} - {title}"

        for attempt in range(retries):
            try:
                with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                    info = ydl.extract_info(search_query, download=False)
                    if not info or 'entries' not in info or not info['entries']:
                        info = ydl.extract_info(f"ytsearch1:{artist} - {title} audio", download=False)

                    if info and 'entries' in info and info['entries']:
                        entry = info['entries'][0]
                        video_id = entry.get('id')
                        url = entry.get('url')
                        if not url and video_id:
                            url = f"https://www.youtube.com/watch?v={video_id}"

                        result = {
                            "url": url,
                            "video_id": video_id,
                            "title": entry.get("title", ""),
                            "duration": entry.get("duration", 0),
                            "thumbnail": entry.get("thumbnail", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "")
                        }
                        self._cache[query_key] = result
                        return result
            except Exception as e:
                if attempt == retries - 1:
                    print(f"  [YouTube Error] Could not search '{artist} - {title}': {e}")
                time.sleep(1.0)

        return None

    def search_multiple(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search YouTube for individual tracks or albums and return top candidates."""
        opts = dict(self._ydl_opts)
        opts['default_search'] = f'ytsearch{limit}'

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                results = []
                for entry in info.get('entries', []):
                    vid_id = entry.get('id')
                    url = entry.get('url') or f"https://www.youtube.com/watch?v={vid_id}"
                    results.append({
                        "video_id": vid_id,
                        "title": entry.get("title", ""),
                        "url": url,
                        "duration": entry.get("duration", 0),
                        "thumbnail": entry.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg" if vid_id else ""),
                        "channel": entry.get("uploader") or entry.get("channel", "")
                    })
                return results
        except Exception as e:
            print(f"[YouTube Search Error] {query}: {e}")
            return []
