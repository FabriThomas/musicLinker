# MusicLinker v2.0 🎵

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

> **Offline-first personal music streaming server, Last.fm library extractor, and multi-artist ID3v2.4 tagger for Windows and Android.**

MusicLinker bridges the gap between your streaming scrobble history and a permanent, high-fidelity local audio library. It extracts your top scrobbled tracks from Last.fm, locates best-match audio via YouTube, enriches files with audiophile-grade metadata and synced karaoke lyrics, and serves your personal collection across your home Wi-Fi network.

---

## ✨ Features

- **Layered Clean Architecture**: Built with strict separation of concerns (`core`, `domain`, `infrastructure`, `services`, `api`, `entrypoints`).
- **Unified SQLite WAL Storage**: Single source of truth in `library.db` with Write-Ahead Logging (`WAL`), thread-safe pooling, and automated schema migrations.
- **Asynchronous SSE Job Pipeline**: Bounded `ThreadPoolExecutor` download manager with real-time Server-Sent Events (`GET /api/jobs/{id}/events`) and UI progress updates.
- **Audiophile-Grade Metadata Tagging**:
  - Full **ID3v2.4** multi-artist tagging (`TPE1` null-delimited `\x00` values, `TPE2` album artist).
  - MusicBrainz integration for original release year (`TDOR`) vs. remaster year (`TDRC`).
  - High-resolution front cover art embedding (`APIC`).
  - Synced karaoke lyrics embedding (`USLT`) and sidecar `.lrc` file export from LRCLIB.
  - Non-destructive ReplayGain loudness normalization (`TXXX:REPLAYGAIN_TRACK_GAIN`).
- **Modern Responsive Web Player**:
  - Native ES6 SPA with zero build step requirement.
  - High-contrast **Car Driving Mode** with screen Wake-Lock support.
  - Real-time timestamped **Karaoke Lyrics** display.
- **Android PWA & Offline Scrobbling**:
  - Progressive Web App installable on Android.
  - Client-side track caching via Cache API.
  - Offline scrobble outbox stored in IndexedDB (`musiclinker_pwa_db`), automatically synced in batches when reconnected.
- **LAN Pairing & Security**:
  - Localhost requests (`127.0.0.1`) auto-trusted.
  - LAN devices (e.g. phones on Wi-Fi) authenticate smoothly via QR code token pairing.
- **Flexible Network Configuration**:
  - Easily configure host and port in `host_config.json` (defaults to `0.0.0.0:8101`).

---

## 🚀 Quick Start

### Option A: Pre-compiled Windows Binary (Recommended)

1. Download `MusicLinker-v2.0.0-windows-x64.zip` from the [Latest Release](https://github.com/FabriThomas/musicLink/releases/latest).
2. Extract the archive.
3. Edit `host_config.json` if you wish to adjust the bind address or port (defaults to `0.0.0.0:8101`).
4. Double-click `MusicLinker.exe` (or `start_player.bat`).

---

### Option B: Running from Source

#### Prerequisites
- Python 3.11 or higher
- Git

#### Installation
```powershell
# 1. Clone repository
git clone https://github.com/FabriThomas/musicLink.git
cd musicLink

# 2. Install dependencies and link package in editable mode
py -m pip install -e .

# 3. Launch Desktop Application
py start_player.py
```

Other available launch entrypoints:
```powershell
# Interactive CLI Wizard
py -m musiclinker.entrypoints.cli

# Streamlit Analytics Dashboard
py -m streamlit run src/musiclinker/entrypoints/streamlit_app.py
```

---

## ⚙️ Configuration (`host_config.json`)

To change the host or port the application binds to, edit `host_config.json` in the root folder:

```json
{
  "host": "0.0.0.0",
  "port": 8101
}
```

- `0.0.0.0` allows other devices (like your phone on local Wi-Fi) to connect.
- `127.0.0.1` restricts access to your local PC only.

You can also pass arguments directly:
```powershell
py start_player.py --host 127.0.0.1 --port 8101
```

---

## 🔀 Built-in Reverse Proxy (`/v1`)

MusicLinker includes a built-in reverse proxy that lets you route requests from `http://localhost:<port>/v1` to an external server URL (such as an LLM endpoint, API bridge, or remote server) with optional password protection:

- **Configure in Settings UI**: Open the web app -> **⚙ Settings** -> **Built-in Reverse Proxy (`/v1`)**.
- **Settings fields**:
  - **Target Server URL**: e.g. `https://api.openai.com/v1` or custom remote server.
  - **Proxy Name**: Custom identifier tagged in `X-Proxy-Name` header.
  - **Proxy Password**: Password callers must provide via Bearer token (`Authorization: Bearer <password>`), `X-Proxy-Password` header, or `?password=<password>` query parameter.
- **Calling the proxy**:
  ```bash
  curl http://localhost:8101/v1/chat/completions \
    -H "Authorization: Bearer <your-proxy-password>" \
    -H "Content-Type: application/json" \
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'
  ```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
