"""External infrastructure adapters: SQLite WAL, Last.fm, yt-dlp, MusicBrainz, LRCLIB, ID3v2.4, LLM Proxy, Spotify."""

from musiclinker.infrastructure.spotify_client import SpotifyClient, spotify_client

__all__ = ["SpotifyClient", "spotify_client"]