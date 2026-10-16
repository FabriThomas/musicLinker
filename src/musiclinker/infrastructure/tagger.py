"""Advanced ID3v2.4 tagger supporting TRCK track numbers, null-delimited multi-artist frames, ReplayGain, and cover art."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TALB,
    TDRC,
    TDOR,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TXXX,
    USLT,
)
from mutagen.mp3 import MP3

from musiclinker.core.exceptions import TaggingError
from musiclinker.infrastructure.hasher import clean_album_name, compute_track_hash, split_artists


class AudioTagger:
    """Reads and writes audiophile-grade ID3v2.4 metadata frames (`TIT2`, `TPE1`, `TPE2`, `TALB`, `TRCK`, `TDRC`, `TDOR`, `APIC`)."""

    def write_id3v24_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        lyrics_text: str = "",
        cover_bytes: bytes | None = None,
        cover_mime: str = "image/jpeg",
    ) -> tuple[int, int]:
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if track_number and track_number > 0:
                tags.delall("TRCK")
                tags.add(TRCK(encoding=3, text=[str(track_number)]))

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
                        type=3,
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

    def update_existing_mp3_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
    ) -> None:
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if track_number and track_number > 0:
                tags.delall("TRCK")
                tags.add(TRCK(encoding=3, text=[str(track_number)]))

            if release_year:
                tags.delall("TDRC")
                tags.add(TDRC(encoding=3, text=[str(release_year)]))

            if original_year:
                tags.delall("TDOR")
                tags.add(TDOR(encoding=3, text=[str(original_year)]))

            tags.save(str(mp3_path), v2_version=4)
        except Exception as exc:
            raise TaggingError(f"Failed updating ID3 tags on {mp3_path.name}: {exc}") from exc

    def update_mp3_lyrics(self, mp3_path: Path, lyrics_text: str) -> None:
        """Permanently alters the USLT ID3v2.4 frame in the MP3 file with updated synchronized/plain lyrics."""
        try:
            try:
                tags = ID3(str(mp3_path))
            except Exception:
                tags = ID3()
            tags.delall("USLT")
            if lyrics_text.strip():
                tags.add(USLT(encoding=3, lang="eng", desc="SyncedLyrics", text=lyrics_text))
            tags.save(str(mp3_path), v2_version=4)
        except Exception as exc:
            raise TaggingError(f"Failed updating lyrics on {mp3_path.name}: {exc}") from exc

    def read_mp3_metadata(self, mp3_path: Path) -> dict[str, Any]:
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
            raw_album = mp3_path.parent.name if mp3_path.parent.name != "downloads" else "Singles"
            return {
                "track_hash": compute_track_hash(album_artist, clean_title),
                "title": clean_title,
                "artist_display": ", ".join(artists),
                "artists": artists,
                "album_artist": album_artist,
                "album": clean_album_name(raw_album, clean_title),
                "track_number": 0,
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

        trck = tags.get("TRCK")
        track_number = 0
        if trck and trck.text:
            raw_trck = str(trck.text[0]).split("/")[0].strip()
            if raw_trck.isdigit():
                track_number = int(raw_trck)

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
            "track_number": track_number,
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
        try:
            tags = ID3(str(mp3_path))
            apics = tags.getall("APIC")
            if apics:
                return bytes(apics[0].data), str(apics[0].mime or "image/jpeg")
        except Exception:
            pass
        return None, "image/jpeg"


audio_tagger = AudioTagger()