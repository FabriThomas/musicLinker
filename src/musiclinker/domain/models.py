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
    """A track extracted from Last.fm, MusicBrainz album lookup, or YouTube search prior to download."""

    artist: str
    title: str
    playcount: int = 0
    album: str = ""
    track_number: int = 0
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
    track_number: int
    release_year: str
    original_year: str
    duration: int
    file_path: str
    lrc_path: str
    lyric_sync_mode: str
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
            "track_number": self.track_number,
            "release_year": self.release_year,
            "original_year": self.original_year,
            "duration": self.duration,
            "has_lrc": bool(self.lrc_path),
            "lyric_sync_mode": self.lyric_sync_mode or ("exact_lrc" if self.lrc_path else "none"),
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
    track_number: int = 0
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
    use_llm_filter: bool = Field(
        default=False,
        description="Run LLM/heuristic deduplication and album/track canonicalization before downloading",
    )


class DirectDownloadRequest(BaseModel):
    """Payload to search and download a single track or YouTube URL directly."""

    query_or_url: str = Field(..., min_length=2)
    artist_override: str = Field(default="")
    title_override: str = Field(default="")
    album_override: str = Field(default="")
    track_number_override: int = Field(default=0)
    bitrate_kbps: int = Field(default=320)


class AlbumTrackItem(BaseModel):
    """Single track entry inside an Album Download request."""

    track_number: int = Field(default=1, ge=1)
    title: str = Field(..., min_length=1)
    artist: str = Field(default="Unknown Artist")
    duration: int = Field(default=0)
    youtube_url: str = Field(default="")


class AlbumDownloadRequest(BaseModel):
    """Payload to download an entire album (either resolved via MusicBrainz/Last.fm/YouTube or custom tracklist)."""

    artist: str = Field(default="", description="Artist name; optional when album URL is provided")
    album: str = Field(..., min_length=1, description="Album name or Spotify/YouTube URL")
    bitrate_kbps: int = Field(default=320)
    tracks: list[AlbumTrackItem] = Field(
        default_factory=list,
        description="Optional explicit tracklist; if empty, server resolves the official tracklist automatically.",
    )
    skip_existing: bool = Field(default=True)



class TrackMetadataUpdatePayload(BaseModel):
    """Payload to rename/edit a track's title, artist, album, track_number, and release year internally & on MP3 tags."""

    title: str = Field(..., min_length=1)
    artist_display: str = Field(..., min_length=1)
    album: str = Field(..., min_length=1)
    track_number: int = Field(default=0, ge=0)
    release_year: str = Field(default="")
    original_year: str = Field(default="")
    remember_album_alias: bool = Field(default=True)


class AlbumBulkRenamePayload(BaseModel):
    """Payload to bulk-rename an album across all matching tracks, update ID3 tags, and save an alias rule."""

    old_album: str = Field(..., min_length=1)
    new_album: str = Field(..., min_length=1)
    album_artist: str = Field(default="")
    remember_alias: bool = Field(default=True)


class LLMCurationAction(BaseModel):
    """A single proposed track rename, album fix, or duplicate merge action produced by the AI Curator."""

    track_id: int = 0
    action_type: str = Field(description="'rename', 'duplicate_remove', or 'bulk_album_rename'")
    current_title: str = ""
    proposed_title: str = ""
    current_artist: str = ""
    proposed_artist: str = ""
    current_album: str = ""
    proposed_album: str = ""
    duplicate_of_track_id: int | None = None
    old_album: str = ""
    new_album: str = ""
    reason: str = ""


class LLMCurationApplyPayload(BaseModel):
    """List of approved LLM curation actions to apply to SQLite and MP3 files."""

    actions: list[LLMCurationAction]


class AgentCommandRequest(BaseModel):
    """Natural language prompt sent to the autonomous AI Music Curator Agent."""

    instruction: str = Field(..., min_length=1)
    history: list[dict[str, str]] = Field(default_factory=list)


class AgentCommandResponse(BaseModel):
    """Interactive response containing the agent's reasoning, reply, and proposed actionable steps."""

    reply: str
    actions: list[LLMCurationAction] = Field(default_factory=list)
    applied_count: int = 0
    used_llm: bool = True


class ScrobblePayload(BaseModel):
    """Single scrobble or Now Playing notification from a client player."""

    track_id: int | None = None
    artist: str
    title: str
    album: str = ""
    duration: int = 0
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class BatchScrobblePayload(BaseModel):
    """Batch of offline scrobbles flushed from the Android APK/PWA IndexedDB outbox."""

    scrobbles: list[ScrobblePayload]


class TransitionRulePayload(BaseModel):
    """Custom transition rule between two tracks with probability weight and crossfade seconds."""

    from_track_id: int
    to_track_id: int
    probability: float = Field(default=1.0, ge=0.05, le=1.0)
    crossfade_seconds: float = Field(default=3.0, ge=0.0, le=15.0)


class SettingsUpdatePayload(BaseModel):
    """User configuration update payload including LLM API link / reverse proxy settings."""

    lastfm_api_key: str | None = None
    lastfm_api_secret: str | None = None
    lastfm_username: str | None = None
    lastfm_session_key: str | None = None
    default_bitrate_kbps: int | None = None
    default_min_plays: int | None = None
    default_download_cap: int | None = None
    require_lan_token: bool | None = None
    normalize_audio: bool | None = None
    auto_clean_albums: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    proxy_name: str | None = None
    proxy_server_url: str | None = None
    proxy_password: str | None = None