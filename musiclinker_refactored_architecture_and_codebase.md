# MusicLinker v2.0 — Architectural Refactoring, ADRs & Complete Implementation Blueprint

> **Agent Implementation Directive**: This document contains the complete architectural review, Architecture Decision Records (ADRs), target directory structure, migration steps, and full production-ready source code for **MusicLinker v2.0**. An autonomous coding agent can parse each `### File: <relative_path>` section below and write the contents directly to disk to construct the refactored application.

## 1. Architectural Critique & High-Impact Improvements establish a Production-Grade Ecosystem

Reviewing the v1.0 `PROJECT_OVERVIEW.md` reveals a feature-rich personal music ecosystem that outgrew its initial script-based roots. Below are the six structural bottlenecks identified in v1.0 and the architectural upgrades engineered into v2.0.

| Subsystem | v1.0 Bottleneck / Vulnerability | v2.0 Architectural Upgrade | Impact |
| :--- | :--- | :--- | :--- |
| **Package & Module Topology** | 18+ Python, batch, and config files sit in a flat root folder (`server.py`, `db.py`, `downloader.py`, `tagger.py`, `lastfm.py`, `main.py`, `app.py`), creating circular import risks and duplicated orchestration logic across CLI, FastAPI, and Streamlit. | **Layered `src/musiclinker/` Layout**: Clean separation into `core/`, `domain/`, `infrastructure/`, `services/`, `api/`, and `entrypoints/`. All UIs (Desktop/Web, CLI, Streamlit) invoke the exact same service layer. | Eliminates duplicated business logic and allows unit testing every layer in isolation. |
| **State & Persistence** | **Split-Brain Storage**: Settings are split between `config.json` and SQLite's `settings` table; track deduplication hashes live in `seen_hashes.json` while track metadata lives in `library.db`. Concurrent reads/writes across Uvicorn and `pywebview` threads risk `sqlite3.OperationalError: database is locked`. | **Unified SQLite WAL Database**: Single source of truth in `library.db` running in `WAL` mode (`PRAGMA journal_mode=WAL`) with thread-local connection pooling, schema migrations, a dedicated `seen_hashes` table, and automatic one-time import from legacy `config.json` / `seen_hashes.json`. | Zero state drift between CLI and Web UI; concurrent reads never block writes during active batch downloads. |
| **Background Jobs & UI Feedback** | Synchronous `yt-dlp`, `ffmpeg`, MusicBrainz, and LRCLIB calls inside FastAPI background tasks block worker threads, cause audio streaming jitter, and leave the UI polling blindly for progress. | **Bounded Job Executor + Server-Sent Events (SSE)**: A managed `ThreadPoolExecutor` runs extraction pipelines asynchronously and emits structured progress frames over `GET /api/jobs/{job_id}/events` with cancellation support. | Buttery-smooth HTTP 206 audio streaming even during heavy 320kbps transcoding jobs, with live per-track progress in the UI. |
| **Audio Pipeline & Tagging** | Client-side `DynamicsCompressorNode` colors audio dynamically and breaks background lockscreen playback on mobile browsers when `MediaElementAudioSourceNode` suspends with the screen off. Interrupted downloads leave corrupt partial MP3s inside `downloads/`. | **Atomic Staging + EBU R128 ReplayGain + ID3v2.4**: Downloads stage in `.staging/`, compute/embed `REPLAYGAIN_TRACK_GAIN` (`TXXX`), write null-separated multi-artist `TPE1`, `TPE2`, `TDOR` (original year), `TDRC`, `APIC` cover art, and `USLT` lyrics, then atomically `os.replace()` into `downloads/`. | Corrupt partial files never enter the library; normalization works natively across external players and mobile lockscreens. |
| **Mobile Offline Scrobbling** | Offline scrobble queueing (`scrobble_queue` table) only works when the **PC** is offline. When an Android phone plays cached songs offline on a flight or subway, plays are lost or fail to reach the PC. | **Client-Side IndexedDB Scrobble Queue + Batch Sync**: The mobile PWA stores offline scrobbles (with exact Unix play timestamps) in IndexedDB when disconnected from home Wi-Fi and flushes them in a single batch (`POST /api/player/scrobble/batch`) when reconnected to the PC or internet. | True 100% offline bi-directional Last.fm scrobbling on both PC and Android. |
| **LAN Security (`0.0.0.0:8000`)** | Binding to `0.0.0.0:8000` with no authentication exposes Last.fm session keys, configuration endpoints, and arbitrary YouTube download triggers to anyone on the same Wi-Fi network. | **Zero-Friction Localhost + QR Token Pairing**: Loopback requests (`127.0.0.1`) from the desktop WebView2 window are trusted automatically; LAN phones authenticate seamlessly via a cryptographic pairing token embedded in the QR code URL (`?pair_token=...`). | Protects API keys and server actions on shared networks while keeping phone pairing a single QR scan. |

## 2. Architecture Decision Records (ADRs) Formalize Core Engineering Choices

### ADR-001: Adoption of Clean / Layered Package Architecture (`src/musiclinker`)
* **Status**: Accepted
* **Context**: In v1.0, all modules resided in the repository root. The FastAPI server (`server.py`), CLI runner (`main.py`), and Streamlit prototype (`app.py`) directly called low-level modules (`downloader.py`, `db.py`, `lastfm.py`, `hasher.py`), making it difficult to enforce consistent error handling, retry policies, and state updates.
* **Decision**: Reorganize the codebase under `src/musiclinker/` into five strict dependency layers:
  1. `core/`: Zero-dependency utilities (paths, logging, constants, custom exceptions, event bus).
  2. `domain/`: Pure data structures (`Track`, `ExtractionRequest`, `JobProgress`, `ScrobbleRecord`, `TransitionRule`, `AppSettings`).
  3. `infrastructure/`: External adapters (`sqlite_repo.py`, `lastfm_client.py`, `ytdlp_adapter.py`, `musicbrainz_client.py`, `lrclib_client.py`, `id3_tagger.py`).
  4. `services/`: Application use-cases (`ExtractionService`, `LibraryService`, `PlaybackService`, `ScrobbleService`).
  5. `api/` & `entrypoints/`: Delivery mechanisms (FastAPI routers, WebView2 desktop host + system tray, CLI wizard, Streamlit app).
* **Consequences**: Dependencies point strictly inward (`entrypoints/api -> services -> domain <- infrastructure`). Any change to Last.fm signing, `yt-dlp` options, or SQLite schema is isolated to `infrastructure/`.

### ADR-002: Unified SQLite WAL Persistence and Legacy File Migration
* **Status**: Accepted
* **Context**: Storing deduplication hashes in `seen_hashes.json`, settings in both `config.json` and SQLite's `settings` table, and tracks in `library.db` created synchronization bugs and race conditions across threads.
* **Decision**: Consolidate all mutable application state into `library.db` using SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;`). Introduce a `schema_migrations` table, a `seen_hashes` table (indexed by normalized 16-char SHA-256 hash), and a unified `settings` table. Provide an idempotent startup migration that imports existing `config.json` and `seen_hashes.json` records into SQLite and backs up the legacy files.
* **Consequences**: Single atomic backup file (`library.db`), zero lock contention between streaming reads and download writes, and consistent configuration across Desktop, PWA, CLI, and Streamlit.

### ADR-003: Asynchronous Job Execution with Server-Sent Events (SSE)
* **Status**: Accepted
* **Context**: Extracting hundreds of Last.fm tracks, querying MusicBrainz/LRCLIB, and transcoding audio via `yt-dlp` and `ffmpeg` takes minutes. Running this synchronously or in unmanaged threads starved FastAPI request handlers and gave users no granular progress visibility.
* **Decision**: Implement a central `JobService` backed by a bounded `ThreadPoolExecutor` (`max_workers=2` for downloads to prevent YouTube HTTP 429 rate-limiting) and an in-memory ring buffer + `asyncio.Queue` pub/sub broker. Expose `GET /api/jobs/{job_id}/events` using standard Server-Sent Events (`text/event-stream`).
* **Consequences**: The frontend receives instant real-time updates (track discovery, YouTube matching, FFmpeg conversion, ID3 tagging, lyrics saving) without polling overhead, and users can cancel active jobs cleanly via `POST /api/jobs/{job_id}/cancel`.

### ADR-004: Atomic Staging, Multi-Artist ID3v2.4 (`\x00`), and ReplayGain Tagging
* **Status**: Accepted
* **Context**: Direct downloads into `downloads/<Artist>/<Album>/` left incomplete files on crash. Furthermore, single-string artist tags broke multi-artist indexing in modern players, and browser-only `DynamicsCompressorNode` normalization failed when mobile browsers backgrounded audio playback.
* **Decision**:
  1. Download and transcode all audio inside `downloads/.staging/<job_id>/`.
  2. Parse artist strings (`feat.`, `ft.`, `&`, `;`, `with`) into a primary `album_artist` (`TPE2`) and a multi-valued list of `artists` written to ID3v2.4 `TPE1` (which `mutagen` serializes using standard null-byte `\x00` delimiters).
  3. Query MusicBrainz (with strict 1 req/sec rate-limiting and local caching) to distinguish Original Release Year (`TDOR`) from Remaster/Release Date (`TDRC`).
  4. Calculate/write ReplayGain metadata (`TXXX:REPLAYGAIN_TRACK_GAIN`) and save synced lyrics both inside `USLT` frames and sidecar `.lrc` files.
  5. Atomically move (`os.replace`) the completed `.mp3` and `.lrc` into `downloads/<SanitizedAlbumArtist>/<SanitizedAlbum>/<SanitizedArtist> - <SanitizedTitle>.mp3`.
* **Consequences**: Crash-proof library directory, audiophile-grade metadata compatible with Navidrome/Plex/Symfonium/Poweramp, and non-destructive loudness normalization.

### ADR-005: Loopback Auto-Trust & QR Token Authentication for LAN Sync
* **Status**: Accepted
* **Context**: The server must listen on `0.0.0.0:8000` so Android phones on home Wi-Fi can stream and cache music, but open LAN ports risk unauthorized access on untrusted networks.
* **Decision**: Generate a persistent 256-bit `lan_pair_token` stored in SQLite `settings`. Requests originating from `127.0.0.1` or `::1` bypass token checks so the Windows WebView2 app works automatically. The QR code displayed in the Desktop UI encodes `http://<LAN_IP>:8000/?pair_token=<token>`. When the phone opens this URL, the server sets an `HttpOnly`, `SameSite=Lax` session cookie (`ml_session`) and the PWA also caches the token in `localStorage` for API requests.
* **Consequences**: Zero-friction UX for the user (scan QR code and play) while blocking unauthenticated LAN devices from reading API keys or triggering jobs.

### ADR-006: Modular ES6 Frontend with Client-Side IndexedDB Scrobble Queue
* **Status**: Accepted
* **Context**: A single monolithic `web/index.html` is difficult to maintain, and mobile offline playback previously could not queue scrobbles locally when disconnected from the PC server.
* **Decision**: Split the frontend into native ES6 modules (`web/js/app.js`, `web/js/api.js`, `web/js/audio-engine.js`, `web/js/offline-store.js`, `web/js/lyrics.js`) requiring zero Node.js build step. Use `IndexedDB` (`musiclinker_pwa_db`) on the client to track offline-cached track metadata and store an `offline_scrobbles` outbox. Whenever the PWA regains connectivity to the PC server, it flushes the outbox to `POST /api/player/scrobble/batch`.
* **Consequences**: Clean frontend separation of concerns without adding Webpack/Vite build complexity, and guaranteed scrobble retention when listening offline on Android.

## 3. Target Folder Structure & File Manifest Map Every Component

Below is the complete target repository layout. Every file listed in the table is provided in full in Section 5.

| File Path | Architectural Layer | Responsibility |
| :--- | :--- | :--- |
| `pyproject.toml` | Packaging & Build | PEP 621 project metadata, dependencies, and CLI script entrypoints (`musiclinker-desktop`, `musiclinker-cli`). |
| `.gitignore` | Configuration | Excludes `.venv/`, `build/`, `dist/`, `.app_data/`, `downloads/`, `library.db*`, and secrets. |
| `src/musiclinker/__init__.py` | Package Root | Exposes package version (`2.0.0`) and metadata. |
| `src/musiclinker/core/__init__.py` | Core Layer | Package marker for core utilities. |
| `src/musiclinker/core/paths.py` | Core Layer | Frozen-aware (`sys._MEIPASS`) path resolver for assets, database, staging, downloads, and WebView2 cache. |
| `src/musiclinker/core/logging_config.py` | Core Layer | UTF-8 safe structured logging configuration (prevents Windows CP1252 charmap crashes). |
| `src/musiclinker/core/exceptions.py` | Core Layer | Domain and infrastructure exception hierarchy (`MusicLinkerError`, `LastFMAuthError`, `DownloadError`). |
| `src/musiclinker/core/security.py` | Core Layer | LAN IP discovery, QR code SVG generator, and loopback/token authentication verifier. |
| `src/musiclinker/domain/__init__.py` | Domain Layer | Package marker for domain models. |
| `src/musiclinker/domain/models.py` | Domain Layer | Dataclasses & Pydantic schemas for `Track`, `ScrobbleItem`, `TransitionRule`, `JobState`, and `ExtractionParams`. |
| `src/musiclinker/infrastructure/__init__.py` | Infrastructure Layer | Package marker for external adapters. |
| `src/musiclinker/infrastructure/db.py` | Infrastructure Layer | Thread-safe SQLite WAL repository, schema migrations, legacy `config.json`/`seen_hashes.json` importer, and CRUD methods. |
| `src/musiclinker/infrastructure/hasher.py` | Infrastructure Layer | Deterministic track title/artist normalizer (strips remaster/featuring noise) and 16-char SHA-256 hash generator. |
| `src/musiclinker/infrastructure/lastfm_client.py` | Infrastructure Layer | Bi-directional Last.fm client (Top Tracks, Recent Tracks date-range aggregator, Web Auth MD5 `api_sig`, Now Playing, batch Scrobble). |
| `src/musiclinker/infrastructure/youtube_client.py` | Infrastructure Layer | Keyless `yt-dlp` search adapter (`ytsearch1:` / `ytsearch5:`) and `imageio_ffmpeg` audio stream extractor. |
| `src/musiclinker/infrastructure/musicbrainz_client.py` | Infrastructure Layer | Rate-limited MusicBrainz API client resolving original release year (`TDOR`), remaster year (`TDRC`), and cover art URLs. |
| `src/musiclinker/infrastructure/lyrics_client.py` | Infrastructure Layer | LRCLIB synced karaoke lyrics fetcher, `.lrc` timestamp parser, and local sidecar file reader/writer. |
| `src/musiclinker/infrastructure/tagger.py` | Infrastructure Layer | ID3v2.4 multi-artist (`\x00` `TPE1`), `TPE2`, `TDOR`, `TDRC`, ReplayGain (`TXXX`), `USLT`, and `APIC` cover art tagger. |
| `src/musiclinker/services/__init__.py` | Service Layer | Package marker for application services. |
| `src/musiclinker/services/library_service.py` | Service Layer | Filesystem-to-SQLite synchronizer, track streaming byte-range resolver, and transition rule manager. |
| `src/musiclinker/services/scrobble_service.py` | Service Layer | Real-time "Now Playing" dispatcher, 50%/4-min scrobble validator, and background offline queue flusher. |
| `src/musiclinker/services/job_service.py` | Service Layer | Bounded `ThreadPoolExecutor` extraction/download pipeline with real-time SSE event broadcasting and download caps. |
| `src/musiclinker/api/__init__.py` | API Layer | Package marker for FastAPI layer. |
| `src/musiclinker/api/app.py` | API Layer | FastAPI application factory, lifespan manager, CORS/auth middleware, HTTP 206 range streaming, and API endpoints. |
| `src/musiclinker/entrypoints/__init__.py` | Entrypoints Layer | Package marker for runtime entrypoints. |
| `src/musiclinker/entrypoints/desktop.py` | Entrypoints Layer | Native Windows application host (`pywebview` Edge WebView2 + decoupled `pystray` system tray media controls). |
| `src/musiclinker/entrypoints/cli.py` | Entrypoints Layer | Interactive CLI wizard and headless automation runner (`--username`, `--period`, `--min-plays`, `--cap`, `--download`). |
| `src/musiclinker/entrypoints/streamlit_app.py` | Entrypoints Layer | Refactored Streamlit data inspection dashboard powered by the shared service layer. |
| `src/musiclinker/web/manifest.json` | Web / PWA Layer | Android & Desktop Progressive Web App manifest. |
| `src/musiclinker/web/icon.svg` | Web / PWA Layer | Scalable vector branding icon for PWA and tray fallback. |
| `src/musiclinker/web/sw.js` | Web / PWA Layer | Service Worker implementing shell caching and `/api/tracks/{id}/file` offline audio caching. |
| `src/musiclinker/web/index.html` | Web / PWA Layer | Responsive Spotify-dark SPA with Car Driving Mode, Karaoke Lyrics drawer, QR Phone Sync modal, and Extraction studio. |
| `src/musiclinker/web/js/api.js` | Web / PWA Layer | Authenticated fetch wrapper, SSE job stream client, and LAN pairing token handler. |
| `src/musiclinker/web/js/offline-store.js` | Web / PWA Layer | IndexedDB manager for offline cached track records and client-side offline scrobble queue. |
| `src/musiclinker/web/js/audio-engine.js` | Web / PWA Layer | HTML5 Audio + Web Audio API gain/normalization engine, custom track transition resolver, and `MediaSession` bridge. |
| `src/musiclinker/web/js/app.js` | Web / PWA Layer | UI state controller connecting playback, synced `.lrc` karaoke scroll, wake-lock Car Mode, and extraction jobs. |
| `start_player.py` | Root Bootstrap | Backwards-compatible root launcher invoking `musiclinker.entrypoints.desktop:main`. |
| `MusicLinker.spec` | Packaging | Updated PyInstaller v6.x spec bundling `src/musiclinker/web`, `imageio_ffmpeg`, and icons into `MusicLinker.exe`. |
| `start_player.bat` | Windows Launcher | 1-click launcher for Desktop App. |
| `run_cli.bat` | Windows Launcher | 1-click launcher for Interactive CLI Wizard. |
| `run_web.bat` | Windows Launcher | 1-click launcher for Streamlit Dashboard. |

## 4. Step-by-Step Agent Migration Instructions Ensure a Clean Transition

When instructing your coding agent to apply this refactoring to your existing repository, give it the following execution steps:

1. **Create Directory Hierarchy**: Create `src/musiclinker/{core,domain,infrastructure,services,api,entrypoints,web/js}`.
2. **Preserve Existing Data**: Keep `library.db`, `config.json`, `seen_hashes.json` (if present), `downloads/`, `icon.ico`, and `icon.png` in the repository root. On first launch, `src/musiclinker/infrastructure/db.py` automatically migrates `config.json` and `seen_hashes.json` into the upgraded `library.db` schema without losing a single track, hash, or API key.
3. **Write Source Files**: Create every file listed in Section 5 using its exact relative path.
4. **Install / Link Package in Editable Mode**: Run `py -m pip install -e .` (or run directly via `py start_player.py`, which automatically adds `src/` to `sys.path`).
5. **Remove Superseded Root Modules**: Once verified, delete the old root-level `db.py`, `downloader.py`, `hasher.py`, `lastfm.py`, `library.py`, `lyrics.py`, `server.py`, `tagger.py`, `youtube.py`, `config.py`, `desktop_app.py`, `main.py`, `app.py`, and root `web/` folder.

## 5. Complete Refactored Codebase Provides Every Module in Full

### File: `pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "musiclinker"
version = "2.0.0"
description = "Offline-first Last.fm extractor, ID3v2.4 multi-artist tagger, and Windows/Android personal music server"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "FabriThomas" }]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.9.0",
    "yt-dlp>=2024.10.0",
    "imageio-ffmpeg>=0.5.1",
    "mutagen>=1.47.0",
    "pywebview>=5.2",
    "pystray>=0.19.5",
    "Pillow>=10.4.0",
    "requests>=2.32.0",
]

[project.optional-dependencies]
dashboard = ["streamlit>=1.39.0", "pandas>=2.2.0"]
dev = ["pytest>=8.3.0", "pyinstaller>=6.10.0"]

[project.scripts]
musiclinker = "musiclinker.entrypoints.desktop:main"
musiclinker-cli = "musiclinker.entrypoints.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"musiclinker.web" = ["*.*", "js/*.*"]
```

### File: `.gitignore`
```gitignore
# Byte-compiled / cache
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/

# Virtual environments
.venv/
venv/
env/

# Build & distribution
build/
dist/
*.egg-info/

# Runtime & user data (never commit secrets or media)
config.json
config.json.bak
seen_hashes.json
seen_hashes.json.bak
library.db
library.db-shm
library.db-wal
downloads/
.webview_cache/
.app_data/
logs/
```

### File: `src/musiclinker/__init__.py`
```python
"""MusicLinker: Offline-first Last.fm extractor and personal music streaming ecosystem."""

__version__ = "2.0.0"
__all__ = ["__version__"]
```

### File: `src/musiclinker/core/__init__.py`
```python
"""Core utilities, path resolution, logging, security, and custom exceptions."""
```

### File: `src/musiclinker/core/paths.py`
```python
"""Centralized, PyInstaller-aware filesystem path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AppPaths:
    """Resolves application directories across source execution and PyInstaller frozen builds."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is not None:
            self.root_dir = base_dir.resolve()
        elif getattr(sys, "frozen", False):
            self.root_dir = Path(sys.executable).resolve().parent
        else:
            # src/musiclinker/core/paths.py -> repo root is 3 levels up
            self.root_dir = Path(__file__).resolve().parents[3]

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            self.bundle_dir = Path(getattr(sys, "_MEIPASS")).resolve()
            self.web_dir = self.bundle_dir / "musiclinker" / "web"
        else:
            self.bundle_dir = Path(__file__).resolve().parents[1]
            self.web_dir = self.bundle_dir / "web"

        self.app_data_dir = self.root_dir / ".app_data"
        self.webview_cache_dir = self.app_data_dir / "webview_cache"
        self.logs_dir = self.app_data_dir / "logs"
        self.downloads_dir = self.root_dir / "downloads"
        self.staging_dir = self.downloads_dir / ".staging"
        self.excess_lists_dir = self.downloads_dir / "_excess_lists"

        self.db_path = self.root_dir / "library.db"
        self.legacy_config_path = self.root_dir / "config.json"
        self.legacy_hashes_path = self.root_dir / "seen_hashes.json"
        self.icon_ico_path = self._resolve_asset("icon.ico")
        self.icon_png_path = self._resolve_asset("icon.png")

    def _resolve_asset(self, filename: str) -> Path:
        candidate_root = self.root_dir / filename
        if candidate_root.exists():
            return candidate_root
        return self.bundle_dir / filename

    def ensure_directories(self) -> None:
        """Create required runtime directories if they do not exist."""
        for directory in (
            self.app_data_dir,
            self.webview_cache_dir,
            self.logs_dir,
            self.downloads_dir,
            self.staging_dir,
            self.excess_lists_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


PATHS = AppPaths(
    Path(os.environ["MUSICLINKER_HOME"]) if "MUSICLINKER_HOME" in os.environ else None
)
PATHS.ensure_directories()
```

### File: `src/musiclinker/core/logging_config.py`
```python
"""UTF-8 safe logging setup preventing Windows CP1252 console crashes."""

from __future__ import annotations

import io
import logging
import sys
from logging.handlers import RotatingFileHandler

from musiclinker.core.paths import PATHS


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root and application loggers with UTF-8 encoding and file rotation."""
    # Reconfigure standard streams on Windows to prevent CP1252 UnicodeEncodeError
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        elif stream is not None and hasattr(stream, "buffer"):
            try:
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                pass

    logger = logging.getLogger("musiclinker")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        PATHS.logs_dir / "musiclinker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


logger = configure_logging()
```

### File: `src/musiclinker/core/exceptions.py`
```python
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
```

### File: `src/musiclinker/core/security.py`
```python
"""LAN IP resolution, pairing token validation, and zero-dependency SVG QR generator."""

from __future__ import annotations

import hmac
import secrets
import socket
from urllib.parse import quote

from fastapi import HTTPException, Request, status


def detect_lan_ip() -> str:
    """Detect the primary LAN IPv4 address of the host machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            # Connect to a non-routable multicast/public DNS address without sending packets
            sock.connect(("10.255.255.255", 1))
            ip_addr = sock.getsockname()[0]
            if ip_addr and not ip_addr.startswith("127."):
                return ip_addr
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for addr in socket.gethostbyname_ex(hostname)[2]:
            if not addr.startswith("127."):
                return addr
    except Exception:
        pass
    return "127.0.0.1"


def generate_pair_token() -> str:
    """Generate a cryptographically random 32-character URL-safe pairing token."""
    return secrets.token_urlsafe(24)


def is_loopback_host(client_host: str | None) -> bool:
    """Return True if the request originates from the local machine."""
    if not client_host:
        return False
    return client_host in ("127.0.0.1", "::1", "localhost") or client_host.startswith("127.")


def verify_request_access(request: Request, expected_token: str, require_auth_on_lan: bool = True) -> None:
    """Allow loopback traffic automatically; validate pairing token for LAN devices."""
    client_host = request.client.host if request.client else None
    if is_loopback_host(client_host) or not require_auth_on_lan:
        return

    provided = (
        request.query_params.get("pair_token")
        or request.headers.get("X-Pair-Token")
        or request.cookies.get("ml_pair_token")
        or ""
    )
    if not provided or not hmac.compare_digest(provided, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LAN pairing token required. Scan the QR code inside the MusicLinker Desktop App.",
        )


def build_pairing_url(port: int, pair_token: str) -> str:
    """Construct the full LAN URL encoded inside the phone pairing QR code."""
    lan_ip = detect_lan_ip()
    return f"http://{lan_ip}:{port}/?pair_token={quote(pair_token)}"
```

### File: `src/musiclinker/domain/__init__.py`
```python
"""Domain entities and value objects."""
```

### File: `src/musiclinker/domain/models.py`
```python
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
```

### File: `src/musiclinker/infrastructure/__init__.py`
```python
"""External infrastructure adapters: SQLite WAL, Last.fm, yt-dlp, MusicBrainz, LRCLIB, ID3v2.4."""
```

### File: `src/musiclinker/infrastructure/hasher.py`
```python
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
```

### File: `src/musiclinker/infrastructure/db.py`
```python
"""Thread-safe SQLite WAL repository with automated schema migrations and legacy file import."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import generate_pair_token
from musiclinker.domain.models import TrackEntity
from musiclinker.infrastructure.hasher import compute_track_hash

DEFAULT_SETTINGS: dict[str, Any] = {
    "lastfm_api_key": os.environ.get("LASTFM_API_KEY", ""),
    "lastfm_api_secret": os.environ.get("LASTFM_API_SECRET", ""),
    "lastfm_username": os.environ.get("LASTFM_USERNAME", ""),
    "lastfm_session_key": os.environ.get("LASTFM_SESSION_KEY", ""),
    "default_bitrate_kbps": 320,
    "default_min_plays": 10,
    "default_download_cap": 25,
    "require_lan_token": True,
    "normalize_audio": True,
    "lan_pair_token": generate_pair_token(),
}


class DatabaseRepository:
    """Thread-local SQLite WAL repository managing tracks, seen hashes, scrobbles, transitions, and settings."""

    def __init__(self, db_path: Path = PATHS.db_path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._initialize_schema()
        self._migrate_legacy_files()

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return conn

    def _initialize_schema(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_hash TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    artist_display TEXT NOT NULL,
                    artists_json TEXT NOT NULL DEFAULT '[]',
                    album_artist TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT '',
                    release_year TEXT NOT NULL DEFAULT '',
                    original_year TEXT NOT NULL DEFAULT '',
                    duration INTEGER NOT NULL DEFAULT 0,
                    file_path TEXT NOT NULL UNIQUE,
                    lrc_path TEXT NOT NULL DEFAULT '',
                    cover_mime TEXT NOT NULL DEFAULT '',
                    has_cover INTEGER NOT NULL DEFAULT 0,
                    lastfm_playcount INTEGER NOT NULL DEFAULT 0,
                    local_playcount INTEGER NOT NULL DEFAULT 0,
                    replaygain_db REAL NOT NULL DEFAULT 0.0,
                    bitrate_kbps INTEGER NOT NULL DEFAULT 320,
                    added_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);
                CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);

                CREATE TABLE IF NOT EXISTS seen_hashes (
                    track_hash TEXT PRIMARY KEY,
                    artist TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'download',
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scrobble_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    album TEXT NOT NULL DEFAULT '',
                    duration INTEGER NOT NULL DEFAULT 0,
                    timestamp INTEGER NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(artist, title, timestamp)
                );

                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_track_id INTEGER NOT NULL,
                    to_track_id INTEGER NOT NULL,
                    probability REAL NOT NULL DEFAULT 1.0,
                    crossfade_seconds REAL NOT NULL DEFAULT 3.0,
                    UNIQUE(from_track_id, to_track_id),
                    FOREIGN KEY(from_track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY(to_track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
            )
            # Seed default settings without overwriting existing keys
            for key, default_val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
                    (key, json.dumps(default_val)),
                )
            conn.commit()

    def _migrate_legacy_files(self) -> None:
        """One-time migration from v1.0 config.json and seen_hashes.json into SQLite."""
        with self._write_lock:
            conn = self._get_conn()
            if PATHS.legacy_config_path.exists():
                try:
                    raw_cfg = json.loads(PATHS.legacy_config_path.read_text(encoding="utf-8"))
                    if isinstance(raw_cfg, dict):
                        for k, v in raw_cfg.items():
                            if v not in (None, ""):
                                conn.execute(
                                    "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                                    (k, json.dumps(v)),
                                )
                        conn.commit()
                    backup = PATHS.legacy_config_path.with_suffix(".json.bak")
                    shutil.move(str(PATHS.legacy_config_path), str(backup))
                    logger.info("Migrated legacy config.json into SQLite settings (backed up to %s)", backup.name)
                except Exception as exc:
                    logger.warning("Failed to migrate legacy config.json: %s", exc)

            if PATHS.legacy_hashes_path.exists():
                try:
                    raw_hashes = json.loads(PATHS.legacy_hashes_path.read_text(encoding="utf-8"))
                    now_ts = int(time.time())
                    if isinstance(raw_hashes, list):
                        for h in raw_hashes:
                            conn.execute(
                                "INSERT OR IGNORE INTO seen_hashes (track_hash, source, created_at) VALUES (?, 'legacy', ?)",
                                (str(h), now_ts),
                            )
                    elif isinstance(raw_hashes, dict):
                        for h, meta in raw_hashes.items():
                            artist = meta.get("artist", "") if isinstance(meta, dict) else ""
                            title = meta.get("title", "") if isinstance(meta, dict) else str(meta)
                            conn.execute(
                                "INSERT OR IGNORE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, 'legacy', ?)",
                                (str(h), artist, title, now_ts),
                            )
                    conn.commit()
                    backup = PATHS.legacy_hashes_path.with_suffix(".json.bak")
                    shutil.move(str(PATHS.legacy_hashes_path), str(backup))
                    logger.info("Migrated legacy seen_hashes.json into SQLite (backed up to %s)", backup.name)
                except Exception as exc:
                    logger.warning("Failed to migrate legacy seen_hashes.json: %s", exc)

    # ---- Settings Operations ----

    def get_settings(self) -> dict[str, Any]:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value_json"])
            except Exception:
                result[row["key"]] = row["value_json"]
        return result

    def get_setting(self, key: str, default: Any = None) -> Any:
        conn = self._get_conn()
        row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return DEFAULT_SETTINGS.get(key, default)
        try:
            return json.loads(row["value_json"])
        except Exception:
            return row["value_json"]

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            for key, value in updates.items():
                if value is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )
            conn.commit()
        return self.get_settings()

    # ---- Deduplication Hash Operations ----

    def is_hash_seen(self, track_hash: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM seen_hashes WHERE track_hash = ? UNION SELECT 1 FROM tracks WHERE track_hash = ? LIMIT 1",
            (track_hash, track_hash),
        ).fetchone()
        return row is not None

    def mark_hash_seen(self, track_hash: str, artist: str = "", title: str = "", source: str = "download") -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (track_hash, artist, title, source, int(time.time())),
            )
            conn.commit()

    def import_playlist_lines(self, lines: list[str]) -> int:
        """Import 'Artist - Title' text lines into seen_hashes so past playlists are not re-downloaded."""
        imported = 0
        now_ts = int(time.time())
        with self._write_lock:
            conn = self._get_conn()
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith("#") or " - " not in line:
                    continue
                artist, title = line.split(" - ", 1)
                thash = compute_track_hash(artist, title)
                conn.execute(
                    "INSERT OR IGNORE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, 'imported_list', ?)",
                    (thash, artist.strip(), title.strip(), now_ts),
                )
                imported += 1
            conn.commit()
        return imported

    # ---- Track Library Operations ----

    def upsert_track(
        self,
        *,
        track_hash: str,
        title: str,
        artist_display: str,
        artists: list[str],
        album_artist: str,
        album: str,
        release_year: str,
        original_year: str,
        duration: int,
        file_path: str,
        lrc_path: str = "",
        cover_mime: str = "",
        has_cover: bool = False,
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        bitrate_kbps: int = 320,
    ) -> TrackEntity:
        now_ts = int(time.time())
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO tracks (
                    track_hash, title, artist_display, artists_json, album_artist,
                    album, release_year, original_year, duration, file_path,
                    lrc_path, cover_mime, has_cover, lastfm_playcount,
                    replaygain_db, bitrate_kbps, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_hash) DO UPDATE SET
                    title = excluded.title,
                    artist_display = excluded.artist_display,
                    artists_json = excluded.artists_json,
                    album_artist = excluded.album_artist,
                    album = excluded.album,
                    release_year = CASE WHEN excluded.release_year != '' THEN excluded.release_year ELSE tracks.release_year END,
                    original_year = CASE WHEN excluded.original_year != '' THEN excluded.original_year ELSE tracks.original_year END,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END,
                    file_path = excluded.file_path,
                    lrc_path = CASE WHEN excluded.lrc_path != '' THEN excluded.lrc_path ELSE tracks.lrc_path END,
                    cover_mime = CASE WHEN excluded.cover_mime != '' THEN excluded.cover_mime ELSE tracks.cover_mime END,
                    has_cover = CASE WHEN excluded.has_cover = 1 THEN 1 ELSE tracks.has_cover END,
                    lastfm_playcount = MAX(tracks.lastfm_playcount, excluded.lastfm_playcount),
                    replaygain_db = excluded.replaygain_db,
                    bitrate_kbps = excluded.bitrate_kbps
                """,
                (
                    track_hash,
                    title,
                    artist_display,
                    json.dumps(artists, ensure_ascii=False),
                    album_artist,
                    album,
                    release_year,
                    original_year,
                    duration,
                    file_path,
                    lrc_path,
                    cover_mime,
                    1 if has_cover else 0,
                    lastfm_playcount,
                    replaygain_db,
                    bitrate_kbps,
                    now_ts,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO seen_hashes (track_hash, artist, title, source, created_at) VALUES (?, ?, ?, 'library', ?)",
                (track_hash, artist_display, title, now_ts),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tracks WHERE track_hash = ?", (track_hash,)).fetchone()
            return self._row_to_track(row)

    def list_tracks(self, search: str = "", artist: str = "") -> list[TrackEntity]:
        conn = self._get_conn()
        clauses: list[str] = []
        params: list[Any] = []
        if search.strip():
            q = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(title) LIKE ? OR LOWER(artist_display) LIKE ? OR LOWER(album) LIKE ?)")
            params.extend([q, q, q])
        if artist.strip():
            a = f"%{artist.strip().lower()}%"
            clauses.append("(LOWER(artist_display) LIKE ? OR LOWER(album_artist) LIKE ?)")
            params.extend([a, a])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM tracks {where_sql} ORDER BY added_at DESC, title ASC",
            params,
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def get_track(self, track_id: int) -> TrackEntity | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def increment_local_playcount(self, track_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("UPDATE tracks SET local_playcount = local_playcount + 1 WHERE id = ?", (track_id,))
            conn.commit()

    def remove_missing_files(self) -> int:
        """Prune database rows whose underlying MP3 files were deleted from disk."""
        removed = 0
        with self._write_lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT id, file_path FROM tracks").fetchall()
            for r in rows:
                if not Path(r["file_path"]).exists():
                    conn.execute("DELETE FROM tracks WHERE id = ?", (r["id"],))
                    removed += 1
            if removed:
                conn.commit()
        return removed

    # ---- Scrobble Queue Operations ----

    def enqueue_scrobble(self, artist: str, title: str, album: str, duration: int, timestamp: int, error: str = "") -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR IGNORE INTO scrobble_queue (artist, title, album, duration, timestamp, synced, last_error)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (artist, title, album, duration, timestamp, error),
            )
            conn.commit()

    def get_pending_scrobbles(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM scrobble_queue WHERE synced = 0 ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_scrobbles_synced(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(f"UPDATE scrobble_queue SET synced = 1 WHERE id IN ({placeholders})", ids)
            conn.commit()

    # ---- Custom Song Transitions ----

    def upsert_transition(self, from_track_id: int, to_track_id: int, probability: float, crossfade_seconds: float) -> dict[str, Any]:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO transitions (from_track_id, to_track_id, probability, crossfade_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(from_track_id, to_track_id) DO UPDATE SET
                    probability = excluded.probability,
                    crossfade_seconds = excluded.crossfade_seconds
                """,
                (from_track_id, to_track_id, probability, crossfade_seconds),
            )
            conn.commit()
        return {
            "from_track_id": from_track_id,
            "to_track_id": to_track_id,
            "probability": probability,
            "crossfade_seconds": crossfade_seconds,
        }

    def get_transitions_for_track(self, from_track_id: int) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT tr.id, tr.from_track_id, tr.to_track_id, tr.probability, tr.crossfade_seconds,
                   t.title AS to_title, t.artist_display AS to_artist
            FROM transitions tr
            JOIN tracks t ON t.id = tr.to_track_id
            WHERE tr.from_track_id = ?
            """,
            (from_track_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_transitions(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT tr.id, tr.from_track_id, tr.to_track_id, tr.probability, tr.crossfade_seconds,
                   t1.title AS from_title, t1.artist_display AS from_artist,
                   t2.title AS to_title, t2.artist_display AS to_artist
            FROM transitions tr
            JOIN tracks t1 ON t1.id = tr.from_track_id
            JOIN tracks t2 ON t2.id = tr.to_track_id
            ORDER BY tr.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_transition(self, transition_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM transitions WHERE id = ?", (transition_id,))
            conn.commit()

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> TrackEntity:
        try:
            artists = json.loads(row["artists_json"])
        except Exception:
            artists = [row["artist_display"]]
        return TrackEntity(
            id=int(row["id"]),
            track_hash=str(row["track_hash"]),
            title=str(row["title"]),
            artist_display=str(row["artist_display"]),
            artists=artists if isinstance(artists, list) else [ str(row["artist_display"]) ],
            album_artist=str(row["album_artist"]),
            album=str(row["album"]),
            release_year=str(row["release_year"]),
            original_year=str(row["original_year"]),
            duration=int(row["duration"]),
            file_path=str(row["file_path"]),
            lrc_path=str(row["lrc_path"]),
            cover_mime=str(row["cover_mime"]),
            has_cover=bool(row["has_cover"]),
            lastfm_playcount=int(row["lastfm_playcount"]),
            local_playcount=int(row["local_playcount"]),
            replaygain_db=float(row["replaygain_db"]),
            bitrate_kbps=int(row["bitrate_kbps"]),
            added_at=int(row["added_at"]),
        )


db_repo = DatabaseRepository()
```

### File: `src/musiclinker/infrastructure/lastfm_client.py`
```python
"""Bi-directional Last.fm API client supporting chart extraction, date-range aggregation, and signed scrobbling."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import time
from typing import Any, Callable

import requests

from musiclinker.core.exceptions import LastFMAuthError, LastFMError
from musiclinker.core.logging_config import logger
from musiclinker.domain.models import CandidateTrack
from musiclinker.infrastructure.hasher import compute_track_hash

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "MusicLinker/2.0 (+https://github.com/FabriThomas/musicLink)"


class LastFMClient:
    """Handles Last.fm read queries (Top Tracks, Calendar Year Scrobbles) and write calls (Now Playing, Scrobble)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str = "",
        session_key: str = "",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.session_key = session_key.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ---- Signature & Request Helpers ----

    def _sign_params(self, params: dict[str, Any]) -> str:
        """Compute official Last.fm MD5 api_sig: sort keys alphabetically, concatenate <k><v>, append secret."""
        if not self.api_secret:
            raise LastFMAuthError("Last.fm API Secret is required to sign authentication and scrobble calls.")
        excluded = {"format", "callback"}
        payload = "".join(f"{k}{params[k]}" for k in sorted(params.keys()) if k not in excluded)
        payload += self.api_secret
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _call(self, method: str, params: dict[str, Any], signed: bool = False, http_post: bool = False) -> dict[str, Any]:
        if not self.api_key:
            raise LastFMError("Last.fm API Key is not configured. Open Settings to add your key.")
        req_params: dict[str, Any] = {
            "method": method,
            "api_key": self.api_key,
            **params,
        }
        if signed:
            req_params["api_sig"] = self._sign_params(req_params)
        req_params["format"] = "json"

        try:
            if http_post:
                resp = self.session.post(LASTFM_API_URL, data=req_params, timeout=self.timeout)
            else:
                resp = self.session.get(LASTFM_API_URL, params=req_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise LastFMError(f"Last.fm network request failed ({method}): {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            err_code = data.get("error")
            err_msg = data.get("message", "Unknown Last.fm error")
            if err_code in (4, 9, 14):
                raise LastFMAuthError(f"Last.fm authentication error ({err_code}): {err_msg}")
            raise LastFMError(f"Last.fm API error ({err_code}): {err_msg}")
        return data

    # ---- Web Authentication Flow ----

    def get_auth_token(self) -> dict[str, str]:
        """Request an unauthorized token and return the browser authorization URL."""
        data = self._call("auth.getToken", {}, signed=True, http_post=False)
        token = str(data.get("token", ""))
        if not token:
            raise LastFMAuthError("Failed to obtain Last.fm auth token.")
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"
        return {"token": token, "auth_url": auth_url}

    def exchange_token_for_session(self, token: str) -> dict[str, str]:
        """Exchange an authorized user token for a permanent Last.fm session key."""
        data = self._call("auth.getSession", {"token": token.strip()}, signed=True, http_post=False)
        session_obj = data.get("session", {})
        key = str(session_obj.get("key", ""))
        name = str(session_obj.get("name", ""))
        if not key:
            raise LastFMAuthError("User has not authorized the token in the browser yet.")
        self.session_key = key
        return {"session_key": key, "username": name}

    # ---- Extraction Operations ----

    def fetch_top_tracks_by_period(
        self,
        username: str,
        period: str = "12month",
        min_plays: int = 10,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[CandidateTrack]:
        """Fetch all user top tracks for a preset period (`7day`, `1month`, `3month`, `6month`, `12month`, `overall`) with playcount >= min_plays."""
        candidates: list[CandidateTrack] = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            if progress_cb:
                progress_cb(f"Fetching Last.fm top tracks page {page}/{total_pages} (period={period})...")
            data = self._call(
                "user.getTopTracks",
                {"user": username, "period": period, "limit": 200, "page": page},
            )
            toptracks = data.get("toptracks", {})
            attr = toptracks.get("@attr", {})
            total_pages = max(1, int(attr.get("totalPages", 1)))
            tracks = toptracks.get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            reached_threshold = False
            for item in tracks:
                playcount = int(item.get("playcount", 0))
                if playcount < min_plays:
                    reached_threshold = True
                    break
                artist_obj = item.get("artist", {})
                artist_name = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                title = str(item.get("name", "")).strip()
                if not artist_name or not title:
                    continue
                candidates.append(
                    CandidateTrack(
                        artist=artist_name.strip(),
                        title=title,
                        playcount=playcount,
                        track_hash=compute_track_hash(artist_name, title),
                        mbid=str(item.get("mbid", "")),
                    )
                )

            if reached_threshold or not tracks:
                break
            page += 1
            time.sleep(0.2)

        return candidates

    def fetch_tracks_by_calendar_year(
        self,
        username: str,
        year: int,
        min_plays: int = 10,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[CandidateTrack]:
        """Aggregate raw scrobbles across an entire calendar year and return tracks with >= min_plays."""
        start_ts = int(datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())

        counts: Counter[str] = Counter()
        exemplars: dict[str, tuple[str, str, str]] = {}  # hash -> (artist, title, album)
        page = 1
        total_pages = 1

        while page <= total_pages:
            if progress_cb:
                progress_cb(f"Scanning {year} scrobble history page {page}/{total_pages}...")
            data = self._call(
                "user.getRecentTracks",
                {
                    "user": username,
                    "from": start_ts,
                    "to": end_ts,
                    "limit": 200,
                    "page": page,
                },
            )
            recent = data.get("recenttracks", {})
            attr = recent.get("@attr", {})
            total_pages = max(1, int(attr.get("totalPages", 1)))
            tracks = recent.get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            for item in tracks:
                if isinstance(item.get("@attr"), dict) and item["@attr"].get("nowplaying") == "true":
                    continue
                artist_obj = item.get("artist", {})
                artist_name = artist_obj.get("#text", "") if isinstance(artist_obj, dict) else str(artist_obj)
                album_obj = item.get("album", {})
                album_name = album_obj.get("#text", "") if isinstance(album_obj, dict) else str(album_obj)
                title = str(item.get("name", "")).strip()
                if not artist_name or not title:
                    continue
                thash = compute_track_hash(artist_name, title)
                counts[thash] += 1
                if thash not in exemplars:
                    exemplars[thash] = (artist_name.strip(), title, album_name.strip())

            if not tracks:
                break
            page += 1
            time.sleep(0.2)

        results: list[CandidateTrack] = []
        for thash, playcount in counts.most_common():
            if playcount < min_plays:
                break
            artist_name, title, album_name = exemplars[thash]
            results.append(
                CandidateTrack(
                    artist=artist_name,
                    title=title,
                    album=album_name,
                    playcount=playcount,
                    track_hash=thash,
                )
            )
        return results

    # ---- Bi-Directional Scrobbling Operations ----

    def update_now_playing(self, artist: str, title: str, album: str = "", duration: int = 0) -> None:
        if not self.session_key:
            return
        params: dict[str, Any] = {
            "artist": artist,
            "track": title,
            "sk": self.session_key,
        }
        if album:
            params["album"] = album
        if duration > 0:
            params["duration"] = duration
        self._call("track.updateNowPlaying", params, signed=True, http_post=True)

    def submit_scrobbles(self, scrobbles: list[dict[str, Any]]) -> bool:
        """Submit up to 50 scrobbles in a single batch call per official Last.fm API spec."""
        if not scrobbles:
            return True
        if not self.session_key:
            raise LastFMAuthError("Cannot submit scrobbles without an active Last.fm session_key.")

        batch = scrobbles[:50]
        params: dict[str, Any] = {"sk": self.session_key}
        for idx, item in enumerate(batch):
            params[f"artist[{idx}]"] = item["artist"]
            params[f"track[{idx}]"] = item["title"]
            params[f"timestamp[{idx}]"] = int(item["timestamp"])
            if item.get("album"):
                params[f"album[{idx}]"] = item["album"]
            if int(item.get("duration", 0)) > 0:
                params[f"duration[{idx}]"] = int(item["duration"])

        self._call("track.scrobble", params, signed=True, http_post=True)
        logger.info("Successfully scrobbled %d track(s) to Last.fm", len(batch))
        return True
```

### File: `src/musiclinker/infrastructure/youtube_client.py`
```python
"""Keyless yt-dlp search engine and FFmpeg audio stream downloader with atomic staging."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp

from musiclinker.core.exceptions import YouTubeResolutionError
from musiclinker.core.logging_config import logger


class YouTubeClient:
    """Provides cached flat-search queries and high-bitrate MP3 extraction using bundled FFmpeg."""

    def __init__(self) -> None:
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            logger.warning("imageio_ffmpeg binary lookup failed (%s); falling back to system ffmpeg", exc)
            self.ffmpeg_exe = "ffmpeg"

    def search_videos(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Execute a fast keyless yt-dlp search and cache results in memory."""
        cache_key = f"{max_results}:{query.strip().lower()}"
        with self._cache_lock:
            if cache_key in self._search_cache:
                return self._search_cache[cache_key]

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": True,
        }
        search_expr = query if query.startswith(("http://", "https://")) else f"ytsearch{max_results}:{query}"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_expr, download=False)
        except Exception as exc:
            raise YouTubeResolutionError(f"YouTube search failed for '{query}': {exc}") from exc

        entries = info.get("entries", [info]) if isinstance(info, dict) else []
        results: list[dict[str, Any]] = []
        for entry in entries:
            if not entry:
                continue
            vid_id = entry.get("id", "")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
            if not url:
                continue
            thumbnails = entry.get("thumbnails") or []
            thumb_url = thumbnails[-1].get("url", "") if thumbnails else entry.get("thumbnail", "")
            results.append(
                {
                    "id": vid_id,
                    "title": entry.get("title", "Unknown Title"),
                    "uploader": entry.get("uploader") or entry.get("channel") or "YouTube",
                    "duration": int(entry.get("duration") or 0),
                    "url": url,
                    "thumbnail": thumb_url,
                }
            )

        with self._cache_lock:
            self._search_cache[cache_key] = results
        return results

    def resolve_best_audio_match(self, artist: str, title: str) -> dict[str, Any]:
        """Find the cleanest official audio stream for an artist and track title."""
        query = f"{artist} - {title} official audio"
        matches = self.search_videos(query, max_results=3)
        if not matches:
            raise YouTubeResolutionError(f"No YouTube results found for '{artist} - {title}'")
        # Prefer tracks between 45 seconds and 15 minutes to avoid 1-hour loops or shorts
        for candidate in matches:
            dur = candidate.get("duration", 0)
            if dur == 0 or (45 <= dur <= 900):
                return candidate
        return matches[0]

    def download_mp3_to_staging(
        self,
        url_or_query: str,
        staging_dir: Path,
        stem_name: str,
        bitrate_kbps: int = 320,
        progress_cb: Callable[[float], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Download audio stream to staging directory and transcode to MP3 at 192k or 320k."""
        staging_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(staging_dir / f"{stem_name}.%(ext)s")
        valid_bitrate = "320" if bitrate_kbps >= 256 else "192"

        def _hook(status_dict: dict[str, Any]) -> None:
            if progress_cb and status_dict.get("status") == "downloading":
                total = status_dict.get("total_bytes") or status_dict.get("total_bytes_estimate") or 0
                downloaded = status_dict.get("downloaded_bytes") or 0
                if total > 0:
                    progress_cb(min(100.0, round((downloaded / total) * 100.0, 1)))

        ydl_opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "ffmpeg_location": self.ffmpeg_exe,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_hook],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": valid_bitrate,
                }
            ],
        }

        target_expr = url_or_query if url_or_query.startswith(("http://", "https://")) else f"ytsearch1:{url_or_query}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_expr, download=True)
                if isinstance(info, dict) and "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    info = entries[0] if entries else {}
        except Exception as exc:
            raise YouTubeResolutionError(f"Failed downloading audio from '{url_or_query}': {exc}") from exc

        mp3_path = staging_dir / f"{stem_name}.mp3"
        if not mp3_path.exists():
            # Fallback scan in staging_dir
            candidates = list(staging_dir.glob(f"{stem_name}*.mp3"))
            if not candidates:
                raise YouTubeResolutionError("FFmpeg post-processing did not produce an MP3 file.")
            mp3_path = candidates[0]

        return mp3_path, (info if isinstance(info, dict) else {})


youtube_client = YouTubeClient()
```

### File: `src/musiclinker/infrastructure/musicbrainz_client.py`
```python
"""MusicBrainz & Cover Art Archive client resolving original release years (TDOR) and front cover art."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ReleaseMetadata

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
MB_USER_AGENT = "MusicLinker/2.0 ( https://github.com/FabriThomas/musicLink )"


class MusicBrainzClient:
    """Rate-limited (1 req/sec) MusicBrainz metadata resolver with in-memory caching."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": MB_USER_AGENT, "Accept": "application/json"})
        self._lock = threading.Lock()
        self._last_call_ts = 0.0
        self._cache: dict[str, ReleaseMetadata] = {}

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call_ts
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            self._last_call_ts = time.monotonic()

    def resolve_release_metadata(
        self,
        artist: str,
        title: str,
        fallback_album: str = "",
        fallback_thumb_url: str = "",
    ) -> ReleaseMetadata:
        """Look up recording on MusicBrainz to determine Original Release Year (TDOR), Release Year (TDRC), Album, and Cover Art."""
        cache_key = f"{artist.lower().strip()}::{title.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        meta = ReleaseMetadata(album=fallback_album or "Singles", album_artist=artist)
        try:
            self._throttle()
            query = f'artist:"{artist}" AND recording:"{title}"'
            resp = self.session.get(
                f"{MB_BASE_URL}/recording",
                params={"query": query, "limit": 5, "fmt": "json"},
                timeout=10.0,
            )
            if resp.ok:
                recordings = resp.json().get("recordings", [])
                if recordings:
                    best = recordings[0]
                    first_release_date = str(best.get("first-release-date", ""))
                    if len(first_release_date) >= 4:
                        meta.original_year = first_release_date[:4]
                        meta.release_year = first_release_date[:4]

                    releases = best.get("releases", [])
                    if releases:
                        chosen = self._pick_canonical_release(releases)
                        meta.album = str(chosen.get("title") or meta.album)
                        meta.release_mbid = str(chosen.get("id") or "")
                        rel_date = str(chosen.get("date") or "")
                        if len(rel_date) >= 4:
                            meta.release_year = rel_date[:4]
                            if not meta.original_year:
                                meta.original_year = meta.release_year
        except Exception as exc:
            logger.debug("MusicBrainz lookup skipped for %s - %s: %s", artist, title, exc)

        # Fetch Cover Art from Cover Art Archive first, fallback to YouTube thumbnail
        if meta.release_mbid:
            cover_bytes, mime = self._download_cover_art(f"{CAA_BASE_URL}/release/{meta.release_mbid}/front-500")
            if cover_bytes:
                meta.cover_bytes = cover_bytes
                meta.cover_mime = mime

        if not meta.cover_bytes and fallback_thumb_url:
            cover_bytes, mime = self._download_cover_art(fallback_thumb_url)
            if cover_bytes:
                meta.cover_bytes = cover_bytes
                meta.cover_mime = mime

        self._cache[cache_key] = meta
        return meta

    @staticmethod
    def _pick_canonical_release(releases: list[dict[str, Any]]) -> dict[str, Any]:
        for rel in releases:
            status = str(rel.get("status", "")).lower()
            rg = rel.get("release-group", {})
            primary_type = str(rg.get("primary-type", "")).lower()
            if status == "official" and primary_type == "album":
                return rel
        return releases[0]

    def _download_cover_art(self, url: str) -> tuple[bytes | None, str]:
        try:
            resp = self.session.get(url, timeout=8.0)
            if resp.ok and resp.content:
                ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                if ctype.startswith("image/"):
                    return resp.content, ctype
        except Exception:
            pass
        return None, "image/jpeg"


musicbrainz_client = MusicBrainzClient()
```

### File: `src/musiclinker/infrastructure/lyrics_client.py`
```python
"""LRCLIB synced karaoke lyrics client and .lrc timestamp parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import requests

from musiclinker.core.logging_config import logger

LRCLIB_API_URL = "https://lrclib.net/api"
_LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\](.*)")


class LyricsClient:
    """Queries LRCLIB for timestamped `.lrc` lyrics and parses `.lrc` files for the karaoke UI."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MusicLinker/2.0 (https://github.com/FabriThomas/musicLink)"})

    def fetch_and_save_lrc(
        self,
        artist: str,
        title: str,
        album: str,
        duration: int,
        target_lrc_path: Path,
    ) -> str:
        """Query LRCLIB for synced lyrics, write sidecar `.lrc` file if found, and return the raw text."""
        synced_text, plain_text = self.fetch_lyrics(artist, title, album, duration)
        content_to_save = synced_text or plain_text
        if content_to_save:
            try:
                target_lrc_path.write_text(content_to_save, encoding="utf-8")
                return content_to_save
            except Exception as exc:
                logger.warning("Failed writing LRC file %s: %s", target_lrc_path, exc)
        return ""

    def fetch_lyrics(self, artist: str, title: str, album: str = "", duration: int = 0) -> tuple[str, str]:
        """Return `(synced_lyrics, plain_lyrics)` from LRCLIB."""
        try:
            params: dict[str, Any] = {"artist_name": artist, "track_name": title}
            if album:
                params["album_name"] = album
            if duration > 0:
                params["duration"] = duration
            resp = self.session.get(f"{LRCLIB_API_URL}/get", params=params, timeout=8.0)
            if resp.status_code == 404:
                # Fallback to LRCLIB search endpoint
                search_resp = self.session.get(
                    f"{LRCLIB_API_URL}/search",
                    params={"q": f"{artist} {title}"},
                    timeout=8.0,
                )
                if search_resp.ok:
                    items = search_resp.json()
                    if isinstance(items, list) and items:
                        first = items[0]
                        return str(first.get("syncedLyrics") or ""), str(first.get("plainLyrics") or "")
                return "", ""
            if resp.ok:
                data = resp.json()
                return str(data.get("syncedLyrics") or ""), str(data.get("plainLyrics") or "")
        except Exception as exc:
            logger.debug("LRCLIB lookup skipped for %s - %s: %s", artist, title, exc)
        return "", ""

    @staticmethod
    def parse_lrc_content(raw_lrc: str) -> dict[str, Any]:
        """Parse timestamped `[mm:ss.xx]` lines into structured JSON for smooth UI auto-scrolling."""
        if not raw_lrc.strip():
            return {"synced": False, "lines": []}

        synced_lines: list[dict[str, Any]] = []
        plain_lines: list[dict[str, Any]] = []

        for raw_line in raw_lrc.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _LRC_LINE_RE.match(line)
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                text = match.group(3).strip()
                total_sec = round(minutes * 60.0 + seconds, 2)
                synced_lines.append({"time": total_sec, "text": text or "♪"})
            elif not line.startswith("["):
                plain_lines.append({"time": None, "text": line})

        if synced_lines:
            synced_lines.sort(key=lambda item: float(item["time"]))
            return {"synced": True, "lines": synced_lines}
        return {"synced": False, "lines": plain_lines}


lyrics_client = LyricsClient()
```

### File: `src/musiclinker/infrastructure/tagger.py`
```python
"""Advanced ID3v2.4 tagger supporting null-delimited multi-artist frames, original year, ReplayGain, and cover art."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TDRC,
    TDOR,
    TIT2,
    TALB,
    TPE1,
    TPE2,
    TXXX,
    USLT,
)
from mutagen.mp3 import MP3

from musiclinker.core.exceptions import TaggingError
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists


class AudioTagger:
    """Reads and writes audiophile-grade ID3v2.4 metadata frames on MP3 files."""

    def write_id3v24_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        release_year: str = "",
        original_year: str = "",
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        lyrics_text: str = "",
        cover_bytes: bytes | None = None,
        cover_mime: str = "image/jpeg",
    ) -> tuple[int, int]:
        """
        Write standard ID3v2.4 frames (`v2_version=4`).

        - `TPE1` stores a list of artist strings (`mutagen` writes null-byte `\\x00` separators in ID3v2.4).
        - `TPE2` stores the primary Album Artist.
        - `TDRC` and `TDOR` store Release Year and Original Year.
        - `TXXX:REPLAYGAIN_TRACK_GAIN` stores loudness normalization offset.
        Returns `(duration_seconds, bitrate_kbps)`.
        """
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            # encoding=3 specifies UTF-8, the standard for ID3v2.4 multi-value frames
            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if release_year:
                tags.delall("TDRC")
                tags.add(TDRC(encoding=3, text=[str(release_year)]))

            if original_year:
                tags.delall("TDOR")
                tags.add(TDOR(encoding=3, text=[str(original_year)]))

            tags.delall("COMM")
            tags.add(
                COMM(
                    encoding=3,
                    lang="eng",
                    desc="MusicLinker",
                    text=[f"Last.fm Scrobbles: {lastfm_playcount}"],
                )
            )

            tags.delall("TXXX:REPLAYGAIN_TRACK_GAIN")
            tags.add(
                TXXX(
                    encoding=3,
                    desc="REPLAYGAIN_TRACK_GAIN",
                    text=[f"{replaygain_db:+.2f} dB"],
                )
            )

            if lyrics_text:
                tags.delall("USLT")
                tags.add(USLT(encoding=3, lang="eng", desc="SyncedLyrics", text=lyrics_text))

            if cover_bytes:
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime=cover_mime or "image/jpeg",
                        type=3,  # Front Cover
                        desc="Cover",
                        data=cover_bytes,
                    )
                )

            tags.save(str(mp3_path), v2_version=4)

            audio = MP3(str(mp3_path))
            duration = int(round(audio.info.length)) if audio.info else 0
            bitrate = int(round((audio.info.bitrate or 320000) / 1000)) if audio.info else 320
            return duration, bitrate
        except Exception as exc:
            raise TaggingError(f"Failed to write ID3v2.4 tags to {mp3_path.name}: {exc}") from exc

    def read_mp3_metadata(self, mp3_path: Path) -> dict[str, Any]:
        """Inspect an existing MP3 file on disk for library synchronization."""
        audio = MP3(str(mp3_path))
        duration = int(round(audio.info.length)) if audio.info else 0
        bitrate = int(round((audio.info.bitrate or 320000) / 1000)) if audio.info else 320
        tags = audio.tags

        fallback_stem = mp3_path.stem
        if " - " in fallback_stem:
            fb_artist, fb_title = fallback_stem.split(" - ", 1)
        else:
            fb_artist, fb_title = mp3_path.parent.parent.name or "Unknown Artist", fallback_stem

        if tags is None:
            album_artist, artists, clean_title = split_artists(fb_artist, fb_title)
            return {
                "track_hash": compute_track_hash(album_artist, clean_title),
                "title": clean_title,
                "artist_display": ", ".join(artists),
                "artists": artists,
                "album_artist": album_artist,
                "album": mp3_path.parent.name if mp3_path.parent.name != "downloads" else "Singles",
                "release_year": "",
                "original_year": "",
                "duration": duration,
                "bitrate_kbps": bitrate,
                "has_cover": False,
                "cover_mime": "",
                "replaygain_db": 0.0,
            }

        tpe1 = tags.get("TPE1")
        artists = [str(x).strip() for x in (tpe1.text if tpe1 else []) if str(x).strip()]
        if len(artists) == 1:
            _, split_list, _ = split_artists(artists[0])
            artists = split_list
        if not artists:
            artists = [fb_artist.strip()]

        tpe2 = tags.get("TPE2")
        album_artist = str(tpe2.text[0]).strip() if (tpe2 and tpe2.text) else artists[0]

        tit2 = tags.get("TIT2")
        title = str(tit2.text[0]).strip() if (tit2 and tit2.text) else fb_title.strip()

        talb = tags.get("TALB")
        album = str(talb.text[0]).strip() if (talb and talb.text) else "Singles"

        tdrc = tags.get("TDRC")
        release_year = str(tdrc.text[0])[:4] if (tdrc and tdrc.text) else ""

        tdor = tags.get("TDOR")
        original_year = str(tdor.text[0])[:4] if (tdor and tdor.text) else release_year

        apics = tags.getall("APIC")
        has_cover = bool(apics)
        cover_mime = str(apics[0].mime) if apics else ""

        rg_frame = tags.get("TXXX:REPLAYGAIN_TRACK_GAIN")
        replaygain_db = 0.0
        if rg_frame and rg_frame.text:
            try:
                replaygain_db = float(str(rg_frame.text[0]).replace("dB", "").strip())
            except ValueError:
                replaygain_db = 0.0

        return {
            "track_hash": compute_track_hash(album_artist, title),
            "title": title,
            "artist_display": ", ".join(artists),
            "artists": artists,
            "album_artist": album_artist,
            "album": album,
            "release_year": release_year,
            "original_year": original_year,
            "duration": duration,
            "bitrate_kbps": bitrate,
            "has_cover": has_cover,
            "cover_mime": cover_mime,
            "replaygain_db": replaygain_db,
        }

    @staticmethod
    def extract_cover_bytes(mp3_path: Path) -> tuple[bytes | None, str]:
        """Extract embedded APIC cover art bytes from an MP3 file on demand."""
        try:
            tags = ID3(str(mp3_path))
            apics = tags.getall("APIC")
            if apics:
                return bytes(apics[0].data), str(apics[0].mime or "image/jpeg")
        except Exception:
            pass
        return None, "image/jpeg"


audio_tagger = AudioTagger()
```

### File: `src/musiclinker/services/__init__.py`
```python
"""Application use-case services orchestrating domain models and infrastructure adapters."""
```

### File: `src/musiclinker/services/library_service.py`
```python
"""Filesystem-to-database library synchronizer, cover/lyrics loader, and transition resolver."""

from __future__ import annotations

from pathlib import Path
import random
import re
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import TrackEntity
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.tagger import audio_tagger

_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or fallback


class LibraryService:
    """Manages local music library indexing, metadata reading, and probabilistic song transitions."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def scan_and_sync_downloads(self) -> dict[str, int]:
        """Scan `downloads/` for MP3 files, index new/updated tracks into `library.db`, and prune missing rows."""
        pruned = self.repo.remove_missing_files()
        indexed = 0

        for mp3_path in PATHS.downloads_dir.rglob("*.mp3"):
            # Skip temporary staging files
            if ".staging" in mp3_path.parts:
                continue
            try:
                meta = audio_tagger.read_mp3_metadata(mp3_path)
                lrc_candidate = mp3_path.with_suffix(".lrc")
                lrc_path = str(lrc_candidate) if lrc_candidate.exists() else ""
                self.repo.upsert_track(
                    track_hash=meta["track_hash"],
                    title=meta["title"],
                    artist_display=meta["artist_display"],
                    artists=meta["artists"],
                    album_artist=meta["album_artist"],
                    album=meta["album"],
                    release_year=meta["release_year"],
                    original_year=meta["original_year"],
                    duration=meta["duration"],
                    file_path=str(mp3_path.resolve()),
                    lrc_path=lrc_path,
                    cover_mime=meta["cover_mime"],
                    has_cover=meta["has_cover"],
                    replaygain_db=meta["replaygain_db"],
                    bitrate_kbps=meta["bitrate_kbps"],
                )
                indexed += 1
            except Exception as exc:
                logger.warning("Skipping unreadable MP3 %s: %s", mp3_path, exc)

        logger.info("Library sync complete: %d track(s) indexed, %d missing entry(s) pruned", indexed, pruned)
        return {"indexed": indexed, "pruned": pruned}

    def get_track_lyrics(self, track: TrackEntity) -> dict[str, Any]:
        """Return parsed synced or plain lyrics for a track, fetching from LRCLIB on demand if missing."""
        if track.lrc_path and Path(track.lrc_path).exists():
            raw = Path(track.lrc_path).read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw)

        mp3_file = Path(track.file_path)
        lrc_file = mp3_file.with_suffix(".lrc")
        if lrc_file.exists():
            raw = lrc_file.read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw)

        fetched = lyrics_client.fetch_and_save_lrc(
            artist=track.album_artist or track.artist_display,
            title=track.title,
            album=track.album,
            duration=track.duration,
            target_lrc_path=lrc_file,
        )
        if fetched:
            self.repo.upsert_track(
                track_hash=track.track_hash,
                title=track.title,
                artist_display=track.artist_display,
                artists=track.artists,
                album_artist=track.album_artist,
                album=track.album,
                release_year=track.release_year,
                original_year=track.original_year,
                duration=track.duration,
                file_path=track.file_path,
                lrc_path=str(lrc_file),
                cover_mime=track.cover_mime,
                has_cover=track.has_cover,
                lastfm_playcount=track.lastfm_playcount,
                replaygain_db=track.replaygain_db,
                bitrate_kbps=track.bitrate_kbps,
            )
            return lyrics_client.parse_lrc_content(fetched)
        return {"synced": False, "lines": []}

    def resolve_next_transition(self, current_track_id: int) -> dict[str, Any] | None:
        """Evaluate probabilistic transition rules for `current_track_id`."""
        rules = self.repo.get_transitions_for_track(current_track_id)
        if not rules:
            return None
        for rule in rules:
            prob = float(rule.get("probability", 1.0))
            if random.random() <= prob:
                next_track = self.repo.get_track(int(rule["to_track_id"]))
                if next_track:
                    return {
                        "next_track": next_track.to_dict(),
                        "crossfade_seconds": float(rule.get("crossfade_seconds", 3.0)),
                    }
        return None


library_service = LibraryService()
```

### File: `src/musiclinker/services/scrobble_service.py`
```python
"""Real-time Now Playing notification and offline-resilient Last.fm scrobble manager."""

from __future__ import annotations

from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ScrobblePayload
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient


class ScrobbleService:
    """Manages real-time Last.fm updates and flushes queued offline scrobbles when connectivity returns."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def _build_client(self) -> LastFMClient:
        cfg = self.repo.get_settings()
        return LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
            session_key=str(cfg.get("lastfm_session_key", "")),
        )

    def notify_now_playing(self, payload: ScrobblePayload) -> dict[str, Any]:
        client = self._build_client()
        if not client.session_key or not client.api_key:
            return {"status": "skipped", "reason": "Last.fm session not configured"}
        try:
            client.update_now_playing(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
            )
            return {"status": "ok"}
        except Exception as exc:
            logger.debug("Now Playing update skipped (offline or unauthenticated): %s", exc)
            return {"status": "offline", "detail": str(exc)}

    def record_scrobble(self, payload: ScrobblePayload) -> dict[str, Any]:
        """Record local play count, attempt immediate Last.fm scrobble, or queue to SQLite if offline."""
        if payload.track_id is not None:
            self.repo.increment_local_playcount(payload.track_id)

        client = self._build_client()
        item = {
            "artist": payload.artist,
            "title": payload.title,
            "album": payload.album,
            "duration": payload.duration,
            "timestamp": payload.timestamp,
        }

        if not client.session_key or not client.api_key:
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
                error="Unauthenticated session; queued locally",
            )
            return {"status": "queued", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}

        try:
            client.submit_scrobbles([item])
            # Piggyback flush of any previously queued offline scrobbles
            self.flush_pending_queue()
            return {"status": "scrobbled", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}
        except Exception as exc:
            logger.warning("Scrobble failed, queuing for later retry: %s", exc)
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
                error=str(exc),
            )
            return {"status": "queued", "pending_count": len(self.repo.get_pending_scrobbles(limit=500))}

    def record_batch_scrobbles(self, items: list[ScrobblePayload]) -> dict[str, Any]:
        """Ingest a batch of offline scrobbles from an Android PWA client and flush to Last.fm."""
        for payload in items:
            if payload.track_id is not None:
                self.repo.increment_local_playcount(payload.track_id)
            self.repo.enqueue_scrobble(
                artist=payload.artist,
                title=payload.title,
                album=payload.album,
                duration=payload.duration,
                timestamp=payload.timestamp,
            )
        flushed = self.flush_pending_queue()
        return {
            "ingested": len(items),
            "flushed": flushed,
            "pending_count": len(self.repo.get_pending_scrobbles(limit=500)),
        }

    def flush_pending_queue(self) -> int:
        """Flush pending SQLite `scrobble_queue` items to Last.fm in batches of 50."""
        pending = self.repo.get_pending_scrobbles(limit=50)
        if not pending:
            return 0
        client = self._build_client()
        if not client.session_key or not client.api_key:
            return 0
        try:
            client.submit_scrobbles(pending)
            ids = [int(row["id"]) for row in pending]
            self.repo.mark_scrobbles_synced(ids)
            return len(ids)
        except Exception as exc:
            logger.debug("Pending scrobble flush deferred: %s", exc)
            return 0


scrobble_service = ScrobbleService()
```

### File: `src/musiclinker/services/job_service.py`
```python
"""Background extraction & audio acquisition pipeline with SSE event streaming and atomic file staging."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from typing import Any

from musiclinker.core.exceptions import JobCancelledError
from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import (
    CandidateTrack,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    TrackEntity,
)
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.musicbrainz_client import musicbrainz_client
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.library_service import sanitize_filename


@dataclass
class JobRecord:
    """In-memory state of an active or historical extraction/download job."""

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    current_step: str = "Queued..."
    total_candidates: int = 0
    downloaded_count: int = 0
    skipped_seen_count: int = 0
    excess_saved_path: str = ""
    logs: list[str] = field(default_factory=list)
    tracks_added: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    created_at: int = field(default_factory=lambda: int(time.time()))

    def append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 250:
            self.logs = self.logs[-250:]
        self.current_step = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress_pct": round(self.progress_pct, 1),
            "current_step": self.current_step,
            "total_candidates": self.total_candidates,
            "downloaded_count": self.downloaded_count,
            "skipped_seen_count": self.skipped_seen_count,
            "excess_saved_path": self.excess_saved_path,
            "logs": self.logs[-60:],
            "tracks_added": self.tracks_added,
            "created_at": self.created_at,
        }


class JobService:
    """Executes extraction and download pipelines in a bounded worker pool with atomic staging."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ml_job")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def get_job(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)]

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancel_requested = True
        job.append_log("Cancellation requested by user...")
        return True

    def start_lastfm_extraction_job(self, req: ExtractionJobRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="lastfm_extract")
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run_extraction_pipeline, record, req)
        return record

    def start_direct_download_job(self, req: DirectDownloadRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="direct_download")
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run_direct_download, record, req)
        return record

    # ---- Pipeline Implementations ----

    def _run_extraction_pipeline(self, job: JobRecord, req: ExtractionJobRequest) -> None:
        job.status = JobStatus.RUNNING
        job_staging = PATHS.staging_dir / job.job_id
        try:
            cfg = self.repo.get_settings()
            api_key = str(cfg.get("lastfm_api_key", ""))
            api_secret = str(cfg.get("lastfm_api_secret", ""))
            lfm = LastFMClient(api_key=api_key, api_secret=api_secret)

            if req.period == "custom_year" and req.year:
                job.append_log(f"Extracting Last.fm scrobbles for {req.username} in year {req.year} (min plays: {req.min_plays})...")
                raw_candidates = lfm.fetch_tracks_by_calendar_year(
                    username=req.username,
                    year=req.year,
                    min_plays=req.min_plays,
                    progress_cb=job.append_log,
                )
            else:
                job.append_log(f"Extracting Last.fm top tracks for {req.username} (period: {req.period}, min plays: {req.min_plays})...")
                raw_candidates = lfm.fetch_top_tracks_by_period(
                    username=req.username,
                    period=req.period,
                    min_plays=req.min_plays,
                    progress_cb=job.append_log,
                )

            job.total_candidates = len(raw_candidates)
            job.append_log(f"Found {len(raw_candidates)} track(s) with >= {req.min_plays} plays.")

            unseen: list[CandidateTrack] = []
            for cand in raw_candidates:
                if req.skip_seen_hashes and self.repo.is_hash_seen(cand.track_hash):
                    job.skipped_seen_count += 1
                else:
                    unseen.append(cand)

            job.append_log(
                f"After SHA-256 deduplication: {len(unseen)} new track(s), {job.skipped_seen_count} previously seen."
            )

            # Enforce download cap and save excess tracks to text file
            selected = unseen[: req.download_cap]
            excess = unseen[req.download_cap :]
            if excess or not req.download_enabled:
                export_list = unseen if not req.download_enabled else excess
                if export_list:
                    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                    label = "full_list" if not req.download_enabled else "excess_tracks"
                    list_path = PATHS.excess_lists_dir / f"{req.username}_{req.period}_{label}_{ts_str}.txt"
                    lines = [f"{c.artist} - {c.title} ({c.playcount} plays)" for c in export_list]
                    list_path.write_text("\n".join(lines), encoding="utf-8")
                    job.excess_saved_path = str(list_path)
                    job.append_log(f"Saved {len(export_list)} track(s) to {list_path.name}")

            if not req.download_enabled or not selected:
                job.progress_pct = 100.0
                job.status = JobStatus.COMPLETED
                job.append_log("Extraction finished.")
                return

            total_to_dl = len(selected)
            for idx, cand in enumerate(selected, start=1):
                if job.cancel_requested:
                    raise JobCancelledError("Job cancelled by user.")
                base_pct = ((idx - 1) / total_to_dl) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total_to_dl}] Processing: {cand.artist} - {cand.title} ({cand.playcount} plays)")
                try:
                    entity = self._acquire_and_tag_single_track(
                        artist_raw=cand.artist,
                        title_raw=cand.title,
                        album_hint=cand.album,
                        query_or_url="",
                        playcount=cand.playcount,
                        bitrate_kbps=req.bitrate_kbps,
                        staging_dir=job_staging,
                        progress_cb=lambda p, b=base_pct, s=total_to_dl: setattr(
                            job, "progress_pct", b + (p / s)
                        ),
                    )
                    job.downloaded_count += 1
                    job.tracks_added.append(entity.to_dict())
                    job.append_log(f"Completed: {entity.artist_display} - {entity.title} [{entity.album}]")
                except Exception as track_exc:
                    logger.warning("Failed processing track %s - %s: %s", cand.artist, cand.title, track_exc)
                    job.append_log(f"Skipped '{cand.artist} - {cand.title}': {track_exc}")

            job.progress_pct = 100.0
            job.status = JobStatus.COMPLETED
            job.append_log(f"Job complete: {job.downloaded_count}/{total_to_dl} track(s) downloaded and indexed.")
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Extraction job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _run_direct_download(self, job: JobRecord, req: DirectDownloadRequest) -> None:
        job.status = JobStatus.RUNNING
        job_staging = PATHS.staging_dir / job.job_id
        try:
            job.total_candidates = 1
            job.append_log(f"Resolving stream for: {req.query_or_url}")
            entity = self._acquire_and_tag_single_track(
                artist_raw=req.artist_override,
                title_raw=req.title_override,
                album_hint=req.album_override,
                query_or_url=req.query_or_url,
                playcount=0,
                bitrate_kbps=req.bitrate_kbps,
                staging_dir=job_staging,
                progress_cb=lambda p: setattr(job, "progress_pct", p),
            )
            job.downloaded_count = 1
            job.tracks_added.append(entity.to_dict())
            job.progress_pct = 100.0
            job.status = JobStatus.COMPLETED
            job.append_log(f"Downloaded & tagged: {entity.artist_display} - {entity.title}")
        except Exception as exc:
            logger.exception("Direct download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _acquire_and_tag_single_track(
        self,
        *,
        artist_raw: str,
        title_raw: str,
        album_hint: str,
        query_or_url: str,
        playcount: int,
        bitrate_kbps: int,
        staging_dir: Path,
        progress_cb: Any = None,
    ) -> TrackEntity:
        """Full atomic pipeline: YouTube resolve -> FFmpeg MP3 stage -> MusicBrainz -> LRCLIB -> ID3v2.4 -> Atomic move -> SQLite."""
        if not query_or_url:
            yt_match = youtube_client.resolve_best_audio_match(artist_raw, title_raw)
            target_url = yt_match["url"]
            thumb_url = yt_match.get("thumbnail", "")
        else:
            target_url = query_or_url
            thumb_url = ""

        stem = uuid.uuid4().hex[:8]
        staged_mp3, yt_info = youtube_client.download_mp3_to_staging(
            url_or_query=target_url,
            staging_dir=staging_dir,
            stem_name=stem,
            bitrate_kbps=bitrate_kbps,
            progress_cb=progress_cb,
        )

        if not thumb_url and isinstance(yt_info, dict):
            thumb_url = str(yt_info.get("thumbnail") or "")

        # Determine artist & title if downloaded from raw YouTube query without overrides
        if not artist_raw or not title_raw:
            yt_title = str(yt_info.get("title") or "Unknown Track")
            if " - " in yt_title:
                inferred_artist, inferred_title = yt_title.split(" - ", 1)
            else:
                inferred_artist = str(yt_info.get("uploader") or "Unknown Artist")
                inferred_title = yt_title
            artist_raw = artist_raw or inferred_artist
            title_raw = title_raw or inferred_title

        album_artist, artists_list, clean_title = split_artists(artist_raw, title_raw)
        artist_display = ", ".join(artists_list)
        track_hash = compute_track_hash(album_artist, clean_title)

        # Query MusicBrainz for Original Year (TDOR), Release Year (TDRC), Album, and Front Cover
        mb_meta = musicbrainz_client.resolve_release_metadata(
            artist=album_artist,
            title=clean_title,
            fallback_album=album_hint or "Singles",
            fallback_thumb_url=thumb_url,
        )
        final_album = album_hint or mb_meta.album or "Singles"

        # Destination folder: downloads/<AlbumArtist>/<Album>/<Artist> - <Title>.mp3
        dest_dir = (
            PATHS.downloads_dir
            / sanitize_filename(album_artist, "Unknown Artist")
            / sanitize_filename(final_album, "Singles")
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        base_filename = f"{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
        final_mp3_path = dest_dir / f"{base_filename}.mp3"
        final_lrc_path = dest_dir / f"{base_filename}.lrc"

        # Fetch synced lyrics via LRCLIB
        approx_duration = int(yt_info.get("duration") or 0)
        lyrics_text = lyrics_client.fetch_and_save_lrc(
            artist=album_artist,
            title=clean_title,
            album=final_album,
            duration=approx_duration,
            target_lrc_path=final_lrc_path,
        )

        # Embed ID3v2.4 multi-artist tags, release years, ReplayGain, and front cover art
        duration, actual_bitrate = audio_tagger.write_id3v24_tags(
            staged_mp3,
            title=clean_title,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            lastfm_playcount=playcount,
            replaygain_db=-1.5,
            lyrics_text=lyrics_text,
            cover_bytes=mb_meta.cover_bytes,
            cover_mime=mb_meta.cover_mime,
        )

        # Atomic move from staging to final library location
        os.replace(str(staged_mp3), str(final_mp3_path))

        entity = self.repo.upsert_track(
            track_hash=track_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            duration=duration or approx_duration,
            file_path=str(final_mp3_path.resolve()),
            lrc_path=str(final_lrc_path.resolve()) if final_lrc_path.exists() else "",
            cover_mime=mb_meta.cover_mime if mb_meta.cover_bytes else "",
            has_cover=bool(mb_meta.cover_bytes),
            lastfm_playcount=playcount,
            replaygain_db=-1.5,
            bitrate_kbps=actual_bitrate,
        )
        self.repo.mark_hash_seen(track_hash, artist_display, clean_title, source="download")
        return entity


job_service = JobService()
```

### File: `src/musiclinker/api/__init__.py`
```python
"""FastAPI application and REST/SSE endpoints."""
```

### File: `src/musiclinker/api/app.py`
```python
"""FastAPI server delivering HTTP 206 Partial Content audio streaming, SSE job events, LAN pairing, and PWA hosting."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import build_pairing_url, detect_lan_ip, verify_request_access
from musiclinker.domain.models import (
    BatchScrobblePayload,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    ScrobblePayload,
    SettingsUpdatePayload,
    TransitionRulePayload,
)
from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.job_service import job_service
from musiclinker.services.library_service import library_service
from musiclinker.services.scrobble_service import scrobble_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Synchronize `downloads/` into `library.db` and flush pending offline scrobbles on server boot."""
    try:
        library_service.scan_and_sync_downloads()
        scrobble_service.flush_pending_queue()
    except Exception as exc:
        logger.warning("Startup sync warning: %s", exc)
    yield


def require_paired_client(request: Request) -> None:
    """Dependency verifying loopback origin or valid LAN pairing token."""
    cfg = db_repo.get_settings()
    verify_request_access(
        request=request,
        expected_token=str(cfg.get("lan_pair_token", "")),
        require_auth_on_lan=bool(cfg.get("require_lan_token", True)),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="MusicLinker Personal Server",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Library & HTTP 206 Audio Streaming Endpoints ----

    @app.get("/api/tracks", dependencies=[Depends(require_paired_client)])
    async def list_tracks(search: str = "", artist: str = "") -> dict[str, Any]:
        tracks = db_repo.list_tracks(search=search, artist=artist)
        return {"tracks": [t.to_dict() for t in tracks], "total": len(tracks)}

    @app.post("/api/library/rescan", dependencies=[Depends(require_paired_client)])
    async def rescan_library() -> dict[str, int]:
        return library_service.scan_and_sync_downloads()

    @app.get("/api/tracks/{track_id}/stream", dependencies=[Depends(require_paired_client)])
    async def stream_track(track_id: int, request: Request) -> Response:
        """Serve audio stream with full RFC 7233 HTTP 206 Partial Content byte-range support for instant scrubbing."""
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track audio file not found on disk.")

        file_path = Path(track.file_path)
        file_size = file_path.stat().st_size
        range_header = request.headers.get("range") or request.headers.get("Range")

        if not range_header:
            return FileResponse(
                path=str(file_path),
                media_type="audio/mpeg",
                headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
            )

        try:
            range_spec = range_header.strip().lower().replace("bytes=", "")
            start_str, end_str = range_spec.split("-", 1)
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
        except ValueError:
            start, end = 0, file_size - 1

        chunk_length = (end - start) + 1

        def _file_iterator(path: Path, offset: int, length: int) -> Any:
            with path.open("rb") as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(
            _file_iterator(file_path, start, chunk_length),
            status_code=206,
            media_type="audio/mpeg",
            headers=headers,
        )

    @app.get("/api/tracks/{track_id}/file", dependencies=[Depends(require_paired_client)])
    async def download_track_file(track_id: int) -> FileResponse:
        """Full MP3 blob endpoint used by the Android PWA Service Worker for 'Save for Offline' caching."""
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track file not found.")
        return FileResponse(
            path=track.file_path,
            media_type="audio/mpeg",
            filename=Path(track.file_path).name,
        )

    @app.get("/api/tracks/{track_id}/cover")
    async def get_track_cover(track_id: int) -> Response:
        track = db_repo.get_track(track_id)
        if not track or not Path(track.file_path).exists():
            raise HTTPException(status_code=404, detail="Track not found.")
        cover_bytes, mime = audio_tagger.extract_cover_bytes(Path(track.file_path))
        if not cover_bytes:
            raise HTTPException(status_code=404, detail="No embedded cover art.")
        return Response(content=cover_bytes, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/tracks/{track_id}/lyrics", dependencies=[Depends(require_paired_client)])
    async def get_track_lyrics(track_id: int) -> dict[str, Any]:
        track = db_repo.get_track(track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found.")
        return library_service.get_track_lyrics(track)

    # ---- Player, Scrobbling & Custom Transitions ----

    @app.post("/api/player/now-playing", dependencies=[Depends(require_paired_client)])
    async def player_now_playing(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.notify_now_playing(payload)

    @app.post("/api/player/scrobble", dependencies=[Depends(require_paired_client)])
    async def player_scrobble(payload: ScrobblePayload) -> dict[str, Any]:
        return scrobble_service.record_scrobble(payload)

    @app.post("/api/player/scrobble/batch", dependencies=[Depends(require_paired_client)])
    async def player_scrobble_batch(payload: BatchScrobblePayload) -> dict[str, Any]:
        return scrobble_service.record_batch_scrobbles(payload.scrobbles)

    @app.get("/api/transitions", dependencies=[Depends(require_paired_client)])
    async def list_transitions() -> dict[str, Any]:
        return {"transitions": db_repo.list_all_transitions()}

    @app.post("/api/transitions", dependencies=[Depends(require_paired_client)])
    async def create_transition(payload: TransitionRulePayload) -> dict[str, Any]:
        return db_repo.upsert_transition(
            from_track_id=payload.from_track_id,
            to_track_id=payload.to_track_id,
            probability=payload.probability,
            crossfade_seconds=payload.crossfade_seconds,
        )

    @app.delete("/api/transitions/{transition_id}", dependencies=[Depends(require_paired_client)])
    async def remove_transition(transition_id: int) -> dict[str, str]:
        db_repo.delete_transition(transition_id)
        return {"status": "deleted"}

    @app.get("/api/tracks/{track_id}/next-transition", dependencies=[Depends(require_paired_client)])
    async def resolve_transition(track_id: int) -> dict[str, Any]:
        resolved = library_service.resolve_next_transition(track_id)
        return {"transition": resolved}

    # ---- YouTube Search & Background Extraction Jobs ----

    @app.get("/api/youtube/search", dependencies=[Depends(require_paired_client)])
    async def search_youtube(q: str = Query(..., min_length=2)) -> dict[str, Any]:
        results = youtube_client.search_videos(q, max_results=6)
        return {"results": results}

    @app.post("/api/jobs/extract", dependencies=[Depends(require_paired_client)])
    async def start_extract_job(req: ExtractionJobRequest) -> dict[str, Any]:
        job = job_service.start_lastfm_extraction_job(req)
        return job.to_dict()

    @app.post("/api/jobs/download", dependencies=[Depends(require_paired_client)])
    async def start_direct_download(req: DirectDownloadRequest) -> dict[str, Any]:
        job = job_service.start_direct_download_job(req)
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[Depends(require_paired_client)])
    async def get_all_jobs() -> dict[str, Any]:
        return {"jobs": job_service.list_jobs()}

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require_paired_client)])
    async def get_job_status(job_id: str) -> dict[str, Any]:
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_paired_client)])
    async def cancel_job(job_id: str) -> dict[str, Any]:
        if not job_service.cancel_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"status": "cancelling"}

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_paired_client)])
    async def stream_job_events(job_id: str) -> StreamingResponse:
        """Server-Sent Events (SSE) stream pushing real-time job progress to the UI."""
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        async def _event_generator() -> AsyncIterator[str]:
            while True:
                payload = json.dumps(job.to_dict())
                yield f"data: {payload}\n\n"
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
                await asyncio.sleep(0.6)

        return StreamingResponse(_event_generator(), media_type="text/event-stream")

    # ---- Phone LAN Pairing & Settings Endpoints ----

    @app.get("/api/sync/info", dependencies=[Depends(require_paired_client)])
    async def get_sync_info(request: Request) -> dict[str, Any]:
        cfg = db_repo.get_settings()
        port = request.url.port or 8000
        token = str(cfg.get("lan_pair_token", ""))
        pairing_url = build_pairing_url(port=port, pair_token=token)
        return {
            "lan_ip": detect_lan_ip(),
            "port": port,
            "pairing_url": pairing_url,
            "pair_token": token,
            "pending_scrobbles": len(db_repo.get_pending_scrobbles(limit=500)),
        }

    @app.get("/api/settings", dependencies=[Depends(require_paired_client)])
    async def read_settings() -> dict[str, Any]:
        cfg = db_repo.get_settings()
        return {
            "lastfm_api_key": cfg.get("lastfm_api_key", ""),
            "lastfm_api_secret_configured": bool(cfg.get("lastfm_api_secret")),
            "lastfm_username": cfg.get("lastfm_username", ""),
            "lastfm_session_connected": bool(cfg.get("lastfm_session_key")),
            "default_bitrate_kbps": cfg.get("default_bitrate_kbps", 320),
            "default_min_plays": cfg.get("default_min_plays", 10),
            "default_download_cap": cfg.get("default_download_cap", 25),
            "require_lan_token": cfg.get("require_lan_token", True),
            "normalize_audio": cfg.get("normalize_audio", True),
        }

    @app.put("/api/settings", dependencies=[Depends(require_paired_client)])
    async def save_settings(payload: SettingsUpdatePayload) -> dict[str, Any]:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        db_repo.update_settings(updates)
        return await read_settings()

    @app.post("/api/lastfm/auth/start", dependencies=[Depends(require_paired_client)])
    async def start_lastfm_web_auth() -> dict[str, str]:
        cfg = db_repo.get_settings()
        client = LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
        )
        return client.get_auth_token()

    @app.post("/api/lastfm/auth/complete", dependencies=[Depends(require_paired_client)])
    async def complete_lastfm_web_auth(token: str = Query(...)) -> dict[str, str]:
        cfg = db_repo.get_settings()
        client = LastFMClient(
            api_key=str(cfg.get("lastfm_api_key", "")),
            api_secret=str(cfg.get("lastfm_api_secret", "")),
        )
        session_info = client.exchange_token_for_session(token)
        db_repo.update_settings(
            {
                "lastfm_session_key": session_info["session_key"],
                "lastfm_username": session_info["username"] or cfg.get("lastfm_username", ""),
            }
        )
        return session_info

    # ---- PWA Static Files & Root Entrypoint ----

    if (PATHS.web_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(PATHS.web_dir / "js")), name="js")

    @app.get("/manifest.json")
    async def serve_manifest() -> FileResponse:
        return FileResponse(str(PATHS.web_dir / "manifest.json"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def serve_service_worker() -> FileResponse:
        return FileResponse(
            str(PATHS.web_dir / "sw.js"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/icon.svg")
    async def serve_icon_svg() -> FileResponse:
        return FileResponse(str(PATHS.web_dir / "icon.svg"), media_type="image/svg+xml")

    @app.get("/")
    async def serve_spa_index(request: Request) -> Response:
        index_file = PATHS.web_dir / "index.html"
        html_content = index_file.read_text(encoding="utf-8")
        response = HTMLResponse(content=html_content)
        pair_token = request.query_params.get("pair_token")
        if pair_token:
            response.set_cookie(
                key="ml_pair_token",
                value=pair_token,
                max_age=365 * 86400,
                httponly=False,
                samesite="lax",
            )
        return response

    return app


app = create_app()
```

### File: `src/musiclinker/entrypoints/__init__.py`
```python
"""Runtime entrypoints for Windows Desktop (WebView2 + pystray), CLI Wizard, and Streamlit Dashboard."""
```

### File: `src/musiclinker/entrypoints/desktop.py`
```python
"""Native Windows Desktop Application host combining FastAPI, Edge WebView2 (pywebview), and pystray System Tray."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser
from typing import Any

from PIL import Image, ImageDraw
import uvicorn

from musiclinker.api.app import app
from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS


def _Wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _load_or_generate_tray_image() -> Image.Image:
    if PATHS.icon_png_path.exists():
        try:
            return Image.open(PATHS.icon_png_path).convert("RGBA")
        except Exception:
            pass
    # Fallback programmatic 64x64 RGBA icon
    img = Image.new("RGBA", (64, 64), (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=(29, 185, 84, 255))
    draw.polygon([(26, 20), (26, 44), (46, 32)], fill=(18, 18, 24, 255))
    return img


class DesktopAppController:
    """Coordinates the background Uvicorn thread, pystray system tray icon, and pywebview desktop window."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self.host = host
        self.port = port
        self.local_url = f"http://127.0.0.1:{port}"
        self.window: Any = None
        self.tray_icon: Any = None

    def start_server_thread(self) -> None:
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config=config)
        thread = threading.Thread(target=server.run, name="ml_uvicorn", daemon=True)
        thread.start()
        _Wait_for_server("127.0.0.1", self.port)
        logger.info("MusicLinker FastAPI server listening on %s:%d", self.host, self.port)

    def _eval_js(self, script: str) -> None:
        if self.window is not None:
            try:
                self.window.evaluate_js(script)
            except Exception as exc:
                logger.debug("WebView2 JS dispatch error: %s", exc)

    def start_system_tray(self) -> None:
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Show MusicLinker", lambda: self.window and self.window.show(), default=True),
                pystray.MenuItem("Minimize to Tray", lambda: self.window and self.window.hide()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Play / Pause", lambda: self._eval_js("window.MusicLinkerApp?.togglePlay()")),
                pystray.MenuItem("Next Track", lambda: self._eval_js("window.MusicLinkerApp?.nextTrack()")),
                pystray.MenuItem("Previous Track", lambda: self._eval_js("window.MusicLinkerApp?.prevTrack()")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open in Default Browser", lambda: webbrowser.open(self.local_url)),
                pystray.MenuItem("Quit MusicLinker", self._quit_app),
            )
            self.tray_icon = pystray.Icon(
                "MusicLinker",
                _load_or_generate_tray_image(),
                "MusicLinker Personal Player",
                menu,
            )
            self.tray_icon.run_detached()
        except Exception as exc:
            logger.warning("System tray initialization skipped: %s", exc)

    def _quit_app(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    def run(self, browser_only: bool = False) -> None:
        self.start_server_thread()
        if browser_only:
            webbrowser.open(self.local_url)
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                return

        self.start_system_tray()
        try:
            import webview

            self.window = webview.create_window(
                title="MusicLinker",
                url=self.local_url,
                width=1280,
                height=840,
                min_size=(940, 620),
                background_color="#09090b",
                text_select=False,
            )
            webview.start(
                private_mode=False,
                storage_path=str(PATHS.webview_cache_dir),
            )
        except Exception as exc:
            logger.warning("pywebview unavailable (%s); falling back to default web browser", exc)
            webbrowser.open(self.local_url)
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass
        finally:
            if self.tray_icon is not None:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="MusicLinker Desktop & Personal Server")
    parser.add_argument("--port", type=int, default=8000, help="HTTP server port (default: 8000)")
    parser.add_argument("--browser", action="store_true", help="Run in standard browser instead of WebView2 window")
    args = parser.parse_args()

    controller = DesktopAppController(port=args.port)
    controller.run(browser_only=args.browser)


if __name__ == "__main__":
    main()
```

### File: `src/musiclinker/entrypoints/cli.py`
```python
"""Interactive CLI Wizard and scriptable headless runner for Last.fm extraction and downloading."""

from __future__ import annotations

import argparse
import sys
import time

from musiclinker.domain.models import ExtractionJobRequest, JobStatus
from musiclinker.infrastructure.db import db_repo
from musiclinker.services.job_service import job_service


def run_interactive_wizard() -> ExtractionJobRequest:
    cfg = db_repo.get_settings()
    print("=== MusicLinker v2.0 Interactive CLI Wizard ===")
    default_user = cfg.get("lastfm_username") or ""
    username = input(f"Last.fm Username [{default_user}]: ").strip() or default_user
    if not username:
        print("Error: Last.fm username is required.")
        sys.exit(1)

    print("Periods: 7day | 1month | 3month | 6month | 12month | overall | custom_year")
    period = input("Select Period [12month]: ").strip() or "12month"
    year = None
    if period == "custom_year":
        year_str = input("Enter Calendar Year (e.g. 2025): ").strip()
        year = int(year_str)

    min_plays_str = input(f"Minimum Scrobble Plays [{cfg.get('default_min_plays', 10)}]: ").strip()
    min_plays = int(min_plays_str) if min_plays_str else int(cfg.get("default_min_plays", 10))

    cap_str = input(f"Max Tracks to Download [{cfg.get('default_download_cap', 25)}]: ").strip()
    download_cap = int(cap_str) if cap_str else int(cfg.get("default_download_cap", 25))

    bitrate_str = input(f"MP3 Bitrate (192 or 320) [{cfg.get('default_bitrate_kbps', 320)}]: ").strip()
    bitrate = int(bitrate_str) if bitrate_str in ("192", "320") else 320

    dl_choice = input("Download MP3s now? (Y/n): ").strip().lower()
    download_enabled = dl_choice not in ("n", "no")

    return ExtractionJobRequest(
        username=username,
        period=period,
        year=year,
        min_plays=min_plays,
        download_cap=download_cap,
        bitrate_kbps=bitrate,
        download_enabled=download_enabled,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MusicLinker Headless CLI & Interactive Wizard")
    parser.add_argument("--username", type=str, help="Last.fm username")
    parser.add_argument("--period", type=str, default="12month", help="Last.fm period or 'custom_year'")
    parser.add_argument("--year", type=int, default=None, help="Calendar year when --period custom_year")
    parser.add_argument("--min-plays", type=int, default=10, help="Minimum play count filter (default: 10)")
    parser.add_argument("--cap", type=int, default=25, help="Maximum tracks to download (default: 25)")
    parser.add_argument("--bitrate", type=int, default=320, choices=[192, 320], help="Target MP3 bitrate")
    parser.add_argument("--download", action="store_true", help="Enable MP3 downloading in non-interactive mode")
    args = parser.parse_args()

    if not args.username:
        req = run_interactive_wizard()
    else:
        req = ExtractionJobRequest(
            username=args.username,
            period=args.period,
            year=args.year,
            min_plays=args.min_plays,
            download_cap=args.cap,
            bitrate_kbps=args.bitrate,
            download_enabled=args.download,
        )

    job = job_service.start_lastfm_extraction_job(req)
    seen_logs = 0
    while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        current_logs = job.logs
        while seen_logs < len(current_logs):
            print(current_logs[seen_logs])
            seen_logs += 1
        time.sleep(0.5)

    for line in job.logs[seen_logs:]:
        print(line)

    if job.status == JobStatus.COMPLETED:
        print(f"\nSuccess! Downloaded {job.downloaded_count} track(s).")
        if job.excess_saved_path:
            print(f"Excess track list saved to: {job.excess_saved_path}")
    else:
        print(f"\nJob finished with status: {job.status.value}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### File: `src/musiclinker/entrypoints/streamlit_app.py`
```python
"""Streamlit prototyping and analytics dashboard backed by the unified MusicLinker service layer."""

from __future__ import annotations

import streamlit as st

from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient


def render_dashboard() -> None:
    st.set_page_config(page_title="MusicLinker Analytics", page_icon="🎵", layout="wide")
    st.title("🎵 MusicLinker Library & Last.fm Explorer")

    cfg = db_repo.get_settings()
    tracks = [t.to_dict() for t in db_repo.list_tracks()]

    col1, col2, col3 = st.columns(3)
    col1.metric("Indexed Library Tracks", len(tracks))
    col2.metric("Pending Offline Scrobbles", len(db_repo.get_pending_scrobbles(limit=500)))
    col3.metric("Default Bitrate", f"{cfg.get('default_bitrate_kbps', 320)} kbps")

    st.subheader("Indexed Local Library")
    if tracks:
        st.dataframe(
            tracks,
            column_order=[
                "id",
                "artist_display",
                "title",
                "album",
                "original_year",
                "release_year",
                "lastfm_playcount",
                "local_playcount",
                "bitrate_kbps",
            ],
            use_container_width=True,
        )
    else:
        st.info("No tracks indexed yet. Run an extraction job or place MP3s inside downloads/.")

    st.subheader("Live Last.fm Filter Preview")
    with st.form("preview_form"):
        username = st.text_input("Last.fm Username", value=cfg.get("lastfm_username", ""))
        period = st.selectbox("Period", ["7day", "1month", "3month", "6month", "12month", "overall"], index=4)
        min_plays = st.number_input("Minimum Plays", min_value=1, value=10)
        submitted = st.form_submit_button("Preview Qualifying Tracks")

    if submitted and username:
        client = LastFMClient(api_key=str(cfg.get("lastfm_api_key", "")))
        candidates = client.fetch_top_tracks_by_period(username=username, period=period, min_plays=int(min_plays))
        rows = [
            {
                "Artist": c.artist,
                "Title": c.title,
                "Plays": c.playcount,
                "Hash": c.track_hash,
                "Already Seen": db_repo.is_hash_seen(c.track_hash),
            }
            for c in candidates
        ]
        st.write(f"Found **{len(rows)}** tracks with >= {min_plays} plays:")
        st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    render_dashboard()
```

### File: `src/musiclinker/web/manifest.json`
```json
{
  "name": "MusicLinker Personal Player",
  "short_name": "MusicLinker",
  "description": "Offline-first personal music player with synced karaoke lyrics, Car Mode, and Last.fm scrobbling.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#09090b",
  "theme_color": "#10b981",
  "orientation": "portrait-primary",
  "icons": [
    {
      "src": "/icon.svg",
      "sizes": "any",
      "type": "image/svg+xml",
      "purpose": "any maskable"
    }
  ]
}
```

### File: `src/musiclinker/web/icon.svg`
```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <rect width="256" height="256" rx="56" fill="#09090b"/>
  <circle cx="128" cy="128" r="92" fill="none" stroke="#10b981" stroke-width="14"/>
  <circle cx="128" cy="128" r="28" fill="#10b981"/>
  <path d="M108 92 L168 128 L108 164 Z" fill="#09090b"/>
</svg>
```

### File: `src/musiclinker/web/sw.js`
```javascript
const STATIC_CACHE = "musiclinker-static-v2";
const AUDIO_CACHE = "musiclinker-audio-v2";

const APP_SHELL = [
  "/",
  "/manifest.json",
  "/icon.svg",
  "/js/api.js",
  "/js/offline-store.js",
  "/js/audio-engine.js",
  "/js/app.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== AUDIO_CACHE)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Offline-cached full audio files (/api/tracks/{id}/file) or streams when offline
  if (url.pathname.endsWith("/file") || url.pathname.endsWith("/stream")) {
    const normalizedFilePath = url.pathname.replace(/\/stream$/, "/file");
    event.respondWith(
      caches.open(AUDIO_CACHE).then(async (audioCache) => {
        const cachedResponse = await audioCache.match(normalizedFilePath);
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request);
      })
    );
    return;
  }

  // Cache-first for cover art and lyrics once fetched
  if (url.pathname.endsWith("/cover") || url.pathname.endsWith("/lyrics")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        try {
          const networkResp = await fetch(event.request);
          if (networkResp && networkResp.ok) {
            cache.put(event.request, networkResp.clone());
          }
          return networkResp;
        } catch (_err) {
          const fallback = await cache.match(event.request);
          return fallback || new Response("{}", { status: 404 });
        }
      })
    );
    return;
  }

  // Network-first with cache fallback for app shell
  if (event.request.method === "GET" && !url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.match(event.request).then((r) => r || caches.match("/")))
    );
  }
});
```

### File: `src/musiclinker/web/js/api.js`
```javascript
/**
 * Authenticated API client and SSE job stream subscriber.
 */
class MusicLinkerAPI {
  constructor() {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("pair_token");
    if (urlToken) {
      localStorage.setItem("ml_pair_token", urlToken);
      window.history.replaceState({}, document.title, "/");
    }
  }

  getPairToken() {
    return localStorage.getItem("ml_pair_token") || "";
  }

  async request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = this.getPairToken();
    if (token) {
      headers.set("X-Pair-Token", token);
    }
    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const resp = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  subscribeJobEvents(jobId, onUpdate) {
    const token = encodeURIComponent(this.getPairToken());
    const url = `/api/jobs/${jobId}/events${token ? `?pair_token=${token}` : ""}`;
    const source = new EventSource(url);
    source.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        onUpdate(data);
        if (["completed", "failed", "cancelled"].includes(data.status)) {
          source.close();
        }
      } catch (err) {
        console.error("SSE parse error:", err);
      }
    };
    source.onerror = () => source.close();
    return source;
  }
}

export const api = new MusicLinkerAPI();
```

### File: `src/musiclinker/web/js/offline-store.js`
```javascript
/**
 * IndexedDB + Cache API offline storage manager for Android PWA caching and offline scrobble outbox.
 */
const DB_NAME = "musiclinker_pwa_db";
const DB_VERSION = 1;
const AUDIO_CACHE_NAME = "musiclinker-audio-v2";

class OfflineStore {
  constructor() {
    this.dbPromise = this._initDB();
  }

  _initDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (evt) => {
        const db = evt.target.result;
        if (!db.objectStoreNames.contains("offline_tracks")) {
          db.createObjectStore("offline_tracks", { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains("offline_scrobbles")) {
          db.createObjectStore("offline_scrobbles", { keyPath: "id", autoIncrement: true });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async saveTrackForOffline(track, pairToken = "") {
    const fileUrl = `/api/tracks/${track.id}/file`;
    const headers = pairToken ? { "X-Pair-Token": pairToken } : {};
    const response = await fetch(fileUrl, { headers });
    if (!response.ok) {
      throw new Error(`Failed to fetch audio blob (${response.status})`);
    }
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.put(fileUrl, response.clone());

    // Pre-cache lyrics and cover art as well
    if (track.cover_url) {
      fetch(track.cover_url).catch(() => {});
    }
    if (track.lyrics_url) {
      fetch(track.lyrics_url, { headers }).catch(() => {});
    }

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").put({ ...track, saved_offline_at: Date.now() });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async removeOfflineTrack(trackId) {
    const fileUrl = `/api/tracks/${trackId}/file`;
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.delete(fileUrl);

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").delete(trackId);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async getOfflineTracks() {
    const db = await this.dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readonly");
      const req = tx.objectStore("offline_tracks").getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async queueOfflineScrobble(scrobble) {
    const db = await this.dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readwrite");
      tx.objectStore("offline_scrobbles").add(scrobble);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async flushOfflineScrobbles(apiClient) {
    const db = await this.dbPromise;
    const pending = await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readonly");
      const req = tx.objectStore("offline_scrobbles").getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
    if (!pending.length) {
      return 0;
    }
    const cleanPayload = pending.map(({ artist, title, album, duration, timestamp, track_id }) => ({
      track_id,
      artist,
      title,
      album,
      duration,
      timestamp,
    }));
    await apiClient.request("/api/player/scrobble/batch", {
      method: "POST",
      body: JSON.stringify({ scrobbles: cleanPayload }),
    });
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readwrite");
      tx.objectStore("offline_scrobbles").clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    return pending.length;
  }
}

export const offlineStore = new OfflineStore();
```

### File: `src/musiclinker/web/js/audio-engine.js`
```javascript
/**
 * HTML5 Audio + ReplayGain Volume Normalization + Official 50% / 4-Minute Scrobble Tracker + MediaSession Bridge.
 */
export class AudioEngine {
  constructor({ onTimeUpdate, onTrackEnded, onScrobbleDue, onNowPlaying, onStateChange }) {
    this.audio = new Audio();
    this.audio.preload = "auto";
    this.audio.crossOrigin = "anonymous";

    this.onTimeUpdate = onTimeUpdate;
    this.onTrackEnded = onTrackEnded;
    this.onScrobbleDue = onScrobbleDue;
    this.onNowPlaying = onNowPlaying;
    this.onStateChange = onStateChange;

    this.currentTrack = null;
    this.normalizeEnabled = true;
    this.baseVolume = 0.9;
    this.scrobbledCurrentTrack = false;
    this.accumulatedPlaySeconds = 0;
    this.lastTickTimestamp = null;

    this._bindEvents();
  }

  _bindEvents() {
    this.audio.addEventListener("play", () => {
      this.lastTickTimestamp = performance.now();
      this.onStateChange?.(true);
      this._updateMediaSessionPlaybackState("playing");
    });

    this.audio.addEventListener("pause", () => {
      this._accumulateElapsed();
      this.lastTickTimestamp = null;
      this.onStateChange?.(false);
      this._updateMediaSessionPlaybackState("paused");
    });

    this.audio.addEventListener("timeupdate", () => {
      this._accumulateElapsed();
      const cur = this.audio.currentTime || 0;
      const dur = this.audio.duration || (this.currentTrack?.duration || 0);
      this.onTimeUpdate?.(cur, dur);

      // Official Last.fm Scrobble Rule: track >= 30s AND played >= 50% OR >= 240s (4 minutes)
      if (this.currentTrack && !this.scrobbledCurrentTrack && dur >= 30) {
        const threshold = Math.min(240, dur * 0.5);
        if (this.accumulatedPlaySeconds >= threshold) {
          this.scrobbledCurrentTrack = true;
          this.onScrobbleDue?.(this.currentTrack);
        }
      }
    });

    this.audio.addEventListener("ended", () => {
      this.onTrackEnded?.(this.currentTrack);
    });
  }

  _accumulateElapsed() {
    if (this.lastTickTimestamp !== null && !this.audio.paused) {
      const now = performance.now();
      const delta = (now - this.lastTickTimestamp) / 1000.0;
      if (delta > 0 && delta < 5.0) {
        this.accumulatedPlaySeconds += delta;
      }
      this.lastTickTimestamp = now;
    }
  }

  applyReplayGain(track) {
    if (!this.normalizeEnabled || !track) {
      this.audio.volume = this.baseVolume;
      return;
    }
    const db = Number(track.replaygain_db || 0);
    const linearFactor = Math.pow(10, db / 20);
    const targetVolume = Math.max(0.15, Math.min(1.0, this.baseVolume * linearFactor));
    this.audio.volume = targetVolume;
  }

  async loadAndPlay(track, pairToken = "") {
    this.currentTrack = track;
    this.scrobbledCurrentTrack = false;
    this.accumulatedPlaySeconds = 0;
    this.lastTickTimestamp = null;

    this.applyReplayGain(track);
    const sep = track.stream_url.includes("?") ? "&" : "?";
    const srcUrl = pairToken
      ? `${track.stream_url}${sep}pair_token=${encodeURIComponent(pairToken)}`
      : track.stream_url;

    this.audio.src = srcUrl;
    await this.audio.play();
    this._setupMediaSession(track);
    this.onNowPlaying?.(track);
  }

  togglePlay() {
    if (!this.audio.src) return;
    if (this.audio.paused) {
      this.audio.play();
    } else {
      this.audio.pause();
    }
  }

  seekToFraction(fraction) {
    const dur = this.audio.duration || (this.currentTrack?.duration || 0);
    if (dur > 0) {
      this.audio.currentTime = Math.max(0, Math.min(dur, dur * fraction));
    }
  }

  _setupMediaSession(track) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist_display,
      album: track.album || "MusicLinker",
      artwork: track.cover_url
        ? [{ src: track.cover_url, sizes: "512x512", type: "image/jpeg" }]
        : [{ src: "/icon.svg", sizes: "256x256", type: "image/svg+xml" }],
    });
  }

  bindMediaSessionHandlers({ onPrev, onNext, onToggle }) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.setActionHandler("play", onToggle);
    navigator.mediaSession.setActionHandler("pause", onToggle);
    navigator.mediaSession.setActionHandler("previoustrack", onPrev);
    navigator.mediaSession.setActionHandler("nexttrack", onNext);
  }

  _updateMediaSessionPlaybackState(state) {
    if ("mediaSession" in navigator) {
      navigator.mediaSession.playbackState = state;
    }
  }
}
```

### File: `src/musiclinker/web/js/app.js`
```javascript
/**
 * Main UI controller for MusicLinker SPA (Desktop & Android PWA).
 */
import { api } from "./api.js";
import { offlineStore } from "./offline-store.js";
import { AudioEngine } from "./audio-engine.js";

class MusicLinkerController {
  constructor() {
    this.tracks = [];
    this.offlineIds = new Set();
    this.currentIndex = -1;
    this.lyricsData = { synced: false, lines: [] };
    this.wakeLock = null;
    this.carModeActive = false;

    this.engine = new AudioEngine({
      onTimeUpdate: (cur, dur) => this.handleTimeUpdate(cur, dur),
      onTrackEnded: () => this.handleTrackEnded(),
      onScrobbleDue: (track) => this.handleScrobbleDue(track),
      onNowPlaying: (track) => this.handleNowPlaying(track),
      onStateChange: (playing) => this.updatePlayButtonUI(playing),
    });

    this.engine.bindMediaSessionHandlers({
      onPrev: () => this.prevTrack(),
      onNext: () => this.nextTrack(),
      onToggle: () => this.togglePlay(),
    });
  }

  async init() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    this.bindDOMEvents();
    await this.refreshOfflineState();
    await this.loadLibrary();
    offlineStore.flushOfflineScrobbles(api).catch(() => {});
  }

  async refreshOfflineState() {
    const cached = await offlineStore.getOfflineTracks();
    this.offlineIds = new Set(cached.map((t) => t.id));
  }

  async loadLibrary(search = "") {
    try {
      const data = await api.request(`/api/tracks?search=${encodeURIComponent(search)}`);
      this.tracks = data.tracks || [];
    } catch (_err) {
      // Offline fallback: load tracks saved in IndexedDB
      this.tracks = await offlineStore.getOfflineTracks();
    }
    this.renderTrackList();
  }

  renderTrackList() {
    const container = document.getElementById("track-list");
    if (!container) return;
    if (!this.tracks.length) {
      container.innerHTML = `<div class="p-8 text-center text-zinc-400">No tracks in library yet. Open the <b>Last.fm Extractor</b> tab to pull your top tracks!</div>`;
      return;
    }

    container.innerHTML = this.tracks
      .map((t, idx) => {
        const isOffline = this.offlineIds.has(t.id);
        const isCurrent = idx === this.currentIndex;
        const yearBadge = t.original_year || t.release_year || "";
        return `
          <div class="flex items-center justify-between p-3 rounded-xl transition ${
            isCurrent ? "bg-emerald-950/60 border border-emerald-500/40" : "hover:bg-zinc-800/70"
          }">
            <div class="flex items-center gap-3 min-w-0 flex-1 cursor-pointer" data-play-idx="${idx}">
              <div class="w-11 h-11 rounded-lg bg-zinc-800 flex items-center justify-center overflow-hidden shrink-0">
                ${
                  t.cover_url
                    ? `<img src="${t.cover_url}" class="w-full h-full object-cover" alt="cover"/>`
                    : `<span class="text-emerald-400 font-bold">♪</span>`
                }
              </div>
              <div class="min-w-0 flex-1">
                <div class="font-semibold text-zinc-100 truncate">${this.escape(t.title)}</div>
                <div class="text-xs text-zinc-400 truncate">
                  ${this.escape(t.artist_display)} • ${this.escape(t.album)} ${yearBadge ? `(${yearBadge})` : ""}
                </div>
              </div>
            </div>
            <div class="flex items-center gap-3 shrink-0 ml-2">
              <span class="text-xs text-zinc-400 hidden sm:inline">${t.lastfm_playcount || 0} plays</span>
              <button
                data-offline-idx="${idx}"
                title="${isOffline ? "Saved for Offline (tap to remove)" : "Save for Offline"}"
                class="px-2.5 py-1 rounded-lg text-xs font-medium border ${
                  isOffline
                    ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                    : "border-zinc-700 text-zinc-300 hover:border-zinc-500"
                }">
                ${isOffline ? "✓ Offline" : "↓ Offline"}
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    container.querySelectorAll("[data-play-idx]").forEach((el) => {
      el.addEventListener("click", () => this.playByIndex(Number(el.dataset.playIdx)));
    });
    container.querySelectorAll("[data-offline-idx]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = Number(btn.dataset.offlineIdx);
        await this.toggleOfflineTrack(this.tracks[idx], btn);
      });
    });
  }

  async toggleOfflineTrack(track, btnEl) {
    if (!track) return;
    btnEl.disabled = true;
    btnEl.textContent = "...";
    try {
      if (this.offlineIds.has(track.id)) {
        await offlineStore.removeOfflineTrack(track.id);
      } else {
        await offlineStore.saveTrackForOffline(track, api.getPairToken());
      }
      await this.refreshOfflineState();
      this.renderTrackList();
    } catch (err) {
      alert(`Offline cache error: ${err.message}`);
      this.renderTrackList();
    }
  }

  async playByIndex(index) {
    if (index < 0 || index >= this.tracks.length) return;
    this.currentIndex = index;
    const track = this.tracks[index];
    this.renderTrackList();
    this.updateNowPlayingBar(track);
    await this.engine.loadAndPlay(track, api.getPairToken());
    this.loadLyricsForTrack(track);
  }

  togglePlay() {
    if (this.currentIndex === -1 && this.tracks.length) {
      this.playByIndex(0);
      return;
    }
    this.engine.togglePlay();
  }

  prevTrack() {
    if (!this.tracks.length) return;
    const nextIdx = (this.currentIndex - 1 + this.tracks.length) % this.tracks.length;
    this.playByIndex(nextIdx);
  }

  async nextTrack() {
    if (!this.tracks.length) return;
    const current = this.tracks[this.currentIndex];
    if (current) {
      try {
        const res = await api.request(`/api/tracks/${current.id}/next-transition`);
        if (res.transition?.next_track) {
          const foundIdx = this.tracks.findIndex((t) => t.id === res.transition.next_track.id);
          if (foundIdx !== -1) {
            await this.playByIndex(foundIdx);
            return;
          }
        }
      } catch (_err) {
        // Ignore when offline
      }
    }
    const nextIdx = (this.currentIndex + 1) % this.tracks.length;
    await this.playByIndex(nextIdx);
  }

  handleTrackEnded() {
    this.nextTrack();
  }

  async handleNowPlaying(track) {
    try {
      await api.request("/api/player/now-playing", {
        method: "POST",
        body: JSON.stringify({
          track_id: track.id,
          artist: track.album_artist || track.artist_display,
          title: track.title,
          album: track.album,
          duration: track.duration,
        }),
      });
    } catch (_err) {
      // Non-fatal if offline
    }
  }

  async handleScrobbleDue(track) {
    const payload = {
      track_id: track.id,
      artist: track.album_artist || track.artist_display,
      title: track.title,
      album: track.album,
      duration: track.duration,
      timestamp: Math.floor(Date.now() / 1000),
    };
    try {
      await api.request("/api/player/scrobble", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (_err) {
      await offlineStore.queueOfflineScrobble(payload);
    }
  }

  async loadLyricsForTrack(track) {
    const box = document.getElementById("lyrics-container");
    if (box) box.innerHTML = `<p class="text-zinc-500 text-sm">Loading synced lyrics...</p>`;
    try {
      this.lyricsData = await api.request(track.lyrics_url);
    } catch (_err) {
      this.lyricsData = { synced: false, lines: [] };
    }
    this.renderLyrics();
  }

  renderLyrics() {
    const box = document.getElementById("lyrics-container");
    if (!box) return;
    if (!this.lyricsData.lines?.length) {
      box.innerHTML = `<p class="text-zinc-500 text-sm italic">No synced lyrics found for this track.</p>`;
      return;
    }
    box.innerHTML = this.lyricsData.lines
      .map(
        (l, idx) =>
          `<div id="lyric-line-${idx}" class="py-1.5 text-lg transition-all duration-200 text-zinc-500">${this.escape(
            l.text
          )}</div>`
      )
      .join("");
  }

  handleTimeUpdate(cur, dur) {
    const seekEl = document.getElementById("seek-bar");
    const timeEl = document.getElementById("time-display");
    if (seekEl && dur > 0) {
      seekEl.value = String(Math.round((cur / dur) * 1000));
    }
    if (timeEl) {
      timeEl.textContent = `${this.formatTime(cur)} / ${this.formatTime(dur)}`;
    }

    if (this.lyricsData.synced && this.lyricsData.lines.length) {
      let activeIdx = 0;
      for (let i = 0; i < this.lyricsData.lines.length; i++) {
        if (cur >= this.lyricsData.lines[i].time) {
          activeIdx = i;
        } else {
          break;
        }
      }
      this.lyricsData.lines.forEach((_, idx) => {
        const el = document.getElementById(`lyric-line-${idx}`);
        if (!el) return;
        if (idx === activeIdx) {
          el.className = "py-1.5 text-xl font-bold text-emerald-400 scale-[1.02] transition-all";
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        } else {
          el.className = "py-1.5 text-base text-zinc-500 transition-all";
        }
      });
    }
  }

  updateNowPlayingBar(track) {
    const titleEl = document.getElementById("np-title");
    const artistEl = document.getElementById("np-artist");
    const carTitle = document.getElementById("car-title");
    const carArtist = document.getElementById("car-artist");
    if (titleEl) titleEl.textContent = track.title;
    if (artistEl) artistEl.textContent = `${track.artist_display} • ${track.album}`;
    if (carTitle) carTitle.textContent = track.title;
    if (carArtist) carArtist.textContent = track.artist_display;
  }

  updatePlayButtonUI(playing) {
    const btn = document.getElementById("btn-play");
    const carBtn = document.getElementById("car-btn-play");
    const label = playing ? "⏸ Pause" : "▶ Play";
    if (btn) btn.textContent = label;
    if (carBtn) carBtn.textContent = label;
  }

  async toggleCarMode() {
    this.carModeActive = !this.carModeActive;
    const overlay = document.getElementById("car-mode-overlay");
    if (overlay) {
      overlay.classList.toggle("hidden", !this.carModeActive);
    }
    if (this.carModeActive && "wakeLock" in navigator) {
      try {
        this.wakeLock = await navigator.wakeLock.request("screen");
      } catch (_err) {}
    } else if (this.wakeLock) {
      try {
        await this.wakeLock.release();
      } catch (_err) {}
      this.wakeLock = null;
    }
  }

  bindDOMEvents() {
    document.getElementById("btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("car-btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("car-btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("car-btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-car-mode")?.addEventListener("click", () => this.toggleCarMode());
    document.getElementById("btn-exit-car")?.addEventListener("click", () => this.toggleCarMode());

    document.getElementById("seek-bar")?.addEventListener("input", (e) => {
      this.engine.seekToFraction(Number(e.target.value) / 1000.0);
    });

    document.getElementById("search-input")?.addEventListener("input", (e) => {
      this.loadLibrary(e.target.value);
    });

    document.getElementById("btn-start-extract")?.addEventListener("click", () => this.startExtractionJob());
    document.getElementById("btn-show-sync")?.addEventListener("click", () => this.openSyncModal());
    document.getElementById("btn-save-settings")?.addEventListener("click", () => this.saveSettings());
  }

  async startExtractionJob() {
    const username = document.getElementById("ext-username")?.value.trim();
    const period = document.getElementById("ext-period")?.value || "12month";
    const minPlays = Number(document.getElementById("ext-min-plays")?.value || 10);
    const cap = Number(document.getElementById("ext-cap")?.value || 25);
    const bitrate = Number(document.getElementById("ext-bitrate")?.value || 320);

    if (!username) {
      alert("Please enter a Last.fm username.");
      return;
    }

    const logBox = document.getElementById("job-logs");
    if (logBox) logBox.textContent = "Starting extraction job...";

    try {
      const job = await api.request("/api/jobs/extract", {
        method: "POST",
        body: JSON.stringify({
          username,
          period,
          min_plays: minPlays,
          download_cap: cap,
          bitrate_kbps: bitrate,
          download_enabled: true,
        }),
      });
      api.subscribeJobEvents(job.job_id, (state) => {
        if (logBox) {
          logBox.textContent = (state.logs || []).join("\n");
          logBox.scrollTop = logBox.scrollHeight;
        }
        const bar = document.getElementById("job-progress-bar");
        if (bar) bar.style.width = `${state.progress_pct}%`;
        if (state.status === "completed") {
          this.loadLibrary();
        }
      });
    } catch (err) {
      alert(`Failed starting job: ${err.message}`);
    }
  }

  async openSyncModal() {
    const modal = document.getElementById("sync-modal");
    const urlBox = document.getElementById("sync-pairing-url");
    if (!modal) return;
    modal.classList.remove("hidden");
    try {
      const info = await api.request("/api/sync/info");
      if (urlBox) urlBox.textContent = info.pairing_url;
    } catch (err) {
      if (urlBox) urlBox.textContent = err.message;
    }
  }

  async saveSettings() {
    const apiKey = document.getElementById("set-api-key")?.value.trim();
    const apiSecret = document.getElementById("set-api-secret")?.value.trim();
    const username = document.getElementById("set-username")?.value.trim();
    await api.request("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        lastfm_api_key: apiKey || undefined,
        lastfm_api_secret: apiSecret || undefined,
        lastfm_username: username || undefined,
      }),
    });
    alert("Settings saved to SQLite database.");
  }

  formatTime(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const rem = String(s % 60).padStart(2, "0");
    return `${m}:${rem}`;
  }

  escape(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }
}

window.MusicLinkerApp = new MusicLinkerController();
window.addEventListener("DOMContentLoaded", () => window.MusicLinkerApp.init());
```

### File: `src/musiclinker/web/index.html`
```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>MusicLinker — Personal Player & Last.fm Sync</title>
  <link rel="manifest" href="/manifest.json" />
  <link rel="icon" href="/icon.svg" type="image/svg+xml" />
  <meta name="theme-color" content="#10b981" />
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-zinc-950 text-zinc-100 min-h-screen flex flex-col select-none">
  <!-- Top Header -->
  <header class="border-b border-zinc-800/80 bg-zinc-900/60 backdrop-blur px-5 py-3.5 flex items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      <img src="/icon.svg" alt="MusicLinker" class="w-8 h-8" />
      <div>
        <h1 class="font-bold text-lg tracking-tight leading-none">MusicLinker <span class="text-xs text-emerald-400 font-mono">v2.0</span></h1>
        <p class="text-xs text-zinc-400">Offline-First Personal Music Server & Scrobbler</p>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <input
        id="search-input"
        type="search"
        placeholder="Search songs, artists, albums..."
        class="bg-zinc-900 border border-zinc-700 rounded-xl px-3.5 py-1.5 text-sm focus:outline-none focus:border-emerald-500 w-44 sm:w-64"
      />
      <button id="btn-car-mode" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium">
        🚗 Car Mode
      </button>
      <button id="btn-show-sync" class="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-semibold text-xs">
        📱 Phone Sync
      </button>
    </div>
  </header>

  <!-- Main Workspace Grid -->
  <main class="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-5 p-5 overflow-hidden">
    <!-- Left Column: Track Library (7 cols) -->
    <section class="lg:col-span-7 bg-zinc-900/50 border border-zinc-800/80 rounded-2xl p-4 flex flex-col overflow-hidden">
      <div class="flex items-center justify-between mb-3">
        <h2 class="font-semibold text-sm uppercase tracking-wider text-zinc-400">Local Library</h2>
      </div>
      <div id="track-list" class="flex-1 overflow-y-auto space-y-1.5 pr-1 max-h-[60vh]"></div>
    </section>

    <!-- Right Column: Synced Karaoke Lyrics + Extractor & Settings (5 cols) -->
    <section class="lg:col-span-5 flex flex-col gap-5 overflow-y-auto max-h-[65vh]">
      <!-- Karaoke Synced Lyrics Card -->
      <div class="bg-zinc-900/50 border border-zinc-800/80 rounded-2xl p-4">
        <h2 class="font-semibold text-xs uppercase tracking-wider text-zinc-400 mb-2">Synced Lyrics (LRCLIB)</h2>
        <div id="lyrics-container" class="h-44 overflow-y-auto space-y-1 px-2 text-center">
          <p class="text-zinc-500 text-sm">Select a song to view timestamped karaoke lyrics.</p>
        </div>
      </div>

      <!-- Last.fm Extraction Studio -->
      <div class="bg-zinc-900/50 border border-zinc-800/80 rounded-2xl p-4 space-y-3">
        <h2 class="font-semibold text-xs uppercase tracking-wider text-zinc-400">Last.fm Extractor & Downloader</h2>
        <div class="grid grid-cols-2 gap-2.5 text-sm">
          <input id="ext-username" type="text" placeholder="Last.fm Username" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2" />
          <select id="ext-period" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2">
            <option value="12month">Last 12 Months</option>
            <option value="6month">Last 6 Months</option>
            <option value="3month">Last 3 Months</option>
            <option value="1month">Last 1 Month</option>
            <option value="overall">Overall All-Time</option>
          </select>
          <input id="ext-min-plays" type="number" value="10" min="1" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2" title="Minimum Plays" />
          <input id="ext-cap" type="number" value="25" min="1" max="500" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2" title="Download Cap" />
          <select id="ext-bitrate" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 col-span-2">
            <option value="320">320 kbps MP3 (Audiophile)</option>
            <option value="192">192 kbps MP3 (Compact Mobile)</option>
          </select>
        </div>
        <button id="btn-start-extract" class="w-full py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-sm">
          Extract & Download Qualifying Tracks
        </button>
        <div class="w-full bg-zinc-800 h-2 rounded-full overflow-hidden">
          <div id="job-progress-bar" class="bg-emerald-500 h-full w-0 transition-all"></div>
        </div>
        <pre id="job-logs" class="bg-zinc-950 border border-zinc-800 rounded-xl p-2.5 text-xs text-zinc-400 h-28 overflow-y-auto font-mono">Idle.</pre>
      </div>

      <!-- API Key Settings Card -->
      <div class="bg-zinc-900/50 border border-zinc-800/80 rounded-2xl p-4 space-y-2.5">
        <h2 class="font-semibold text-xs uppercase tracking-wider text-zinc-400">Last.fm Credentials</h2>
        <input id="set-api-key" type="text" placeholder="Last.fm API Key" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-1.5 text-sm" />
        <input id="set-api-secret" type="password" placeholder="Last.fm API Secret" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-1.5 text-sm" />
        <input id="set-username" type="text" placeholder="Default Last.fm Username" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-1.5 text-sm" />
        <button id="btn-save-settings" class="w-full py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold">
          Save Credentials
        </button>
      </div>
    </section>
  </main>

  <!-- Bottom Persistent Player Bar -->
  <footer class="border-t border-zinc-800 bg-zinc-900/95 backdrop-blur px-5 py-3 flex flex-col sm:flex-row items-center justify-between gap-4">
    <div class="min-w-0 text-center sm:text-left">
      <div id="np-title" class="font-semibold text-sm truncate">Not Playing</div>
      <div id="np-artist" class="text-xs text-zinc-400 truncate">Select a track from your library</div>
    </div>
    <div class="flex flex-col items-center gap-1.5 flex-1 max-w-xl w-full">
      <div class="flex items-center gap-3">
        <button id="btn-prev" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs">⏮ Prev</button>
        <button id="btn-play" class="px-5 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-sm">▶ Play</button>
        <button id="btn-next" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs">Next ⏭</button>
      </div>
      <div class="flex items-center gap-3 w-full">
        <input id="seek-bar" type="range" min="0" max="1000" value="0" class="flex-1 accent-emerald-500" />
        <span id="time-display" class="text-xs font-mono text-zinc-400">0:00 / 0:00</span>
      </div>
    </div>
  </footer>

  <!-- High-Contrast Car Driving Mode Overlay (Wake-Lock Enabled) -->
  <div id="car-mode-overlay" class="hidden fixed inset-0 bg-black z-50 flex flex-col justify-between p-8">
    <div class="flex justify-between items-center">
      <span class="text-emerald-400 font-bold uppercase tracking-widest text-sm">🚗 Car Driving Mode (Wake-Lock Active)</span>
      <button id="btn-exit-car" class="px-5 py-2.5 rounded-2xl bg-zinc-800 text-white font-bold text-lg">Exit</button>
    </div>
    <div class="text-center my-auto space-y-3">
      <div id="car-title" class="text-4xl sm:text-6xl font-extrabold text-white">Not Playing</div>
      <div id="car-artist" class="text-2xl sm:text-3xl text-emerald-400 font-semibold">—</div>
    </div>
    <div class="grid grid-cols-3 gap-6 h-36">
      <button id="car-btn-prev" class="rounded-3xl bg-zinc-900 border-2 border-zinc-700 text-3xl font-bold">⏮ PREV</button>
      <button id="car-btn-play" class="rounded-3xl bg-emerald-500 text-zinc-950 text-4xl font-extrabold">▶ PLAY</button>
      <button id="car-btn-next" class="rounded-3xl bg-zinc-900 border-2 border-zinc-700 text-3xl font-bold">NEXT ⏭</button>
    </div>
  </div>

  <!-- Phone LAN Pairing Modal -->
  <div id="sync-modal" class="hidden fixed inset-0 bg-black/75 z-40 flex items-center justify-center p-4">
    <div class="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 max-w-md w-full space-y-4">
      <h3 class="font-bold text-lg">📱 Android Wi-Fi Sync & Pairing</h3>
      <p class="text-xs text-zinc-400">
        Open this URL on your Android phone connected to the same Wi-Fi network to install the PWA and cache tracks for offline playback:
      </p>
      <div id="sync-pairing-url" class="p-3 rounded-xl bg-zinc-950 border border-zinc-800 font-mono text-xs text-emerald-400 break-all"></div>
      <button onclick="document.getElementById('sync-modal').classList.add('hidden')" class="w-full py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-sm font-semibold">
        Close
      </button>
    </div>
  </div>

  <script type="module" src="/js/app.js"></script>
</body>
</html>
```

### File: `start_player.py`
```python
"""Bootstrap launcher for MusicLinker Desktop Application."""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure src/ is on sys.path even if package was not pip-installed yet
SRC_DIR = Path(__file__).resolve().parent / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from musiclinker.entrypoints.desktop import main

if __name__ == "__main__":
    main()
```

### File: `MusicLinker.spec`
```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ("src/musiclinker/web", "musiclinker/web"),
    ("icon.ico", "."),
    ("icon.png", "."),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

for pkg in ("imageio_ffmpeg", "yt_dlp", "mutagen", "webview", "pystray"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

a = Analysis(
    ["start_player.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MusicLinker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)
```

### File: `start_player.bat`
```bat
@echo off
title MusicLinker Desktop Player
py start_player.py %*
```

### File: `run_cli.bat`
```bat
@echo off
title MusicLinker Interactive CLI Wizard
set PYTHONPATH=%~dp0src
py -m musiclinker.entrypoints.cli %*
pause
```

### File: `run_web.bat`
```bat
@echo off
title MusicLinker Streamlit Analytics Dashboard
set PYTHONPATH=%~dp0src
py -m streamlit run src/musiclinker/entrypoints/streamlit_app.py
```
