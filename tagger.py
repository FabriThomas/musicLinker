import re
import urllib.request
import urllib.parse
import json
import time
from typing import Dict, List, Any, Optional
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, TDRC, TDOR, COMM, TPE1, TPE2, TALB, TIT2

USER_AGENT = "MusicLinker/2.0 ( https://github.com/ ; musiclinker@local )"

def parse_multi_artists(artist_string: str, title_string: str) -> Dict[str, Any]:
    """
    Parse collaborating and featured artists from artist string and title.
    Examples:
      - "Daft Punk feat. Pharrell Williams" -> primary: "Daft Punk", all: ["Daft Punk", "Pharrell Williams"]
      - "David Bowie & Queen" -> primary: "David Bowie", all: ["David Bowie", "Queen"]
      - "Song Name (feat. Drake)" -> clean title: "Song Name", all: [..., "Drake"]
    """
    artist_str = (artist_string or "").strip()
    title_str = (title_string or "").strip()

    # 1. Extract featuring from title: (feat. X), [ft. Y], (with Z)
    feat_pattern = r'[\(\[\{]\s*(?:feat\.?|ft\.?|with)\s+([^\)\]\}]+)[\)\]\}]'
    title_feats = re.findall(feat_pattern, title_str, flags=re.IGNORECASE)
    clean_title = re.sub(feat_pattern, '', title_str, flags=re.IGNORECASE).strip()

    # Also clean video / audio artifacts: (Official Audio), [Official Video], etc.
    clean_title = re.sub(r'[\(\[\{]\s*official\s*(?:video|audio|music\s*video|lyric\s*video)?\s*[\)\]\}]', '', clean_title, flags=re.IGNORECASE).strip()

    # 2. Delimiters to split collaborating artists
    delimiters = r'\s+(?:feat\.?|ft\.?|with|&|x|vs\.)\s+|,\s*'
    raw_artists = re.split(delimiters, artist_str, flags=re.IGNORECASE)

    all_artists: List[str] = []
    for a in raw_artists:
        a_clean = a.strip()
        if a_clean and a_clean.lower() not in [x.lower() for x in all_artists]:
            all_artists.append(a_clean)

    for f_group in title_feats:
        for f in re.split(delimiters, f_group, flags=re.IGNORECASE):
            f_clean = f.strip()
            if f_clean and f_clean.lower() not in [x.lower() for x in all_artists]:
                all_artists.append(f_clean)

    primary_artist = all_artists[0] if all_artists else artist_str

    return {
        "title": clean_title or title_str,
        "primary_artist": primary_artist,
        "all_artists": all_artists or [primary_artist],
        "display_artist": ", ".join(all_artists) if len(all_artists) > 1 else primary_artist
    }

def fetch_musicbrainz_release_info(artist: str, title: str) -> Dict[str, Any]:
    """
    Query MusicBrainz API for original release year, album, and release date.
    MusicBrainz is free and requires a custom User-Agent.
    """
    clean_artist = re.sub(r'[^\w\s]', '', artist).strip()
    clean_title = re.sub(r'[^\w\s]', '', title).strip()
    query = f'artist:"{clean_artist}" AND recording:"{clean_title}"'
    url = f"https://musicbrainz.org/ws/2/recording?query={urllib.parse.quote(query)}&fmt=json&limit=3"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            recordings = data.get("recordings", [])
            if not recordings:
                return {}

            rec = recordings[0]
            first_release_date = rec.get("first-release-date", "")
            orig_year = None
            if first_release_date and len(first_release_date) >= 4:
                try:
                    orig_year = int(first_release_date[:4])
                except ValueError:
                    pass

            album_name = ""
            release_year = orig_year
            releases = rec.get("releases", [])
            if releases:
                rel = releases[0]
                album_name = rel.get("title", "")
                r_date = rel.get("date", "")
                if r_date and len(r_date) >= 4:
                    try:
                        release_year = int(r_date[:4])
                    except ValueError:
                        pass

            return {
                "album": album_name,
                "year": release_year,
                "original_year": orig_year
            }
    except Exception:
        return {}

def tag_mp3_file(
    filepath: str,
    title: str,
    primary_artist: str,
    all_artists: List[str],
    album: str = "Singles",
    year: Optional[int] = None,
    original_year: Optional[int] = None,
    genre: str = "",
    plays: int = 0,
    cover_url: str = ""
) -> None:
    """
    Apply comprehensive ID3v2.4 tags including multi-artist frames, release dates, and artwork.
    """
    try:
        # 1. EasyID3 tags
        try:
            audio = EasyID3(filepath)
        except Exception:
            id3_init = ID3()
            id3_init.save(filepath)
            audio = EasyID3(filepath)

        audio["title"] = title
        audio["artist"] = all_artists
        audio["albumartist"] = primary_artist
        audio["album"] = album or "Singles"
        if genre:
            audio["genre"] = genre
        if year:
            audio["date"] = str(year)
        audio.save()

        # 2. Advanced ID3 Frames (Years, Scrobble Count, Cover Art)
        id3 = ID3(filepath)

        if original_year:
            id3.add(TDOR(encoding=3, text=str(original_year)))
        if year:
            id3.add(TDRC(encoding=3, text=str(year)))

        if plays > 0:
            id3.add(COMM(encoding=3, lang='eng', desc='Last.fm Plays', text=f"Scrobbles: {plays}"))

        # Download & embed cover art
        if cover_url and cover_url.startswith("http"):
            try:
                img_req = urllib.request.Request(cover_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(img_req, timeout=8) as img_resp:
                    img_data = img_resp.read()
                    mime = "image/png" if cover_url.lower().endswith(".png") else "image/jpeg"
                    id3.add(APIC(
                        encoding=3,
                        mime=mime,
                        type=3,  # Front cover
                        desc="Cover",
                        data=img_data
                    ))
            except Exception:
                pass

        id3.save()
    except Exception as e:
        print(f"  [Tagging Warning for {filepath}]: {e}")
