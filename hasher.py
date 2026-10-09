import os
import re
import json
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Set, List

def clean_text(text: str) -> str:
    """Normalize text for consistent duplicate detection."""
    if not text:
        return ""
    text = text.lower().strip()
    # Normalize unicode quotes and dashes
    text = text.replace("’", "'").replace("`", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    # Remove common video / remaster artifacts in titles
    text = re.sub(r'\(official\s*(?:video|audio|music\s*video|lyric\s*video)?\)', '', text, flags=re.I)
    text = re.sub(r'\[official\s*(?:video|audio|music\s*video|lyric\s*video)?\]', '', text, flags=re.I)
    text = re.sub(r'\(remaster(?:ed)?(?:\s*\d{4})?\)', '', text, flags=re.I)
    text = re.sub(r'\[remaster(?:ed)?(?:\s*\d{4})?\]', '', text, flags=re.I)
    # Collapse multiple whitespaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def compute_track_hash(artist: str, title: str) -> str:
    """Compute a deterministic 16-character SHA-256 hash for a track."""
    norm_artist = clean_text(artist)
    norm_title = clean_text(title)
    canonical = f"{norm_artist}:::{norm_title}".encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()[:16]

class TrackHashManager:
    """Manages seen track hashes to prevent duplicates across runs."""
    
    def __init__(self, storage_file: str = "seen_hashes.json"):
        self.storage_file = storage_file
        self.seen_data: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Load seen hashes from disk."""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.seen_data = data
            except Exception as e:
                print(f"[Warning] Could not load hash store {self.storage_file}: {e}")
                self.seen_data = {}
        else:
            self.seen_data = {}

    def save(self) -> None:
        """Save seen hashes to disk."""
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(self.seen_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Warning] Could not save hash store {self.storage_file}: {e}")

    def is_seen(self, artist: str, title: str) -> bool:
        """Check if an artist & title have already been seen."""
        track_hash = compute_track_hash(artist, title)
        return track_hash in self.seen_data

    def mark_seen(self, artist: str, title: str, youtube_url: str = "", metadata: Optional[Dict[str, Any]] = None) -> str:
        """Record track as seen with timestamp and metadata."""
        track_hash = compute_track_hash(artist, title)
        record = {
            "artist": artist,
            "title": title,
            "youtube_url": youtube_url,
            "seen_at": datetime.now().isoformat()
        }
        if metadata:
            record.update(metadata)
        self.seen_data[track_hash] = record
        return track_hash

    def get_all_hashes(self) -> Set[str]:
        return set(self.seen_data.keys())

    def import_external_file(self, filepath: str) -> int:
        """
        Import an existing text file or playlist to pre-populate seen tracks.
        Can parse lines formatted as:
          - 'Artist - Title'
          - 'Artist - Title | https://...'
          - Plain 16/32/64 char hashes
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        count = 0
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Check if it's already a hex hash
                if re.fullmatch(r'[a-fA-F0-9]{16,64}', line):
                    h = line.lower()[:16]
                    if h not in self.seen_data:
                        self.seen_data[h] = {
                            "imported_from": filepath,
                            "seen_at": datetime.now().isoformat()
                        }
                        count += 1
                    continue

                # Check for 'Artist - Title' format
                parts = line.split(" - ", 1)
                if len(parts) == 2:
                    artist = parts[0].strip()
                    rest = parts[1].strip()
                    # Strip any trailing URL or pipe
                    title = rest.split("|")[0].split("http")[0].strip()
                    if artist and title:
                        h = compute_track_hash(artist, title)
                        if h not in self.seen_data:
                            self.seen_data[h] = {
                                "artist": artist,
                                "title": title,
                                "imported_from": filepath,
                                "seen_at": datetime.now().isoformat()
                            }
                            count += 1

        self.save()
        return count
