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


def test_youtube_album_lookup_mocked():
    from unittest.mock import patch
    from musiclinker.infrastructure.youtube_client import youtube_client
    from musiclinker.services.library_service import library_service

    # Test full video with chapters
    mock_video_info = {
        "title": "Daft Punk - Discovery (Full Album)",
        "uploader": "Daft Punk",
        "webpage_url": "https://www.youtube.com/watch?v=mock123",
        "id": "mock123",
        "chapters": [
            {"title": "01. One More Time", "start_time": 0, "end_time": 320},
            {"title": "02. Aerodynamic", "start_time": 320, "end_time": 530},
        ],
        "thumbnail": "https://img.youtube.com/vi/mock123/hqdefault.jpg",
    }

    from unittest.mock import MagicMock
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = mock_video_info

    with patch("musiclinker.infrastructure.youtube_client.yt_dlp.YoutubeDL", return_value=mock_ydl):
        res = youtube_client.lookup_youtube_album("https://www.youtube.com/watch?v=mock123")
        assert res["artist"] == "Daft Punk"
        assert "Discovery" in res["album"]
        assert len(res["tracks"]) == 2
        assert res["tracks"][0]["title"] == "One More Time"
        assert res["tracks"][0]["duration"] == 320
        assert "t=0s" in res["tracks"][0]["youtube_url"]
        assert res["tracks"][1]["title"] == "Aerodynamic"
        assert res["tracks"][1]["duration"] == 210
        assert "t=320s" in res["tracks"][1]["youtube_url"]

    # Test video with timestamps in description
    mock_desc_info = {
        "title": "Justice - Cross",
        "uploader": "Justice - Topic",
        "webpage_url": "https://www.youtube.com/watch?v=mock456",
        "id": "mock456",
        "duration": 500,
        "description": "Tracklist:\n0:00 1. Genesis\n3:54 2. Let There Be Light\n",
        "chapters": [],
    }
    mock_ydl_desc = MagicMock()
    mock_ydl_desc.__enter__.return_value = mock_ydl_desc
    mock_ydl_desc.extract_info.return_value = mock_desc_info

    with patch("musiclinker.infrastructure.youtube_client.yt_dlp.YoutubeDL", return_value=mock_ydl_desc):
        res = youtube_client.lookup_youtube_album("https://www.youtube.com/watch?v=mock456")
        assert res["artist"] == "Justice"
        assert len(res["tracks"]) == 2
        assert res["tracks"][0]["title"] == "Genesis"
        assert res["tracks"][1]["title"] == "Let There Be Light"

    # Test library service delegation
    with patch("musiclinker.infrastructure.youtube_client.yt_dlp.YoutubeDL", return_value=mock_ydl):
        lib_res = library_service.lookup_external_album_tracklist(
            artist="https://www.youtube.com/watch?v=mock123",  # artist passed as URL should auto-swap
            album_or_url="",
        )
        assert lib_res["source"] == "youtube_playlist"
        assert len(lib_res["tracks"]) == 2


def test_crud_endpoints_and_library_service(tmp_path):
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.db import DatabaseRepository
    from musiclinker.services.library_service import LibraryService

    test_db = tmp_path / "crud_test.db"
    repo = DatabaseRepository(db_path=test_db)
    lib_service = LibraryService(repo=repo)

    # 1. Create a dummy audio file on disk
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(b"dummy mp3 data")
    lrc_file = tmp_path / "song.lrc"
    lrc_file.write_text("[00:00.00] Test Lyric", encoding="utf-8")

    # Insert track into database
    track = repo.upsert_track(
        track_hash="hash_crud_1",
        title="Test Track",
        artist_display="Test Artist",
        artists=["Test Artist"],
        album_artist="Test Artist",
        album="Test Album",
        track_number=1,
        file_path=str(mp3_file),
        lrc_path=str(lrc_file),
    )
    assert track.id > 0
    assert repo.is_hash_seen("hash_crud_1") is True

    # Save an album alias
    repo.save_album_alias("Test Artist", "Old Raw Album", "Test Album")
    aliases = repo.list_album_aliases()
    assert len(aliases) >= 1
    alias_id = aliases[0]["id"]

    # 2. Test deleting single track via library service with file deletion and unmark
    success = lib_service.delete_track(track.id, delete_file=True, unmark_hash=True)
    assert success is True
    assert repo.get_track(track.id) is None
    assert repo.is_hash_seen("hash_crud_1") is False
    assert not mp3_file.exists()
    assert not lrc_file.exists()

    # 3. Test deleting album alias
    lib_service.delete_album_alias(alias_id)
    assert len(repo.list_album_aliases()) == 0

    # 4. Test API endpoint routes using TestClient
    client = TestClient(app, base_url="http://127.0.0.1")

    # Settings: preferred download method
    res_set = client.put("/api/settings", json={"preferred_download_method": "youtube_music"})
    assert res_set.status_code == 200
    assert res_set.json()["preferred_download_method"] == "youtube_music"

    # Batch delete endpoint with empty list (should 422) or valid list
    res_batch = client.post("/api/tracks/batch-delete", json={
        "track_ids": [99999],
        "delete_files": False,
        "unmark_hashes": False
    })
    assert res_batch.status_code == 200
    assert res_batch.json()["count"] == 0

    # Album delete endpoint
    res_album_del = client.delete("/api/albums/NonExistentAlbum?delete_files=false")
    assert res_album_del.status_code == 200
    assert res_album_del.json()["status"] == "deleted"


def test_musicbrainz_tracklist_return_and_timestamp_slicing():
    from unittest.mock import MagicMock, patch
    from musiclinker.infrastructure.musicbrainz_client import musicbrainz_client
    from musiclinker.infrastructure.youtube_client import youtube_client
    from musiclinker.services.library_service import library_service

    # 1. Verify _parse_timestamp_slice parses &t= and &end= cleanly
    clean_url, start_s, end_s = youtube_client._parse_timestamp_slice(
        "https://www.youtube.com/watch?v=mock123&t=320s&end=530s"
    )
    assert clean_url == "https://www.youtube.com/watch?v=mock123"
    assert start_s == 320.0
    assert end_s == 530.0

    # 2. Verify MusicBrainz fetch_album_tracklist returns result dictionary (not None)
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {
        "releases": [
            {
                "id": "rel-123",
                "title": "Random Access Memories",
                "date": "2013-05-17",
                "artist-credit": [{"name": "Daft Punk"}],
            }
        ]
    }
    mock_rel_resp = MagicMock()
    mock_rel_resp.json.return_value = {
        "media": [
            {
                "tracks": [
                    {"number": "1", "title": "Give Life Back to Music", "length": 275000},
                    {"number": "2", "title": "The Game of Love", "length": 322000},
                ]
            }
        ]
    }
    with patch.object(musicbrainz_client.session, "get", side_effect=[mock_search_resp, mock_rel_resp]), patch.object(
        musicbrainz_client, "_throttle"
    ):
        mb_res = musicbrainz_client.fetch_album_tracklist("Daft Punk", "Random Access Memories")
        assert mb_res is not None
        assert mb_res["album"] == "Random Access Memories"
        assert mb_res["artist"] == "Daft Punk"
        assert len(mb_res["tracks"]) == 2
        assert mb_res["tracks"][0]["title"] == "Give Life Back to Music"

    # 3. Verify Single Track / Link preview for Spotify track URL
    sp_preview = library_service.preview_single_track_or_link(
        "https://open.spotify.com/track/6OmhkSOpvYBokMKQxpIGx2"
    )
    assert len(sp_preview) == 1
    assert sp_preview[0]["source"] == "spotify"
    assert sp_preview[0]["url"] == "https://open.spotify.com/track/6OmhkSOpvYBokMKQxpIGx2"


def test_fifo_queue_pause_resume_retry():
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.services.job_service import job_service
    from musiclinker.domain.models import JobStatus, DirectDownloadRequest

    client = TestClient(app, base_url="http://127.0.0.1")

    # Start a dummy direct download request
    req = DirectDownloadRequest(query_or_url="https://www.youtube.com/watch?v=mock123")
    job = job_service.start_direct_download_job(req)
    assert job.job_id is not None
    assert job.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    # Test pause / resume controls via API
    job.status = JobStatus.RUNNING
    res_pause = client.post(f"/api/jobs/{job.job_id}/pause")
    assert res_pause.status_code == 200
    assert job.status == JobStatus.PAUSED

    res_resume = client.post(f"/api/jobs/{job.job_id}/resume")
    assert res_resume.status_code == 200
    assert job.status == JobStatus.RUNNING

    # Test retry control
    job.status = JobStatus.FAILED
    res_retry = client.post(f"/api/jobs/{job.job_id}/retry")
    assert res_retry.status_code == 200
    assert job.status == JobStatus.QUEUED

    # Clean up job
    job_service.cancel_job(job.job_id)


def test_consistent_album_release_year():
    from unittest.mock import MagicMock, patch
    from pathlib import Path
    from musiclinker.services.job_service import job_service, JobRecord
    from musiclinker.domain.models import JobStatus

    job = JobRecord(job_id="testyr01", job_type="album_download", status=JobStatus.RUNNING)

    # Verify that when album_year_override is provided, the track receives this year
    with patch("musiclinker.infrastructure.youtube_client.youtube_client.download_mp3_to_staging") as mock_dl, \
         patch("musiclinker.infrastructure.tagger.audio_tagger.write_id3v24_tags") as mock_tag, \
         patch("musiclinker.infrastructure.lyrics_client.lyrics_client.fetch_and_save_lrc") as mock_lrc, \
         patch("os.replace"):

        tmp_mp3 = Path("temp_test.mp3")
        mock_dl.return_value = (tmp_mp3, {"title": "Track 1", "duration": 180, "uploader": "Artist"}, "")
        mock_tag.return_value = (180, 320)
        mock_lrc.return_value = ("[00:01.00] Line", "synced", "LRCLIB synced")

        entity = job_service._acquire_and_tag_single_track(
            job=job,
            artist_raw="Artist A",
            title_raw="Song One",
            album_hint="Canonical Album",
            track_number_hint=1,
            album_year_override="1999",
            query_or_url="https://youtube.com/watch?v=1",
            playcount=10,
            bitrate_kbps=320,
            staging_dir=Path("."),
        )

        assert entity.release_year == "1999"
        assert entity.original_year == "1999"
        # Verify write_id3v24_tags received release_year="1999"
        called_args = mock_tag.call_args[1]
        assert called_args["release_year"] == "1999"
        assert called_args["original_year"] == "1999"


def test_playlist_crud_and_tracks():
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.db import db_repo

    client = TestClient(app, base_url="http://127.0.0.1")

    # Create mock tracks in db
    t1 = db_repo.upsert_track(
        track_hash="hash_pl_1",
        title="Synth Song 1",
        artist_display="Artist 1",
        artists=["Artist 1"],
        album_artist="Artist 1",
        album="Album 1",
        release_year="2020",
        original_year="2020",
        duration=200,
        file_path="/downloads/Artist 1/Synth Song 1.mp3",
    )
    t2 = db_repo.upsert_track(
        track_hash="hash_pl_2",
        title="Synth Song 2",
        artist_display="Artist 2",
        artists=["Artist 2"],
        album_artist="Artist 2",
        album="Album 2",
        release_year="2021",
        original_year="2021",
        duration=210,
        file_path="/downloads/Artist 2/Synth Song 2.mp3",
    )

    # 1. Create a playlist
    res = client.post("/api/playlists", json={
        "name": "My Synthwave Mix",
        "description": "Retro driving beats"
    })
    assert res.status_code == 200
    pl = res.json()
    pl_id = pl["id"]
    assert pl["name"] == "My Synthwave Mix"
    assert pl["track_count"] == 0

    # 2. Add tracks to playlist
    res_tracks = client.post(f"/api/playlists/{pl_id}/tracks", json={
        "track_ids": [t1.id, t2.id]
    })
    assert res_tracks.status_code == 200
    updated_pl = res_tracks.json()
    assert len(updated_pl["tracks"]) == 2

    # 3. Update playlist details
    res_update = client.put(f"/api/playlists/{pl_id}", json={
        "name": "Synthwave Favorites",
        "description": "Updated description"
    })
    assert res_update.status_code == 200
    assert res_update.json()["name"] == "Synthwave Favorites"

    # 4. Remove a track
    res_rm = client.delete(f"/api/playlists/{pl_id}/tracks/{t1.id}")
    assert res_rm.status_code == 200
    after_rm = res_rm.json()
    assert len(after_rm["tracks"]) == 1
    assert after_rm["tracks"][0]["id"] == t2.id

    # 5. List playlists
    res_list = client.get("/api/playlists")
    assert res_list.status_code == 200
    playlists = res_list.json()["playlists"]
    assert any(p["id"] == pl_id for p in playlists)

    # 6. Delete playlist
    res_del = client.delete(f"/api/playlists/{pl_id}")
    assert res_del.status_code == 200

    # Verify 404 after deletion
    res_get = client.get(f"/api/playlists/{pl_id}")
    assert res_get.status_code == 404


def test_resolve_best_audio_match_accuracy():
    from unittest.mock import patch
    from musiclinker.infrastructure.youtube_client import youtube_client

    mock_candidates = [
        # Candidate 1: Wrong song by the same artist (the artist's big hit), duration 180s
        {
            "id": "wrong_hit",
            "title": "Pink Floyd - Comfortably Numb (Official Video)",
            "uploader": "Pink Floyd - Topic",
            "duration": 380,
            "url": "https://www.youtube.com/watch?v=wrong_hit",
        },
        # Candidate 2: Completely random song from another artist
        {
            "id": "random_song",
            "title": "Random Artist - Money Trees",
            "uploader": "Random Artist - Topic",
            "duration": 210,
            "url": "https://www.youtube.com/watch?v=random_song",
        },
        # Candidate 3: The exact correct track with expected duration ~382s
        {
            "id": "correct_match",
            "title": "Pink Floyd - Money (Official Audio)",
            "uploader": "Pink Floyd - Topic",
            "duration": 382,
            "url": "https://www.youtube.com/watch?v=correct_match",
        },
    ]

    with patch.object(youtube_client, "search_videos", return_value=mock_candidates):
        with patch.object(youtube_client, "_get_base_ydl_opts", return_value={}):
            match = youtube_client.resolve_best_audio_match(
                artist="Pink Floyd",
                title="Money",
                preferred_method="spotify_match",
                expected_duration=382,
            )
            assert match["id"] == "correct_match"
            assert match["title"] == "Pink Floyd - Money (Official Audio)"


def test_shift_lrc_timestamps():
    from musiclinker.infrastructure.lyrics_client import lyrics_client

    sample_lrc = (
        "[x-ml-sync-mode:exact_lrc]\n"
        "[00:01.00] First line\n"
        "[01:23.45] Middle line\n"
        "[02:00.00] Last line\n"
    )

    # Positive shift (+0.5s)
    shifted_pos = lyrics_client.shift_lrc_timestamps(sample_lrc, 0.5)
    assert "[00:01.50] First line" in shifted_pos
    assert "[01:23.95] Middle line" in shifted_pos
    assert "[02:00.50] Last line" in shifted_pos
    assert "[x-ml-sync-mode:exact_lrc]" in shifted_pos

    # Negative shift (-0.5s)
    shifted_neg = lyrics_client.shift_lrc_timestamps(sample_lrc, -0.5)
    assert "[00:00.50] First line" in shifted_neg
    assert "[01:22.95] Middle line" in shifted_neg
    assert "[01:59.50] Last line" in shifted_neg

    # Clamp at 0.0s
    shifted_clamp = lyrics_client.shift_lrc_timestamps(sample_lrc, -5.0)
    assert "[00:00.00] First line" in shifted_clamp


def test_permanently_shift_track_lyrics(tmp_path):
    from unittest.mock import patch, MagicMock
    from musiclinker.services.library_service import LibraryService
    from musiclinker.domain.models import TrackEntity

    fake_lrc = tmp_path / "song.lrc"
    fake_lrc.write_text("[00:10.00] Hello world\n[00:20.00] Second line\n", encoding="utf-8")
    fake_mp3 = tmp_path / "song.mp3"
    fake_mp3.write_bytes(b"fake-mp3-bytes")

    mock_track = TrackEntity(
        id=99,
        track_hash="abcdef1234567890",
        title="Test Song",
        artist_display="Test Artist",
        artists=["Test Artist"],
        album_artist="Test Artist",
        album="Test Album",
        track_number=1,
        release_year="2024",
        original_year="2024",
        duration=180,
        file_path=str(fake_mp3),
        lrc_path=str(fake_lrc),
        lyric_sync_mode="exact_lrc",
        cover_mime="image/jpeg",
        has_cover=False,
        lastfm_playcount=0,
        local_playcount=0,
        replaygain_db=0.0,
        bitrate_kbps=320,
    )

    mock_repo = MagicMock()
    mock_repo.get_track.return_value = mock_track

    with patch("musiclinker.services.library_service.audio_tagger.update_mp3_lyrics") as mock_tagger:
        service = LibraryService(repo=mock_repo)
        result = service.permanently_shift_track_lyrics(99, 1.5)
        assert result["permanently_saved"] is True
        assert result["shift_applied"] == 1.5
        assert result["lines"][0]["time"] == 11.5
        assert result["lines"][1]["time"] == 21.5

        # Check LRC file was updated on disk
        updated_lrc_content = fake_lrc.read_text(encoding="utf-8")
        assert "[00:11.50] Hello world" in updated_lrc_content
        assert "[00:21.50] Second line" in updated_lrc_content

        # Verify ID3 tagger was called with new shifted lyrics
        mock_tagger.assert_called_once_with(fake_mp3, updated_lrc_content)


def test_audio_candidates_and_replacement_endpoints(tmp_path: Path):
    from unittest.mock import MagicMock, patch
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.domain.models import TrackEntity
    from musiclinker.infrastructure.db import db_repo
    from musiclinker.services.library_service import library_service

    client = TestClient(app, base_url="http://127.0.0.1")

    fake_file = tmp_path / "song.mp3"
    fake_file.write_bytes(b"ID3" + b"\x00" * 100)

    track = TrackEntity(
        id=77,
        track_hash="hash77",
        title="Get Lucky",
        artist_display="Daft Punk",
        artists=["Daft Punk"],
        album_artist="Daft Punk",
        album="Random Access Memories",
        track_number=8,
        release_year="2013",
        original_year="2013",
        duration=248,
        file_path=str(fake_file),
        lrc_path="",
        lyric_sync_mode="none",
        cover_mime="",
        has_cover=False,
        lastfm_playcount=0,
        local_playcount=0,
        replaygain_db=0.0,
        bitrate_kbps=320,
    )

    with patch.object(db_repo, "get_track", return_value=track):
        # 1. Test get candidates endpoint
        with patch.object(
            library_service,
            "get_track_audio_candidates",
            return_value=[
                {
                    "title": "Daft Punk - Get Lucky (Official Audio) ft. Pharrell Williams",
                    "url": "https://www.youtube.com/watch?v=5NV6Rdv1a3I",
                    "video_id": "5NV6Rdv1a3I",
                    "duration": 248,
                    "duration_diff_sec": 0,
                    "channel": "DaftPunkVEVO",
                }
            ],
        ):
            res = client.get("/api/tracks/77/candidates")
            assert res.status_code == 200
            data = res.json()
            assert len(data["candidates"]) == 1
            assert data["candidates"][0]["video_id"] == "5NV6Rdv1a3I"

        # 2. Test candidate preview snippet endpoint
        fake_snippet = tmp_path / "snippet.mp3"
        fake_snippet.write_bytes(b"ID3" + b"\x00" * 50)
        with patch.object(library_service, "generate_audio_snippet", return_value=fake_snippet):
            res_preview = client.get("/api/tracks/candidate-preview/5NV6Rdv1a3I")
            assert res_preview.status_code == 200
            assert res_preview.headers["content-type"] == "audio/mpeg"

        # 3. Test replace track audio endpoint
        with patch.object(library_service, "replace_track_audio", return_value=track):
            res_replace = client.post(
                "/api/tracks/77/replace-audio",
                json={"youtube_url": "https://www.youtube.com/watch?v=5NV6Rdv1a3I", "bitrate_kbps": 320},
            )
            assert res_replace.status_code == 200
            assert res_replace.json()["status"] == "success"


def test_bulk_album_matching_and_assignment_endpoints(tmp_path: Path):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.services.library_service import library_service

    client = TestClient(app, base_url="http://127.0.0.1")

    # 1. Test match album tracklist
    mock_match_result = {
        "album": "Discovery",
        "artist": "Daft Punk",
        "official_tracks": [
            {"track_number": 1, "title": "One More Time", "duration": 320},
            {"track_number": 2, "title": "Aerodynamic", "duration": 207},
        ],
        "assignments": [
            {
                "track_id": 101,
                "current_title": "One More Time (Radio Edit)",
                "current_artist": "Daft Punk",
                "track_number": 1,
                "confidence": 0.85,
            }
        ],
    }
    with patch.object(library_service, "match_tracks_to_album", return_value=mock_match_result):
        res = client.post(
            "/api/albums/match-tracklist",
            json={"album": "Discovery", "artist": "Daft Punk", "track_ids": [101]},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["album"] == "Discovery"
        assert len(data["assignments"]) == 1
        assert data["assignments"][0]["track_number"] == 1

    # 2. Test bulk assign album
    mock_assign_result = {
        "album": "Discovery",
        "album_artist": "Daft Punk",
        "release_year": 2001,
        "updated_tracks": [{"id": 101, "track_number": 1, "title": "One More Time"}],
    }
    with patch.object(library_service, "bulk_assign_album", return_value=mock_assign_result):
        res_assign = client.post(
            "/api/tracks/bulk-assign-album",
            json={
                "album": "Discovery",
                "album_artist": "Daft Punk",
                "release_year": 2001,
                "track_assignments": [{"track_id": 101, "track_number": 1, "title": "One More Time"}],
            },
        )
        assert res_assign.status_code == 200
        assert len(res_assign.json()["updated_tracks"]) == 1


def test_lastfm_scrobbler_and_candidate_query_features():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.lastfm_client import LastFMClient, DEFAULT_LASTFM_API_KEY, DEFAULT_LASTFM_API_SECRET
    from musiclinker.infrastructure.db import db_repo

    # 1. Verify default imbued Last.fm credentials
    client_default = LastFMClient()
    assert client_default.api_key == DEFAULT_LASTFM_API_KEY
    assert client_default.api_secret == DEFAULT_LASTFM_API_SECRET

    test_client = TestClient(app, base_url="http://127.0.0.1")

    # 2. Test settings retrieval & update for Last.fm toggles
    res_set = test_client.put(
        "/api/settings",
        json={"lastfm_scrobble_enabled": True, "lastfm_nowplaying_enabled": False},
    )
    assert res_set.status_code == 200
    data = res_set.json()
    assert data["lastfm_scrobble_enabled"] is True
    assert data["lastfm_nowplaying_enabled"] is False

    # 3. Test Last.fm disconnect endpoint
    db_repo.update_settings({"lastfm_session_key": "dummy_session", "lastfm_username": "musicfan"})
    res_dc = test_client.post("/api/lastfm/disconnect")
    assert res_dc.status_code == 200
    cfg = db_repo.get_settings()
    assert cfg.get("lastfm_session_key") == ""

    # 4. Test Last.fm scrobble pending flush endpoint
    res_flush = test_client.post("/api/lastfm/scrobble-pending")
    assert res_flush.status_code == 200
    assert "flushed" in res_flush.json()


def test_v312_lastfm_polling_and_candidate_album(tmp_path):
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from musiclinker.api.app import app
    from musiclinker.infrastructure.lastfm_client import LastFMClient, LastFMAuthError
    from musiclinker.infrastructure.db import db_repo
    from musiclinker.services.library_service import library_service

    client = LastFMClient()

    # 1. Test LastFMClient.check_or_exchange_token when token is pending approval (error 14)
    with patch.object(client, "exchange_token_for_session", side_effect=LastFMAuthError("Last.fm authentication error (14): This token has not been authorized")):
        res = client.check_or_exchange_token("test_token_123")
        assert res["authenticated"] is False
        assert res["status"] == "pending"

    # 2. Test LastFMClient.check_or_exchange_token when user approved
    with patch.object(client, "exchange_token_for_session", return_value={"session_key": "sess_abc", "username": "audiophile"}):
        res_ok = client.check_or_exchange_token("test_token_123")
        assert res_ok["authenticated"] is True
        assert res_ok["status"] == "connected"
        assert res_ok["session_key"] == "sess_abc"

    # 3. Test API endpoint /api/lastfm/auth/complete returns pending cleanly
    test_client = TestClient(app, base_url="http://127.0.0.1")
    with patch("musiclinker.api.app.LastFMClient") as mock_lfm:
        instance = MagicMock()
        instance.check_or_exchange_token.return_value = {
            "authenticated": False,
            "status": "pending",
            "message": "Authorization pending. Please approve access in your browser.",
        }
        mock_lfm.return_value = instance

        resp = test_client.post("/api/lastfm/auth/complete?token=test_pending")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
        assert resp.json()["status"] == "pending"

    # 4. Test candidate audio versions contains album
    fake_mp3 = tmp_path / "song.mp3"
    fake_mp3.write_bytes(b"ID3" + b"\x00" * 100)
    t = db_repo.upsert_track(
        track_hash="hash_cand_test",
        title="Aerodynamic",
        artist_display="Daft Punk",
        artists=["Daft Punk"],
        album="Discovery",
        album_artist="Daft Punk",
        duration=212,
        file_path=str(fake_mp3),
    )
    with patch("musiclinker.services.library_service.youtube_client.search_videos", return_value=[
        {"id": "yt_vid_1", "title": "Aerodynamic", "uploader": "Daft Punk - Topic", "duration": 212, "url": "https://youtube.com/watch?v=yt_vid_1", "thumbnail": "https://img.youtube.com/vi/yt_vid_1/0.jpg"}
    ]):
        cands_data = library_service.get_track_audio_candidates(t.id, custom_query="Aerodynamic", max_results=3)
        cands = cands_data["candidates"]
        assert len(cands) == 1
        assert cands[0]["album"] == "Discovery"
        assert cands[0]["thumbnail"] == "https://img.youtube.com/vi/yt_vid_1/0.jpg"
