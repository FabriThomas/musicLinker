"""Custom exception hierarchy for MusicLinker."""

from __future__ import annotations


class MusicLinkerError(Exception):
    """Base exception for all MusicLinker domain and infrastructure errors."""


class ConfigurationError(MusicLinkerError):
    """Raised when required API keys or settings are missing or invalid."""


class LastFMError(MusicLinkerError):
    """Raised when the Last.fm API returns an error or cannot be reached."""


class LastFMAuthError(LastFMError):
    """Raised when Last.fm session authentication fails or is missing."""


class YouTubeResolutionError(MusicLinkerError):
    """Raised when yt-dlp cannot resolve or download an audio stream."""


class TaggingError(MusicLinkerError):
    """Raised when Mutagen fails to write ID3v2.4 metadata frames."""


class JobCancelledError(MusicLinkerError):
    """Raised when a user cancels an active background extraction/download job."""