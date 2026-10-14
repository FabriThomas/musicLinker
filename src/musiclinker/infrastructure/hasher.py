"""Deterministic SHA-256 track normalizer, multi-artist splitter, and automatic album cleaner."""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Matches parenthetical/bracketed remaster, live, version, or featuring noise
_BRACKET_NOISE_RE = re.compile(
    r"[\(\[\{][^\)\]\}]*?\b("
    r"remaster(?:ed)?|deluxe|anniversary|expanded|edition|version|edit|"
    r"feat\.?|ft\.?|featuring|with|prod\.?|official\s+video|official\s+audio|lyrics?|"
    r"bonus\s+track|explicit|clean|single|ep"
    r")\b[^\)\]\}]*?[\)\]\}]",
    re.IGNORECASE,
)
_ALBUM_EDITION_NOISE_RE = re.compile(
    r"(?:\s*[\(\[\{][^\)\]\}]*?\b(?:"
    r"deluxe|remaster(?:ed)?|expanded|anniversary|special\s+edition|collector'?s\s+edition|"
    r"bonus\s+track\s+version|explicit\s+version|international\s+version|super\s+deluxe|"
    r"\d{4}\s+remaster|original\s+motion\s+picture\s+soundtrack"
    r")\b[^\)\]\}]*?[\)\]\}])|"
    r"(?:\s+-\s+(?:single|ep|deluxe\s+edition|remastered(?:\s+\d{4})?)$)",
    re.IGNORECASE,
)
_FEAT_NOISE_RE = re.compile(
    r"\b(?:feat\.?|ft\.?|featuring|with)\s+.+$",
    re.IGNORECASE,
)
_DASH_SUFFIX_RE = re.compile(
    r"\s+-\s+.*?\b(?:remaster(?:ed)?|live|mono|stereo|single\s+version|radio\s+edit|acoustic|\d{4})\b.*$",
    re.IGNORECASE,
)
_MULTI_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,\s*|\s+(?:feat\.?|ft\.?|featuring|with|&|x|vs\.?)\s+|\s*;\s*|\s*/\s*)\s*",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    """Normalize Unicode accents, strip remaster/featuring suffixes, and collapse to lowercase alphanumerics."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _BRACKET_NOISE_RE.sub(" ", text)
    text = _DASH_SUFFIX_RE.sub(" ", text)
    text = _FEAT_NOISE_RE.sub(" ", text)
    text = text.lower()
    text = _NON_ALNUM_RE.sub(" ", text).strip()
    return " ".join(text.split())


def clean_album_name(raw_album: str, track_title: str = "") -> str:
    """
    Auto-filter and clean messy album titles returned by Last.fm, YouTube, or MusicBrainz.

    - Strips '(Deluxe Edition)', '[Remastered 2021]', '- Single', '- EP', etc.
    - Replaces YouTube auto-generated 'Provided to YouTube' or empty names with 'Singles'.
    """
    if not raw_album:
        return "Singles"
    cleaned = raw_album.strip()
    if cleaned.lower() in ("unknown", "unknown album", "none", "n/a", "youtube"):
        return "Singles"

    cleaned = _ALBUM_EDITION_NOISE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—")

    # If the album was literally "<Track Title> - Single", classify cleanly or keep base title
    if not cleaned:
        return track_title.strip() or "Singles"
    return cleaned


def clean_track_title(raw_title: str) -> str:
    """Strip '(Official Video)', '[Lyrics]', '- 2011 Remaster', etc. for clean display & tagging."""
    if not raw_title:
        return "Unknown Title"
    text = raw_title.strip()
    text = re.sub(
        r"[\(\[\{]\s*(?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|lyrics?|hd|hq|4k|visualizer)\s*[\)\]\}]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _DASH_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or raw_title.strip()


def split_artists(artist_raw: str, title_raw: str = "") -> tuple[str, list[str], str]:
    """Extract primary album artist, full list of individual artists, and cleaned song title."""
    clean_title = clean_track_title(title_raw)
    extracted_from_title: list[str] = []

    title_feat_match = re.search(
        r"[\(\[\{]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]\}]+)[\)\]\}]",
        clean_title,
        re.IGNORECASE,
    )
    if title_feat_match:
        feat_str = title_feat_match.group(1).strip()
        extracted_from_title = [p.strip() for p in _MULTI_ARTIST_SPLIT_RE.split(feat_str) if p.strip()]
        clean_title = (
            clean_title[: title_feat_match.start()].strip()
            + " "
            + clean_title[title_feat_match.end() :].strip()
        ).strip()

    parts = [p.strip() for p in _MULTI_ARTIST_SPLIT_RE.split(artist_raw) if p.strip()]
    for extra in extracted_from_title:
        if extra.lower() not in {p.lower() for p in parts}:
            parts.append(extra)

    if not parts:
        parts = ["Unknown Artist"]

    album_artist = parts[0]
    return album_artist, parts, clean_title


def compute_track_hash(artist: str, title: str) -> str:
    """Compute a deterministic 16-character SHA-256 hex digest from primary artist and normalized title."""
    primary_artist, _, cleaned_title = split_artists(artist, title)
    norm_artist = normalize_text(primary_artist)
    norm_title = normalize_text(cleaned_title)
    payload = f"{norm_artist}::{norm_title}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]