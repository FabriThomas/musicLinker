import os
import re
from typing import Optional, Dict, Any, Callable, List
import yt_dlp
import imageio_ffmpeg

from tagger import parse_multi_artists, fetch_musicbrainz_release_info, tag_mp3_file
from hasher import compute_track_hash
import db

def sanitize_filename(name: str) -> str:
    """Sanitize directory or file names for Windows and Android filesystems."""
    if not name:
        return "Unknown"
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    return cleaned or "Unknown"

class TrackDownloader:
    """Downloads audio via yt-dlp, converts to MP3, embeds ID3 tags, and registers in database."""

    def __init__(self, base_output_dir: str = "downloads", audio_quality: str = "192"):
        self.base_output_dir = os.path.abspath(base_output_dir)
        self.audio_quality = audio_quality
        os.makedirs(self.base_output_dir, exist_ok=True)
        try:
            self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            self.ffmpeg_path = None

    def download_and_tag(
        self,
        youtube_url: str,
        artist: str,
        title: str,
        album: str = "",
        cover_url: str = "",
        plays: int = 0,
        genre: str = "",
        year: Optional[int] = None,
        progress_cb: Optional[Callable[[str], None]] = None
    ) -> Optional[str]:
        # 1. Parse collaborating and multi-artists
        artist_info = parse_multi_artists(artist, title)
        primary_artist = artist_info["primary_artist"]
        all_artists = artist_info["all_artists"]
        clean_title = artist_info["title"]

        # 2. Lookup release info from MusicBrainz if missing year/album
        if not year or not album:
            mb_info = fetch_musicbrainz_release_info(primary_artist, clean_title)
            if not album and mb_info.get("album"):
                album = mb_info["album"]
            if not year and mb_info.get("year"):
                year = mb_info["year"]
            orig_year = mb_info.get("original_year")
        else:
            orig_year = None

        album = album or "Singles"

        # 3. Prepare target folder and filename
        safe_artist = sanitize_filename(primary_artist)
        safe_album = sanitize_filename(album)
        safe_title = sanitize_filename(clean_title)

        album_dir = os.path.join(self.base_output_dir, safe_artist, safe_album)
        os.makedirs(album_dir, exist_ok=True)

        target_mp3 = os.path.join(album_dir, f"{safe_artist} - {safe_title}.mp3")
        track_hash = compute_track_hash(primary_artist, clean_title)

        # Skip download if already exists on disk
        if os.path.exists(target_mp3) and os.path.getsize(target_mp3) > 10000:
            if progress_cb:
                progress_cb(f"Already downloaded: {safe_artist} - {safe_title}")
            # Ensure it is in the database
            db.add_or_update_track({
                "hash": track_hash,
                "title": clean_title,
                "primary_artist": primary_artist,
                "all_artists": all_artists,
                "album": album,
                "year": year,
                "original_year": orig_year,
                "genre": genre,
                "plays": plays,
                "filepath": target_mp3,
                "youtube_url": youtube_url,
                "cover_url": cover_url
            })
            return target_mp3

        if progress_cb:
            progress_cb(f"Downloading: {primary_artist} - {clean_title}...")

        temp_base = os.path.join(album_dir, f"temp_{abs(hash(youtube_url)) % 10000000}")
        out_tmpl = f"{temp_base}.%(ext)s"

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_tmpl,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': str(self.audio_quality),
            }],
        }

        if self.ffmpeg_path:
            ydl_opts['ffmpeg_location'] = self.ffmpeg_path

        temp_mp3 = f"{temp_base}.mp3"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])

            if not os.path.exists(temp_mp3):
                possible = [f for f in os.listdir(album_dir) if f.startswith(os.path.basename(temp_base)) and f.endswith(".mp3")]
                if possible:
                    temp_mp3 = os.path.join(album_dir, possible[0])
                else:
                    return None

            # 4. Tag MP3 file
            tag_mp3_file(
                filepath=temp_mp3,
                title=clean_title,
                primary_artist=primary_artist,
                all_artists=all_artists,
                album=album,
                year=year,
                original_year=orig_year,
                genre=genre,
                plays=plays,
                cover_url=cover_url
            )

            # 5. Move to final destination
            if os.path.exists(target_mp3):
                os.remove(target_mp3)
            os.rename(temp_mp3, target_mp3)

            # 6. Index in database
            db.add_or_update_track({
                "hash": track_hash,
                "title": clean_title,
                "primary_artist": primary_artist,
                "all_artists": all_artists,
                "album": album,
                "year": year,
                "original_year": orig_year,
                "genre": genre,
                "plays": plays,
                "filepath": target_mp3,
                "youtube_url": youtube_url,
                "cover_url": cover_url
            })

            if progress_cb:
                progress_cb(f"Saved & Tagged: {safe_artist} - {safe_title}.mp3")

            return target_mp3

        except Exception as e:
            if progress_cb:
                progress_cb(f"Download failed for {primary_artist} - {clean_title}: {e}")
            if os.path.exists(temp_mp3):
                try:
                    os.remove(temp_mp3)
                except Exception:
                    pass
            return None
