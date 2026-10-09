import os
from typing import Dict, Any, List
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from hasher import compute_track_hash
import db

def scan_library(directory: str = "downloads") -> Dict[str, int]:
    """
    Scan audio files in directory, parse ID3 tags, and sync into database.
    """
    base_dir = os.path.abspath(directory)
    if not os.path.exists(base_dir):
        os.makedirs(base_dir, exist_ok=True)
        return {"scanned": 0, "added": 0}

    scanned = 0
    added = 0

    for root, _, files in os.walk(base_dir):
        for f in files:
            if f.lower().endswith(".mp3"):
                scanned += 1
                full_path = os.path.join(root, f)
                try:
                    audio_tags = EasyID3(full_path)
                    mp3_info = MP3(full_path)
                    duration = int(mp3_info.info.length) if mp3_info.info else 0

                    title = audio_tags.get("title", [os.path.splitext(f)[0]])[0]
                    artist_list = audio_tags.get("artist", ["Unknown Artist"])
                    primary_artist = audio_tags.get("albumartist", artist_list)[0]
                    album = audio_tags.get("album", ["Singles"])[0]
                    genre = audio_tags.get("genre", [""])[0]
                    year_val = None
                    date_tag = audio_tags.get("date", [""])[0]
                    if date_tag and len(date_tag) >= 4:
                        try:
                            year_val = int(date_tag[:4])
                        except ValueError:
                            pass

                    track_hash = compute_track_hash(primary_artist, title)

                    db.add_or_update_track({
                        "hash": track_hash,
                        "title": title,
                        "primary_artist": primary_artist,
                        "all_artists": artist_list,
                        "album": album,
                        "year": year_val,
                        "genre": genre,
                        "duration": duration,
                        "filepath": full_path
                    })
                    added += 1
                except Exception as e:
                    print(f"[Library Scanner Error] {full_path}: {e}")

    return {"scanned": scanned, "added": added}
