"""Domain models and Pydantic request/response schemas."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LastFMPeriod(str, Enum):
    OVERALL = "overall"
    SEVEN_DAY = "7day"
    ONE_MONTH = "1month"
    THREE_MONTH = "3month"
    SIX_MONTH = "6month"
    TWELVE_MONTH = "12month"
    CUSTOM_YEAR = "custom_year"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class CandidateTrack:
    """A track extracted from Last.fm or searched via YouTube prior to download."""

    artist: str
    title: str
    playcount: int = 0
    album: str = ""
    track_hash: str = ""
    mbid: str = ""
    youtube_url: str = ""
    duration: int = 0
    cover_url: str = ""


@dataclass(slots=True)
class TrackEntity:
    """A downloaded and indexed audio track stored in SQLite (`library.db`)."""

    id: int
    track_hash: str
    title: str
    artist_display: str
    artists: list[str]
    album_artist: str
    album: str
    release_year: str
    original_year: str
    duration: int
    file_path: str
    lrc_path: str
    cover_mime: str
    has_cover: bool
    lastfm_playcount: int
    local_playcount: int
    replaygain_db: float
    bitrate_kbps: int
    added_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "track_hash": self.track_hash,
            "title": self.title,
            "artist_display": self.artist_display,
            "artists": self.artists,
            "album_artist": self.album_artist,
            "album": self.album,
            "release_year": self.release_year,
            "original_year": self.original_year,
            "duration": self.duration,
            "has_lrc": bool(self.lrc_path),
            "has_cover": self.has_cover,
            "lastfm_playcount": self.lastfm_playcount,
            "local_playcount": self.local_playcount,
            "replaygain_db": self.replaygain_db,
            "bitrate_kbps": self.bitrate_kbps,
            "added_at": self.added_at,
            "stream_url": f"/api/tracks/{self.id}/stream",
            "file_url": f"/api/tracks/{self.id}/file",
            "cover_url": f"/api/tracks/{self.id}/cover" if self.has_cover else "",
            "lyrics_url": f"/api/tracks/{self.id}/lyrics",
        }


@dataclass(slots=True)
class ReleaseMetadata:
    """Enriched release metadata resolved via MusicBrainz & Cover Art Archive."""

    album: str = ""
    album_artist: str = ""
    release_year: str = ""
    original_year: str = ""
    release_mbid: str = ""
    cover_bytes: bytes | None = None
    cover_mime: str = "image/jpeg"


class ExtractionJobRequest(BaseModel):
    """Payload to start a Last.fm extraction and optional download job."""

    username: str = Field(..., min_length=1, description="Last.fm username")
    period: str = Field(default="12month", description="Last.fm period or 'custom_year'")
    year: int | None = Field(default=None, description="Calendar year if period == 'custom_year'")
    min_plays: int = Field(default=10, ge=1, description="Minimum scrobble count threshold")
    download_cap: int = Field(default=25, ge=1, le=500, description="Max tracks to download in this run")
    bitrate_kbps: int = Field(default=320, description="Target MP3 bitrate (192 or 320)")
    download_enabled: bool = Field(default=True, description="Whether to download or only export list")
    skip_seen_hashes: bool = Field(default=True, description="Skip tracks already in seen_hashes")


class DirectDownloadRequest(BaseModel):
    """Payload to search and download a single track or YouTube URL directly."""

    query_or_url: str = Field(..., min_length=2)
    artist_override: str = Field(default="")
    title_override: str = Field(default="")
    album_override: str = Field(default="")
    bitrate_kbps: int = Field(default=320)


class ScrobblePayload(BaseModel):
    """Single scrobble or Now Playing notification from a client player."""

    track_id: int | None = None
    artist: str
    title: str
    album: str = ""
    duration: int = 0
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class BatchScrobblePayload(BaseModel):
    """Batch of offline scrobbles flushed from the Android PWA IndexedDB outbox."""

    scrobbles: list[ScrobblePayload]


class TransitionRulePayload(BaseModel):
    """Custom transition rule between two tracks with probability weight and crossfade seconds."""

    from_track_id: int
    to_track_id: int
    probability: float = Field(default=1.0, ge=0.05, le=1.0)
    crossfade_seconds: float = Field(default=3.0, ge=0.0, le=15.0)


class SettingsUpdatePayload(BaseModel):
    """User configuration update payload."""

    lastfm_api_key: str | None = None
    lastfm_api_secret: str | None = None
    lastfm_username: str | None = None
    lastfm_session_key: str | None = None
    default_bitrate_kbps: int | None = None
    default_min_plays: int | None = None
    default_download_cap: int | None = None
    require_lan_token: bool | None = None
    normalize_audio: bool | None = None