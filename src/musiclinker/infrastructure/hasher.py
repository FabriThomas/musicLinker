"""Deterministic SHA-256 track normalizer and deduplication hash engine."""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Matches parenthetical/bracketed remaster, live, version, or featuring noise
_BRACKET_NOISE_RE = re.compile(
    r"[\(\[\{][^\)\]\}]*?\b("
    r"remaster(?:ed)?|deluxe|anniversary|expanded|edition|version|edit|"
    r"feat\.?|ft\.?|featuring|with|prod\.?|official\s+video|official\s+audio|lyrics?"
    r")\b[^\)\]\}]*?[\)\]\}]",
    re.IGNORECASE,
)
_FEAT_NOISE_RE = re.compile(
    r"\b(?:feat\.?|ft\.?|featuring|with)\s+.+$",
    re.IGNORECASE,
)
_DASH_SUFFIX_RE = re.compile(
    r"\s+-\s+.*?\b(?:remaster(?:ed)?|live|mono|stereo|single\s+version|radio\s+edit|\d{4})\b.*$",
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


def split_artists(artist_raw: str, title_raw: str = "") -> tuple[str, list[str], str]:
    """
    Extract primary album artist, full list of individual artists, and cleaned song title.

    Example:
        artist_raw="Daft Punk feat. Pharrell Williams & Nile Rodgers"
        -> album_artist="Daft Punk"
        -> artists=["Daft Punk", "Pharrell Williams", "Nile Rodgers"]
    """
    clean_title = title_raw.strip()
    extracted_from_title: list[str] = []

    # Check if featuring artists are embedded in the title: "Get Lucky (feat. Pharrell Williams)"
    title_feat_match = re.search(
        r"[\(\[\{]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]\}] specifically|[^\)\]\}]+)[\)\]\}]",
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