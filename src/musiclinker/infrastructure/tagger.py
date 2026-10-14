"""Advanced ID3v2.4 tagger supporting null-delimited multi-artist frames, original year, ReplayGain, and cover art."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TDRC,
    TDOR,
    TIT2,
    TALB,
    TPE1,
    TPE2,
    TXXX,
    USLT,
)
from mutagen.mp3 import MP3

from musiclinker.core.exceptions import TaggingError
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists


class AudioTagger:
    """Reads and writes audiophile-grade ID3v2.4 metadata frames on MP3 files."""

    def write_id3v24_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        release_year: str = "",
        original_year: str = "",
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        lyrics_text: str = "",
        cover_bytes: bytes | None = None,
        cover_mime: str = "image/jpeg",
    ) -> tuple[int, int]:
        """
        Write standard ID3v2.4 frames (`v2_version=4`).

        - `TPE1` stores a list of artist strings (`mutagen` writes null-byte `\\x00` separators in ID3v2.4).
        - `TPE2` stores the primary Album Artist.
        - `TDRC` and `TDOR` store Release Year and Original Year.
        - `TXXX:REPLAYGAIN_TRACK_GAIN` stores loudness normalization offset.
        Returns `(duration_seconds, bitrate_kbps)`.
        """
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            # encoding=3 specifies UTF-8, the standard for ID3v2.4 multi-value frames
            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if release_year:
                tags.delall("TDRC")
                tags.add(TDRC(encoding=3, text=[str(release_year)]))

            if original_year:
                tags.delall("TDOR")
                tags.add(TDOR(encoding=3, text=[str(original_year)]))

            tags.delall("COMM")
            tags.add(
                COMM(
                    encoding=3,
                    lang="eng",
                    desc="MusicLinker",
                    text=[f"Last.fm Scrobbles: {lastfm_playcount}"],
                )
            )

            tags.delall("TXXX:REPLAYGAIN_TRACK_GAIN")
            tags.add(
                TXXX(
                    encoding=3,
                    desc="REPLAYGAIN_TRACK_GAIN",
                    text=[f"{replaygain_db:+.2f} dB"],
                )
            )

            if lyrics_text:
                tags.delall("USLT")
                tags.add(USLT(encoding=3, lang="eng", desc="SyncedLyrics", text=lyrics_text))

            if cover_bytes:
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime=cover_mime or "image/jpeg",
                        type=3,  # Front Cover
                        desc="Cover",
                        data=cover_bytes,
                    )
                )

            tags.save(str(mp3_path), v2_version=4)

            audio = MP3(str(mp3_path))
            duration = int(round(audio.info.length)) if audio.info else 0
            bitrate = int(round((audio.info.bitrate or 320000) / 1000)) if audio.info else 320
            return duration, bitrate
        except Exception as exc:
            raise TaggingError(f"Failed to write ID3v2.4 tags to {mp3_path.name}: {exc}") from exc

    def read_mp3_metadata(self, mp3_path: Path) -> dict[str, Any]:
        """Inspect an existing MP3 file on disk for library synchronization."""
        audio = MP3(str(mp3_path))
        duration = int(round(audio.info.length)) if audio.info else 0
        bitrate = int(round((audio.info.bitrate or 320000) / 1000)) if audio.info else 320
        tags = audio.tags

        fallback_stem = mp3_path.stem
        if " - " in fallback_stem:
            fb_artist, fb_title = fallback_stem.split(" - ", 1)
        else:
            fb_artist, fb_title = mp3_path.parent.parent.name or "Unknown Artist", fallback_stem

        if tags is None:
            album_artist, artists, clean_title = split_artists(fb_artist, fb_title)
            return {
                "track_hash": compute_track_hash(album_artist, clean_title),
                "title": clean_title,
                "artist_display": ", ".join(artists),
                "artists": artists,
                "album_artist": album_artist,
                "album": mp3_path.parent.name if mp3_path.parent.name != "downloads" else "Singles",
                "release_year": "",
                "original_year": "",
                "duration": duration,
                "bitrate_kbps": bitrate,
                "has_cover": False,
                "cover_mime": "",
                "replaygain_db": 0.0,
            }

        tpe1 = tags.get("TPE1")
        artists = [str(x).strip() for x in (tpe1.text if tpe1 else []) if str(x).strip()]
        if len(artists) == 1:
            _, split_list, _ = split_artists(artists[0])
            artists = split_list
        if not artists:
            artists = [fb_artist.strip()]

        tpe2 = tags.get("TPE2")
        album_artist = str(tpe2.text[0]).strip() if (tpe2 and tpe2.text) else artists[0]

        tit2 = tags.get("TIT2")
        title = str(tit2.text[0]).strip() if (tit2 and tit2.text) else fb_title.strip()

        talb = tags.get("TALB")
        album = str(talb.text[0]).strip() if (talb and talb.text) else "Singles"

        tdrc = tags.get("TDRC")
        release_year = str(tdrc.text[0])[:4] if (tdrc and tdrc.text) else ""

        tdor = tags.get("TDOR")
        original_year = str(tdor.text[0])[:4] if (tdor and tdor.text) else release_year

        apics = tags.getall("APIC")
        has_cover = bool(apics)
        cover_mime = str(apics[0].mime) if apics else ""

        rg_frame = tags.get("TXXX:REPLAYGAIN_TRACK_GAIN")
        replaygain_db = 0.0
        if rg_frame and rg_frame.text:
            try:
                replaygain_db = float(str(rg_frame.text[0]).replace("dB", "").strip())
            except ValueError:
                replaygain_db = 0.0

        return {
            "track_hash": compute_track_hash(album_artist, title),
            "title": title,
            "artist_display": ", ".join(artists),
            "artists": artists,
            "album_artist": album_artist,
            "album": album,
            "release_year": release_year,
            "original_year": original_year,
            "duration": duration,
            "bitrate_kbps": bitrate,
            "has_cover": has_cover,
            "cover_mime": cover_mime,
            "replaygain_db": replaygain_db,
        }

    @staticmethod
    def extract_cover_bytes(mp3_path: Path) -> tuple[bytes | None, str]:
        """Extract embedded APIC cover art bytes from an MP3 file on demand."""
        try:
            tags = ID3(str(mp3_path))
            apics = tags.getall("APIC")
            if apics:
                return bytes(apics[0].data), str(apics[0].mime or "image/jpeg")
        except Exception:
            pass
        return None, "image/jpeg"


audio_tagger = AudioTagger()