import os
import socket
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import config
from lastfm import LastFMClient
from library import scan_library
from downloader import TrackDownloader
from hasher import TrackHashManager
from youtube import YouTubeSearcher
from lyrics import fetch_synced_lyrics

app = FastAPI(title="MusicLinker Personal Server", version="2.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.254.254.254', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

job_state: Dict[str, Any] = {
    "running": False,
    "progress": 0,
    "total": 0,
    "current_track": "",
    "status": "Idle",
    "error": None
}

class NowPlayingRequest(BaseModel):
    artist: str
    title: str
    album: Optional[str] = ""

class ScrobbleRequest(BaseModel):
    artist: str
    title: str
    album: Optional[str] = ""
    timestamp: Optional[int] = None

class ExtractJobRequest(BaseModel):
    username: str
    period: Optional[str] = "12month"
    year: Optional[int] = None
    min_plays: Optional[int] = 10
    download_cap: Optional[int] = 25
    audio_quality: Optional[str] = "192"

class LastfmAuthRequest(BaseModel):
    api_key: str
    api_secret: str

class LastfmSessionRequest(BaseModel):
    token: str

class CustomDownloadRequest(BaseModel):
    url: str
    artist: str
    title: str
    album: Optional[str] = "Singles"

class TransitionRequest(BaseModel):
    from_hash: str
    to_hash: str
    from_title: str
    to_title: str
    probability: Optional[int] = 50
    duration: Optional[float] = 4.0

@app.get("/api/status")
def get_status():
    tracks = db.get_all_tracks()
    cfg = config.load_config()
    session_key = cfg.get("lastfm_session_key", "")
    return {
        "status": "online",
        "version": "2.5",
        "tracks_count": len(tracks),
        "artists_count": len(db.get_artists_summary()),
        "albums_count": len(db.get_albums_summary()),
        "lastfm_connected": bool(session_key),
        "local_ip": get_local_ip(),
        "port": cfg.get("server_port", 8000),
        "server_url": f"http://{get_local_ip()}:{cfg.get('server_port', 8000)}"
    }

@app.get("/api/tracks")
def list_tracks():
    return db.get_all_tracks()

@app.get("/api/artists")
def list_artists():
    return db.get_artists_summary()

@app.get("/api/albums")
def list_albums():
    return db.get_albums_summary()

@app.post("/api/library/scan")
def trigger_scan():
    result = scan_library("downloads")
    return {"status": "success", "result": result}

# Lyrics Endpoint (Synced LRC)
@app.get("/api/tracks/{track_id}/lyrics")
def get_track_lyrics(track_id: int):
    track = db.get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    filepath = track.get("filepath", "")
    lrc_path = os.path.splitext(filepath)[0] + ".lrc" if filepath else None

    lyrics_data = fetch_synced_lyrics(
        artist=track["primary_artist"],
        title=track["title"],
        album=track.get("album", ""),
        duration=track.get("duration", 0),
        save_path=lrc_path
    )
    return lyrics_data

# Range-enabled Audio Streaming
@app.get("/api/tracks/{track_id}/stream")
def stream_track(track_id: int, request: Request):
    track = db.get_track_by_id(track_id)
    if not track or not os.path.exists(track["filepath"]):
        raise HTTPException(status_code=404, detail="Track file not found")

    filepath = track["filepath"]
    filesize = os.path.getsize(filepath)
    range_header = request.headers.get("range")

    if range_header:
        bytes_part = range_header.replace("bytes=", "").split("-")
        start = int(bytes_part[0])
        end = int(bytes_part[1]) if bytes_part[1] else filesize - 1
        chunk_size = (end - start) + 1

        def iterfile():
            with open(filepath, "rb") as f:
                f.seek(start)
                bytes_left = chunk_size
                while bytes_left > 0:
                    read_len = min(64 * 1024, bytes_left)
                    data = f.read(read_len)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{filesize}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "audio/mpeg",
        }
        return StreamingResponse(iterfile(), status_code=206, headers=headers)

    def iter_full():
        with open(filepath, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    return StreamingResponse(iter_full(), media_type="audio/mpeg", headers={"Content-Length": str(filesize), "Accept-Ranges": "bytes"})

@app.get("/api/tracks/{track_id}/file")
def download_track_file(track_id: int):
    track = db.get_track_by_id(track_id)
    if not track or not os.path.exists(track["filepath"]):
        raise HTTPException(status_code=404, detail="Track file not found")
    filename = os.path.basename(track["filepath"])
    return FileResponse(track["filepath"], media_type="audio/mpeg", filename=filename)

# Direct Search for Individual Songs & Albums
@app.get("/api/search")
def search_individual(q: str):
    if not q or len(q.strip()) < 2:
        return []
    searcher = YouTubeSearcher()
    return searcher.search_multiple(q.strip(), limit=6)

@app.post("/api/download/custom")
def download_custom_track(req: CustomDownloadRequest):
    downloader = TrackDownloader()
    mp3_path = downloader.download_and_tag(
        youtube_url=req.url,
        artist=req.artist,
        title=req.title,
        album=req.album or "Singles"
    )
    if mp3_path:
        # Pre-fetch lyrics
        lrc_path = os.path.splitext(mp3_path)[0] + ".lrc"
        fetch_synced_lyrics(req.artist, req.title, req.album or "", save_path=lrc_path)
        return {"status": "success", "filepath": mp3_path}
    raise HTTPException(status_code=500, detail="Download failed")

# Transition Rules Endpoints
@app.get("/api/transitions")
def list_transitions():
    return db.get_all_transitions()

@app.post("/api/transitions")
def create_transition(req: TransitionRequest):
    tid = db.add_transition(
        from_hash=req.from_hash,
        to_hash=req.to_hash,
        from_title=req.from_title,
        to_title=req.to_title,
        probability=req.probability or 50,
        duration=req.duration or 4.0
    )
    return {"status": "success", "id": tid}

@app.delete("/api/transitions/{trans_id}")
def remove_transition(trans_id: int):
    db.delete_transition(trans_id)
    return {"status": "success"}

# Player Events: Last.fm
@app.post("/api/player/now-playing")
def update_now_playing(req: NowPlayingRequest):
    creds = config.get_api_credentials()
    if creds["session_key"] and creds["api_secret"]:
        client = LastFMClient(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            session_key=creds["session_key"]
        )
        success = client.update_now_playing(req.artist, req.title, req.album or "")
        return {"success": success}
    return {"success": False, "reason": "Last.fm session key not configured"}

@app.post("/api/player/scrobble")
def scrobble_track(req: ScrobbleRequest):
    creds = config.get_api_credentials()
    client = LastFMClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        session_key=creds["session_key"]
    )
    success = client.scrobble_track(req.artist, req.title, req.album or "", req.timestamp)
    return {"success": success}

@app.post("/api/sync/scrobbles")
def sync_offline_scrobbles(scrobbles: List[ScrobbleRequest]):
    creds = config.get_api_credentials()
    client = LastFMClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        session_key=creds["session_key"]
    )
    synced = 0
    for s in scrobbles:
        if client.scrobble_track(s.artist, s.title, s.album or "", s.timestamp):
            synced += 1
    return {"synced": synced}

# Last.fm Authentication
@app.post("/api/auth/lastfm/start")
def start_lastfm_auth(req: LastfmAuthRequest):
    cfg = config.load_config()
    cfg["lastfm_api_key"] = req.api_key.strip()
    cfg["lastfm_api_secret"] = req.api_secret.strip()
    config.save_config(cfg)

    client = LastFMClient(api_key=req.api_key, api_secret=req.api_secret)
    token = client.get_auth_token()
    auth_url = client.get_auth_url(token)
    return {"token": token, "auth_url": auth_url}

@app.post("/api/auth/lastfm/complete")
def complete_lastfm_auth(req: LastfmSessionRequest):
    creds = config.get_api_credentials()
    client = LastFMClient(api_key=creds["api_key"], api_secret=creds["api_secret"])
    res = client.create_session(req.token)

    cfg = config.load_config()
    cfg["lastfm_session_key"] = res["session_key"]
    if res.get("username"):
        cfg["default_username"] = res["username"]
    config.save_config(cfg)

    return {"status": "success", "username": res.get("username")}

# Extraction Worker
def run_extraction_worker(req: ExtractJobRequest):
    global job_state
    job_state["running"] = True
    job_state["status"] = "Connecting to Last.fm..."
    job_state["progress"] = 0
    job_state["error"] = None

    try:
        creds = config.get_api_credentials()
        client = LastFMClient(api_key=creds["api_key"])
        hasher = TrackHashManager()

        if req.year:
            from datetime import datetime
            s_ts = int(datetime(req.year, 1, 1).timestamp())
            e_ts = int(datetime(req.year, 12, 31, 23, 59, 59).timestamp())
            tracks = client.get_tracks_custom_range(req.username, s_ts, e_ts, req.min_plays)
        else:
            tracks = client.get_top_tracks(req.username, req.period or "12month", req.min_plays)

        unseen = [t for t in tracks if not hasher.is_seen(t["artist"], t["title"])]
        if not unseen:
            job_state["status"] = "All tracks already in library (0 new to download)"
            job_state["running"] = False
            return

        cap = req.download_cap or 25
        to_download = unseen[:cap]
        excess = unseen[cap:]

        if excess:
            with open("excess_tracks.txt", "a", encoding="utf-8") as f:
                for item in excess:
                    f.write(f"{item['artist']} - {item['title']} | Plays: {item['plays']}\n")

        job_state["total"] = len(to_download)
        yt_searcher = YouTubeSearcher()
        downloader = TrackDownloader(audio_quality=req.audio_quality or "192")

        for idx, t in enumerate(to_download, start=1):
            job_state["current_track"] = f"{t['artist']} - {t['title']}"
            job_state["progress"] = idx
            job_state["status"] = f"Downloading & Tagging ({idx}/{len(to_download)})"

            yt_res = yt_searcher.search_track(t["artist"], t["title"])
            if yt_res and yt_res.get("url"):
                yt_url = yt_res["url"]
                hasher.mark_seen(t["artist"], t["title"], yt_url)

                album_info = client.get_track_info(t["artist"], t["title"])
                album_name = album_info.get("album") or t.get("album", "")
                cover_url = album_info.get("cover_url") or t.get("image_url", "")

                mp3_res = downloader.download_and_tag(
                    youtube_url=yt_url,
                    artist=t["artist"],
                    title=t["title"],
                    album=album_name,
                    cover_url=cover_url,
                    plays=t["plays"]
                )

                if mp3_res:
                    lrc_path = os.path.splitext(mp3_res)[0] + ".lrc"
                    fetch_synced_lyrics(t["artist"], t["title"], album_name, save_path=lrc_path)

        hasher.save()
        job_state["status"] = f"Successfully downloaded {len(to_download)} new tracks!"

    except Exception as e:
        job_state["error"] = str(e)
        job_state["status"] = f"Failed: {e}"
    finally:
        job_state["running"] = False

@app.post("/api/jobs/extract")
def start_extract_job(req: ExtractJobRequest, background_tasks: BackgroundTasks):
    global job_state
    if job_state["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(run_extraction_worker, req)
    return {"status": "started"}

@app.get("/api/jobs/status")
def get_job_status():
    return job_state

# Serve Web/PWA UI static assets
base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(base_dir, "web")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
