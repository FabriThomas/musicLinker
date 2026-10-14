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
