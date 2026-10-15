import pytest
from musiclinker.core.paths import PATHS
from musiclinker.core.security import generate_pair_token, is_loopback_host
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists, normalize_text
from musiclinker.infrastructure.db import DatabaseRepository
from musiclinker.domain.models import TrackEntity, CandidateTrack, ExtractionJobRequest


def test_paths():
    assert PATHS.root_dir.exists()
    assert PATHS.downloads_dir.exists()
    assert PATHS.app_data_dir.exists()


def test_security():
    token = generate_pair_token()
    assert len(token) >= 24
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("192.168.1.50") is False


def test_hasher_normalize_and_split():
    album_artist, artists, clean_title = split_artists(
        "Daft Punk feat. Pharrell Williams & Nile Rodgers",
        "Get Lucky"
    )
    assert album_artist == "Daft Punk"
    assert "Pharrell Williams" in artists
    assert "Nile Rodgers" in artists
    assert clean_title == "Get Lucky"

    # Testing featuring in title extraction
    aa, art_list, c_title = split_artists(
        "Daft Punk",
        "Get Lucky (feat. Pharrell Williams)"
    )
    assert aa == "Daft Punk"
    assert "Pharrell Williams" in art_list
    assert c_title == "Get Lucky"

    norm = normalize_text("Get Lucky (2013 Remastered Edition)")
    assert "remaster" not in norm
    assert "get lucky" in norm

    hash1 = compute_track_hash("Daft Punk", "Get Lucky")
    hash2 = compute_track_hash("Daft Punk feat. Pharrell Williams", "Get Lucky (Radio Edit)")
    assert hash1 == hash2
    assert len(hash1) == 16


def test_database_repository(tmp_path):
    test_db = tmp_path / "test_library.db"
    repo = DatabaseRepository(db_path=test_db)
    
    # Test settings
    settings = repo.get_settings()
    assert "lan_pair_token" in settings
    repo.update_settings({"lastfm_username": "testuser"})
    updated = repo.get_settings()
    assert updated["lastfm_username"] == "testuser"

    # Test seen hashes
    assert repo.is_hash_seen("abcdef1234567890") is False
    repo.mark_hash_seen("abcdef1234567890", "Artist", "Title")
    assert repo.is_hash_seen("abcdef1234567890") is True

    # Test track insertion
    track = repo.upsert_track(
        track_hash="hash001",
        title="Midnight City",
        artist_display="M83",
        artists=["M83"],
        album_artist="M83",
        album="Hurry Up, We're Dreaming",
        release_year="2011",
        original_year="2011",
        duration=243,
        file_path="/downloads/M83/Midnight City.mp3",
        lrc_path="/downloads/M83/Midnight City.lrc",
        cover_mime="image/jpeg",
        has_cover=True,
        lastfm_playcount=150,
        replaygain_db=-2.5,
        bitrate_kbps=320,
    )
    assert track.id > 0
    assert track.title == "Midnight City"
    assert track.artist_display == "M83"
    assert track.replaygain_db == -2.5

    # Test library query
    tracks = repo.list_tracks(search="Midnight")
    assert len(tracks) == 1
    assert tracks[0].id == track.id


def test_models():
    candidate = CandidateTrack(artist="Pink Floyd", title="Time", playcount=42)
    assert candidate.artist == "Pink Floyd"
    assert candidate.playcount == 42

    job_req = ExtractionJobRequest(username="testuser", min_plays=5, download_cap=10)
    assert job_req.username == "testuser"
    assert job_req.min_plays == 5


def test_v21_cleaners_and_llm():
    from musiclinker.infrastructure.hasher import clean_album_name, clean_track_title
    from musiclinker.infrastructure.llm_client import LLMClient

    # Test album cleaner
    album1 = clean_album_name("Random Access Memories (10th Anniversary Edition)")
    assert "Anniversary" not in album1
    assert "Random Access Memories" in album1

    album2 = clean_album_name("Song - Single", track_title="Song")
    assert album2 == "Song"

    # Test track cleaner
    title1 = clean_track_title("Get Lucky (Official Audio)")
    assert title1 == "Get Lucky"

    # Test LLM client configuration
    llm = LLMClient(base_url="https://api.openai.com/v1", api_key="")
    assert llm.is_configured() is False
    llm2 = LLMClient(base_url="https://api.openai.com/v1", api_key="sk-test")
    assert llm2.is_configured() is True
    llm_local = LLMClient(base_url="http://localhost:11434/v1")
    assert llm_local.is_configured() is True


def test_reverse_proxy_endpoints():
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.db import db_repo

    client = TestClient(app)

    # 1. When proxy_server_url is not configured, should return 502
    db_repo.update_settings({"proxy_server_url": "", "proxy_password": ""})
    res = client.get("/v1/models")
    assert res.status_code == 502
    assert "not configured" in res.json()["detail"]

    # 2. When proxy_password is set, wrong password returns 401
    db_repo.update_settings({
        "proxy_server_url": "https://api.openai.com/v1",
        "proxy_password": "supersecretpassword",
        "proxy_name": "custom-proxy"
    })
    res_unauth = client.get("/v1/models")
    assert res_unauth.status_code == 401

    res_wrong = client.get("/v1/models", headers={"Authorization": "Bearer wrongpass"})
    assert res_wrong.status_code == 401

    # Cleanup settings
    db_repo.update_settings({"proxy_server_url": "", "proxy_password": ""})


def test_ai_curation_agent():
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.db import db_repo

    client = TestClient(app, base_url="http://127.0.0.1")

    # Test agent fallback when LLM not configured
    db_repo.update_settings({"llm_base_url": "https://api.openai.com/v1", "llm_api_key": ""})
    res = client.post("/api/curation/agent", json={"instruction": "Deduplicate all tracks in my library"})
    assert res.status_code == 200
    data = res.json()
    assert "reply" in data
    assert "actions" in data
    assert "debug" in data
    assert data["used_llm"] is False
    assert data["debug"]["status"] in ("heuristics_only", "fallback")
    assert "evaluated_tracks_count" in data["debug"]

    # Test agent with title cleaning prompt
    res2 = client.post("/api/curation/agent", json={"instruction": "Clean messy track titles"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert "reply" in data2
    assert "debug" in data2
    assert isinstance(data2["actions"], list)

    # Test LLMClient thinking tags extraction
    from musiclinker.infrastructure.llm_client import LLMClient
    thinking, clean = LLMClient._extract_thinking("<think>Let me evaluate track 1 and 2</think>{\"reply\": \"Cleaned!\"}")
    assert thinking == "Let me evaluate track 1 and 2"
    assert clean == "{\"reply\": \"Cleaned!\"}"


def test_spotify_client_and_youtube_music():
    from musiclinker.infrastructure.spotify_client import spotify_client
    from musiclinker.services.library_service import library_service

    # 1. URL pattern recognition
    assert spotify_client.is_spotify_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy") is True
    assert spotify_client.is_spotify_url("https://open.spotify.com/intl-pt/album/4aawyAB9vmqN3uQ7FjRGTy?si=abc") is True
    assert spotify_client.is_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") is True
    assert spotify_client.is_spotify_url("https://open.spotify.com/track/6OmhkSOpvYBokMKQxpIGx2") is True
    assert spotify_client.is_spotify_url("spotify:album:4aawyAB9vmqN3uQ7FjRGTy") is True
    assert spotify_client.is_spotify_url("spotify:track:6OmhkSOpvYBokMKQxpIGx2") is True
    assert spotify_client.is_spotify_url("https://www.youtube.com/watch?v=123") is False
    assert spotify_client.is_spotify_url("Random Track Name") is False

    # 2. URI parsing
    parsed_album = spotify_client.parse_spotify_uri("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
    assert parsed_album == ("album", "4aawyAB9vmqN3uQ7FjRGTy")

    parsed_track = spotify_client.parse_spotify_uri("spotify:track:6OmhkSOpvYBokMKQxpIGx2")
    assert parsed_track == ("track", "6OmhkSOpvYBokMKQxpIGx2")

    # 3. Live Spotify embed metadata fetch (zero-auth public embed)
    meta = spotify_client.fetch_spotify_metadata("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
    assert meta["source"] == "spotify"
    assert meta["entity_type"] == "album"
    assert "Pitbull" in meta["artist"]
    assert "Global Warming" in meta["album"]
    assert len(meta["tracks"]) >= 10
    first_tr = meta["tracks"][0]
    assert first_tr["track_number"] == 1
    assert "Global Warming" in first_tr["title"]
    assert bool(meta["cover_url"]) is True

    # 4. Library service external lookup integration
    lookup = library_service.lookup_external_album_tracklist(
        artist="",
        album_or_url="https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy",
    )
    assert lookup["source"] == "spotify"
    assert len(lookup["tracks"]) >= 10


def test_direct_album_download_job_endpoint():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.services.job_service import job_service

    client = TestClient(app, base_url="http://127.0.0.1")

    # Test direct album job creation with Spotify URL and empty artist (should not 500)
    with patch.object(job_service, "_run_album_download_pipeline") as mock_pipeline:
        res = client.post("/api/jobs/download-album", json={
            "artist": "",
            "album": "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy",
            "tracks": []
        })
        assert res.status_code == 200
        data = res.json()
        assert "job_id" in data
        assert len(data["job_id"]) == 10
        assert data["job_type"] == "album_download"

    # Test direct album job creation with empty album returning 422 validation error instead of 500
    res_empty = client.post("/api/jobs/download-album", json={
        "artist": "",
        "album": "",
        "tracks": []
    })
    assert res_empty.status_code == 422


