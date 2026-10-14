# MusicLinker v3.0 — Architectural Refactoring, ADRs, Native Android APK & Complete Codebase Blueprint

> **Agent Implementation Directive**: This document contains the complete architectural review, Architecture Decision Records (ADRs), target directory structure, migration steps, and full production-ready source code for **MusicLinker v3.0** (Windows Desktop + FastAPI Server + Native Android `.apk` via Capacitor). An autonomous coding agent can parse each `### File: <relative_path>` section below and write the contents directly to disk to construct the entire ecosystem.

## 1. Architectural Critique & High-Impact Improvements Establish a Production-Grade Ecosystem

| Subsystem | Bottleneck / Requirement Identified | v3.0 Architectural & UI Upgrade | Impact |
| :--- | :--- | :--- | :--- |
| **Native Android APK Packaging** | Browser-only PWA installation requires Chrome menus and lacks native APK distribution or configurable remote server origin when bundled locally inside an APK. | **Capacitor Native Android APK (`android/`, `capacitor.config.json`, `build_apk.bat`)**: Packages `src/musiclinker/web` into an installable `MusicLinker.apk` with cleartext LAN streaming (`usesCleartextTraffic="true"`), wake-lock & foreground media playback permissions, dynamic PC Server Origin pairing (`localStorage.ml_server_origin`), and 100% offline playback from local IndexedDB/Cache storage. | Installable `.apk` on any Android phone that pairs with your PC over Wi-Fi and plays cached albums/tracks 100% offline anywhere. |
| **3-Layer Hierarchical Selection Mode** | Users need a structured way to build custom playback queues or offline batches across large libraries without manually unchecking hundreds of artists/albums. | **3-Layer Selection Studio (`Artists -> Albums -> Tracks`)**: **Layer 1 (Artists)** starts 100% *unselected*; **Layer 2 (Albums, optional filter)** starts 100% *unselected* (if left untouched, all albums by the chosen artists pass through; if specific albums are ticked, filters to those albums); **Layer 3 (Tracks)** starts **100% *selected*** for all matching songs so you can immediately play, shuffle, or cache them offline. | Effortless multi-artist/multi-album queue building with exact default selection semantics. |
| **Play by Album, Randomizer & Track # / Alphabetical Ordering** | Tracks lacked `track_number` (`TRCK`) indexing in SQLite, preventing true album-order playback (`1..N`). | **Track # (`TRCK`) Indexing + Queue Order Modes + Fisher-Yates Randomizer**: Stores `track_number` in SQLite and MP3 ID3v2.4 `TRCK` frames. Adds **Play by Album**, **Sort by Track # (`1..N`)**, **Sort Alphabetically (`A–Z` / `Z–A`)**, **Sort by Plays**, and a **Randomizer (`🔀 Shuffle`)** mode. | Authentic gapless-style album listening in studio tracklist order or instant randomized shuffle. |
| **Full Album Downloader (Server + Android Offline)** |Previously only extracted top Last.fm tracks or single YouTube queries; could not download a complete studio album by name. | **MusicBrainz / YouTube Album Tracklist Resolver + Batch Album Downloader**: `POST /api/albums/lookup` resolves the official tracklist (`1..N`) for any `Artist + Album`; `POST /api/jobs/download-album` downloads every track, embeds `TRCK`, `TALB`, `TPE1`, `TPE2`, `TDOR`, `APIC`, and 3-tier `.lrc` lyrics. Also adds 1-tap **"↓ Cache Full Album Offline"** on Android APK. | Download complete studio albums on PC or cache entire albums onto your Android phone in one click. |
| **Synced Lyrics Scroll Hijacking & Rough Sync** | `scrollIntoView()` hijacked parent scrollbars; tracks without LRCLIB entries had no sync indicator. | **Isolated `#lyrics-container` Scroll Math + 3-Tier Sync (`exact_lrc`, `youtube_captions`, `rough_interpolated`)**: Never scrolls parent containers; pauses on manual scroll; shows live vocal progress `%` and `±0.5s` offset controls. | Zero scroll glitches while checking active downloads, plus rough sync position for every track. |
| **Internal Album Auto-Filter & LLM Smart Curator** | Wrong/noisy album names (`- Single`, `(Deluxe)`) and duplicate track versions (`Remaster` vs `Edit`) cluttered the library. | **Auto-Regex Cleaner + `album_aliases` + OpenAI-Compatible LLM Proxy Curator**: Strips edition noise automatically, renames albums/tracks across SQLite + MP3 ID3 tags, and connects to any LLM API link/proxy to deduplicate tracks and canonicalize albums. | Self-healing discography metadata. |

## 2. Architecture Decision Records (ADRs) Formalize Core Engineering Choices

### ADR-001: Adoption of Clean / Layered Package Architecture (`src/musiclinker`)
* **Status**: Accepted
* **Context**: Flat root scripts caused circular dependencies and duplicated logic across Desktop, Web, CLI, and Streamlit.
* **Decision**: Organize backend and shared web code under `src/musiclinker/{core,domain,infrastructure,services,api,entrypoints,web}` and native mobile packaging under `android/`.
* **Consequences**: Strict inward dependency flow and single source of truth.

### ADR-002: Unified SQLite WAL Persistence, `track_number` Indexing & `album_aliases`
* **Status**: Accepted
* **Context**: Supporting Play by Album in true tracklist order (`1..N`) and persistent album renaming requires first-class schema support.
* **Decision**: Run `library.db` in `WAL` mode (`PRAGMA journal_mode=WAL`) with `track_number INTEGER NOT NULL DEFAULT 0` on `tracks` (auto-migrated via `ALTER TABLE` for existing databases) and `album_aliases` for permanent album name overrides.
* **Consequences**: Zero lock contention and accurate `1..N` track ordering inside albums.

### ADR-003: Isolated Container Scroll Math & 3-Tier Synced/Rough Lyrics Engine
* **Status**: Accepted
* **Context**: `Element.scrollIntoView()` scrolled ancestor containers and the main page on every `timeupdate` tick.
* **Decision**: Use isolated `box.scrollTo({ top })` math only when `activeIdx` changes, lock auto-scroll for 4.5s on user wheel/touch interaction, and resolve lyrics across 3 tiers (`exact_lrc` from LRCLIB -> `youtube_captions` `.vtt` from `yt-dlp` -> `rough_interpolated` proportional line timing).
* **Consequences**: Users can browse downloads, albums, or the 3-layer selector while lyrics scroll smoothly in their own pane.

### ADR-004: Pluggable LLM Smart Curator via Custom API Link or Reverse Proxy
* **Status**: Accepted
* **Context**: Duplicate versions of tracks and non-canonical album titles require semantic disambiguation.
* **Decision**: Provide `LLMClient` + `CurationService` compatible with any `/v1/chat/completions` endpoint or proxy URL, backed by a deterministic local fuzzy matcher (`SequenceMatcher`) when offline.
* **Consequences**: Automated deduplication and album canonicalization before or after downloading.

### ADR-005: Native Android APK Architecture via Capacitor & Remote Server Origin Bridge
* **Status**: Accepted
* **Context**: When running as a native Android `.apk`, the UI assets are served locally inside the Capacitor WebView (`https://localhost` or `capacitor://localhost`), rather than being loaded from the PC's HTTP origin (`http://192.168.x.x:8000`). Furthermore, Android 9+ blocks HTTP LAN traffic by default unless explicitly permitted.
* **Decision**:
  1. Package `src/musiclinker/web` using Capacitor (`capacitor.config.json`, `android/app/src/main/AndroidManifest.xml` with `android:usesCleartextTraffic="true"` and foreground media permissions).
  2. Upgrade `MusicLinkerAPI` (`web/js/api.js`), `OfflineStore` (`web/js/offline-store.js`), and `AudioEngine` (`web/js/audio-engine.js`) to resolve a dynamic `serverOrigin` (`localStorage.getItem("ml_server_origin")`). When running on the PC or LAN browser, `serverOrigin` defaults to `window.location.origin`; inside the Android APK, the user enters or scans the PC's LAN URL (`http://192.168.1.50:8000?pair_token=...`) once, which stores both `ml_server_origin` and `ml_pair_token`.
  3. Store offline-cached MP3 blobs in the APK's persistent `CacheStorage` keyed by canonical path (`/api/tracks/{id}/file`) and intercept playback in `AudioEngine` via `caches.match()` -> `URL.createObjectURL(blob)` so cached songs and albums play 100% offline inside the native APK even when the Service Worker is bypassed or the PC is turned off.
* **Consequences**: The exact same codebase runs as a Windows `.exe`, a mobile PWA, and a standalone native Android `.apk` with bulletproof offline playback.

### ADR-006: 3-Layer Hierarchical Selection State Machine (`Artists -> Albums -> Tracks`)
* **Status**: Accepted
* **Context**: The user requested a layered selection mode where you select by layers: first which artists, then optionally which albums, then which tracks—with artists and albums starting *fully unselected* and the resulting tracks starting *fully selected*.
* **Decision**: Implement a dedicated **Layered Selector Mode** in `app.js`:
  - **Layer 1 (Artists)**: `layerSelectedArtists = new Set()` (starts empty / unselected). Toggling an artist adds/removes it from the active filter and recomputes Layer 2 & Layer 3.
  - **Layer 2 (Albums — Optional)**: `layerSelectedAlbums = new Set()` (starts empty / unselected). Displays all albums belonging to the artists selected in Layer 1. If **0 albums** are selected in Layer 2, all albums from the selected artists pass through to Layer 3; if **1 or more albums** are selected in Layer 2, only tracks from those specific albums pass through to Layer 3.
  - **Layer 3 (Tracks)**: Whenever the candidate track pool from Layer 1 + Layer 2 changes, all tracks entering that pool default to **SELECTED** (`layerExcludedTrackIds` tracks only what the user explicitly unchecks, so new matching tracks are 100% checked by default).
* **Consequences**: Matches the exact user mental model—pick artists (and optionally narrow by albums) from a clean unselected state, and immediately get a fully-selected track list ready to play, randomize, or save offline.

## 3. Target Folder Structure & File Manifest Map Every Component

| File Path | Architectural Layer | Responsibility |
| :--- | :--- | :--- |
| `pyproject.toml` | Python Packaging | PEP 621 project metadata, dependencies, and script entrypoints. |
| `package.json` | APK / Capacitor Build | Node/Capacitor dependencies and scripts (`npm run apk:sync`, `npm run apk:build`) for building the Android `.apk`. |
| `capacitor.config.json` | APK / Capacitor Config | Configures Capacitor app ID (`com.fabrithomas.musiclinker`), webDir (`src/musiclinker/web`), and cleartext HTTP LAN allowances. |
| `android/build.gradle` | Android APK Root Gradle | Top-level Android Gradle configuration. |
| `android/settings.gradle` | Android APK Settings | Includes `:app` and Capacitor Android bridge module. |
| `android/app/build.gradle` | Android APK App Module | Configures Android SDK 34, versionCode, minSdk 26, and APK build types. |
| `android/app/src/main/AndroidManifest.xml` | Android APK Manifest | Declares `INTERNET`, `WAKE_LOCK`, `FOREGROUND_SERVICE_MEDIA_PLAYBACK`, and `usesCleartextTraffic="true"` for LAN Wi-Fi streaming. |
| `android/app/src/main/java/com/fabrithomas/musiclinker/MainActivity.java` | Android Native Activity | Capacitor `BridgeActivity` configuring WebView media playback without user gesture restrictions for seamless track transitions. |
| `build_apk.bat` | Windows APK Builder | 1-click Windows script that syncs web assets into `android/` and runs `gradlew assembleDebug` to output `MusicLinker-debug.apk`. |
| `.gitignore` | Configuration | Excludes `.venv/`, `build/`, `dist/`, `.app_data/`, `downloads/`, `library.db*`, and secrets. |
| `src/musiclinker/__init__.py` | Package Root | Exposes package version (`3.0.0`). |
| `src/musiclinker/core/__init__.py` | Core Layer | Package marker. |
| `src/musiclinker/core/paths.py` | Core Layer | Frozen-aware (`sys._MEIPASS`) path resolver. |
| `src/musiclinker/core/logging_config.py` | Core Layer | UTF-8 safe structured logging configuration. |
| `src/musiclinker/core/exceptions.py` | Core Layer | Custom exception hierarchy. |
| `src/musiclinker/core/security.py` | Core Layer | LAN IP discovery, pairing URL builder, and loopback/token verifier. |
| `src/musiclinker/domain/__init__.py` | Domain Layer | Package marker. |
| `src/musiclinker/domain/models.py` | Domain Layer | Dataclasses & Pydantic schemas including `track_number`, `AlbumDownloadRequest`, `AlbumTrackItem`, and LLM curation models. |
| `src/musiclinker/infrastructure/__init__.py` | Infrastructure Layer | Package marker. |
| `src/musiclinker/infrastructure/hasher.py` | Infrastructure Layer | Track normalizer, multi-artist splitter, automatic album name cleaner, and SHA-256 hasher. |
| `src/musiclinker/infrastructure/db.py` | Infrastructure Layer | Thread-safe SQLite WAL repository with `track_number`, sort modes (`track_num`, `alpha_asc`, `alpha_desc`, `plays_desc`, `recent`), and `album_aliases`. |
| `src/musiclinker/infrastructure/lastfm_client.py` | Infrastructure Layer | Bi-directional Last.fm client (Top Tracks, Calendar Year Scrobbles, Album Info tracklist lookup, Now Playing, Batch Scrobble). |
| `src/musiclinker/infrastructure/youtube_client.py` | Infrastructure Layer | Keyless `yt-dlp` searcher, playlist/album expander, MP3 staging downloader, and `.vtt` subtitle extractor. |
| `src/musiclinker/infrastructure/musicbrainz_client.py` | Infrastructure Layer | MusicBrainz & Cover Art Archive resolver with `fetch_album_tracklist()` returning ordered `1..N` album tracks and `track_number`. |
| `src/musiclinker/infrastructure/lyrics_client.py` | Infrastructure Layer | 3-tier lyric resolver (`exact_lrc`, `youtube_captions`, `rough_interpolated`). |
| `src/musiclinker/infrastructure/tagger.py` | Infrastructure Layer | ID3v2.4 tagger writing `TRCK` (track number), multi-artist `\x00` `TPE1`, `TPE2`, `TDOR`, `TDRC`, `TXXX` ReplayGain, `USLT`, and `APIC`. |
| `src/musiclinker/infrastructure/llm_client.py` | Infrastructure Layer | OpenAI-compatible API / Proxy LLM client. |
| `src/musiclinker/services/__init__.py` | Service Layer | Package marker. |
| `src/musiclinker/services/library_service.py` | Service Layer | Filesystem scanner, internal album/track renamer, hierarchy summary builder, and transition resolver. |
| `src/musiclinker/services/curation_service.py` | Service Layer | LLM + heuristic library auditor and deduplicator. |
| `src/musiclinker/services/scrobble_service.py` | Service Layer | Real-time Now Playing and offline scrobble queue manager. |
| `src/musiclinker/services/job_service.py` | Service Layer | Worker pool executing Last.fm extractions, direct downloads, and **Full Album Downloads** (`start_album_download_job`). |
| `src/musiclinker/api/__init__.py` | API Layer | Package marker. |
| `src/musiclinker/api/app.py` | API Layer | FastAPI server with HTTP 206 streaming, album tracklist lookup (`/api/albums/lookup`), full album download (`/api/jobs/download-album`), and sorting. |
| `src/musiclinker/entrypoints/__init__.py` | Entrypoints Layer | Package marker. |
| `src/musiclinker/entrypoints/desktop.py` | Entrypoints Layer | Native Windows application (`pywebview` WebView2 + `pystray` system tray). |
| `src/musiclinker/entrypoints/cli.py` | Entrypoints Layer | Interactive CLI wizard and headless runner (supports `--album` download mode). |
| `src/musiclinker/entrypoints/streamlit_app.py` | Entrypoints Layer | Streamlit analytics dashboard. |
| `src/musiclinker/web/manifest.json` | Web / APK Layer | Progressive Web App manifest. |
| `src/musiclinker/web/icon.svg` | Web / APK Layer | Vector branding icon. |
| `src/musiclinker/web/css/styles.css` | Web / APK Layer | Custom range sliders (`--progress`), glassmorphic cards, and 3-layer selector styles. |
| `src/musiclinker/web/sw.js` | Web / APK Layer | Service Worker for offline shell and MP3 caching. |
| `src/musiclinker/web/js/api.js` | Web / APK Layer | APK-aware fetch wrapper supporting remote PC `serverOrigin` (`http://<LAN_IP>:8000`) and SSE job streams. |
| `src/musiclinker/web/js/offline-store.js` | Web / APK Layer | IndexedDB + Cache API offline manager with direct Blob URL playback for offline APK mode and batch album offline caching. |
| `src/musiclinker/web/js/audio-engine.js` | Web / APK Layer | HTML5 Audio + offline Blob fallback + ReplayGain + MediaSession bridge. |
| `src/musiclinker/web/js/app.js` | Web / APK Layer | Full UI controller with **Play by Album**, **Randomizer (`🔀`)**, **Sort by Track # / Alphabet**, **3-Layer Selection Mode (`Artists -> Albums -> Tracks`)**, and **Full Album Downloader**. |
| `src/musiclinker/web/index.html` | Web / APK Layer | Studio & Mobile APK interface with view switcher (`Standard Library` vs `3-Layer Selector`), Album Downloader studio, and APK Server Connection modal. |
| `start_player.py` | Root Bootstrap | Root launcher invoking `musiclinker.entrypoints.desktop:main`. |
| `MusicLinker.spec` | Packaging | PyInstaller v6.x spec file. |
| `start_player.bat` | Windows Launcher | 1-click launcher for Desktop App. |
| `run_cli.bat` | Windows Launcher | 1-click launcher for CLI Wizard. |
| `run_web.bat` | Windows Launcher | 1-click launcher for Streamlit Dashboard. |

## 4. Step-by-Step Agent Migration & APK Build Instructions

1. **Create Directory Hierarchy**: Create `src/musiclinker/{core,domain,infrastructure,services,api,entrypoints,web/js,web/css}` and `android/app/src/main/java/com/fabrithomas/musiclinker`.
2. **Preserve Existing Data**: Keep `library.db`, `config.json`, `seen_hashes.json` (if present), `downloads/`, `icon.ico`, and `icon.png` in the repository root. `db.py` automatically migrates legacy files and adds `track_number` to existing `tracks` tables.
3. **Write Source Files**: Create every file listed in Section 5 using its exact relative path.
4. **Run Windows Desktop / Server**: Run `py start_player.py` (or `start_player.bat`).
5. **Build the Android `.apk`**: Run `build_apk.bat` (or `npm install && npx cap sync android && cd android && gradlew assembleDebug`). Install `android/app/build/outputs/apk/debug/app-debug.apk` on your Android phone, tap **📱 Connect Server / Sync**, and scan or enter your PC's LAN URL!


## 5. Complete Refactored Codebase Provides Every Module in Full

### File: `pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "musiclinker"
version = "3.0.0"
description = "Offline-first Last.fm extractor, full album downloader, ID3v2.4 multi-artist tagger, LLM curator, and Windows/Android APK personal music ecosystem"
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
"musiclinker.web" = ["*.*", "js/*.*", "css/*.*"]
```

### File: `package.json`
```json
{
  "name": "musiclinker-android",
  "version": "3.0.0",
  "description": "Capacitor Native Android APK wrapper for MusicLinker Studio",
  "private": true,
  "scripts": {
    "apk:init": "npx cap add android",
    "apk:sync": "npx cap sync android",
    "apk:open": "npx cap open android",
    "apk:build": "npx cap sync android && cd android && gradlew assembleDebug"
  },
  "dependencies": {
    "@capacitor/android": "^6.1.2",
    "@capacitor/core": "^6.1.2"
  },
  "devDependencies": {
    "@capacitor/cli": "^6.1.2"
  }
}
```

### File: `capacitor.config.json`
```json
{
  "appId": "com.fabrithomas.musiclinker",
  "appName": "MusicLinker",
  "webDir": "src/musiclinker/web",
  "bundledWebRuntime": false,
  "server": {
    "androidScheme": "http",
    "cleartext": true,
    "allowNavigation": ["*"]
  },
  "android": {
    "allowMixedContent": true,
    "backgroundColor": "#09090b"
  }
}
```

### File: `android/build.gradle`
```groovy
buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath 'com.android.tools.build:gradle:8.5.2'
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

task clean(type: Delete) {
    delete rootProject.buildDir
}
```

### File: `android/settings.gradle`
```groovy
include ':app'
include ':capacitor-cordova-android-plugins'
project(':capacitor-cordova-android-plugins').projectDir = new File('./capacitor-cordova-android-plugins/')
apply from: 'capacitor.settings.gradle'
```

### File: `android/app/build.gradle`
```groovy
apply plugin: 'com.android.application'

android {
    namespace "com.fabrithomas.musiclinker"
    compileSdk 34

    defaultConfig {
        applicationId "com.fabrithomas.musiclinker"
        minSdk 26
        targetSdk 34
        versionCode 300
        versionName "3.0.0"
    }

    buildTypes {
        debug {
            minifyEnabled false
        }
        release {
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android.txt'), 'proguard-rules.pro'
        }
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
}

dependencies {
    implementation fileTree(include: ['*.jar'], dir: 'libs')
    implementation "androidx.appcompat:appcompat:1.7.0"
    implementation "androidx.coordinatorlayout:coordinatorlayout:1.2.0"
    implementation "androidx.core:core-splashscreen:1.0.1"
    implementation project(':capacitor-android')
    implementation project(':capacitor-cordova-android-plugins')
}
```

### File: `android/app/src/main/AndroidManifest.xml`
```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <!-- Required for LAN Wi-Fi streaming, offline sync, lockscreen media, and Car Mode wake-lock -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.ACCESS_WIFI_STATE" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="MusicLinker"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:usesCleartextTraffic="true"
        android:hardwareAccelerated="true"
        android:theme="@style/AppTheme">

        <activity
            android:configChanges="orientation|keyboardHidden|keyboard|screenSize|locale|smallestScreenSize|screenLayout|uiMode"
            android:name=".MainActivity"
            android:label="MusicLinker"
            android:launchMode="singleTask"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

### File: `android/app/src/main/java/com/fabrithomas/musiclinker/MainActivity.java`
```java
package com.fabrithomas.musiclinker;

import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (this.bridge != null && this.bridge.getWebView() != null) {
            WebView webView = this.bridge.getWebView();
            WebSettings settings = webView.getSettings();
            // Allow gapless track-to-track transitions and background audio without requiring a tap per track
            settings.setMediaPlaybackRequiresUserGesture(false);
            settings.setDomStorageEnabled(true);
            settings.setDatabaseEnabled(true);
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
    }
}
```

### File: `build_apk.bat`
```bat
@echo off
title MusicLinker Android APK Builder
echo ========================================================
echo   MusicLinker v3.0 — Native Android APK Builder
echo ========================================================
if not exist node_modules (
    echo [1/3] Installing Capacitor dependencies...
    call npm install
)
if not exist android\capacitor-android (
    echo [2/3] Syncing web assets into Android Capacitor project...
    call npx cap sync android
) else (
    call npx cap copy android
)
echo [3/3] Building APK via Gradle...
cd android
call gradlew.bat assembleDebug
cd ..
echo.
echo Done! APK generated at:
echo   android\app\build\outputs\apk\debug\app-debug.apk
pause
```

### File: `.gitignore`
```gitignore
# Byte-compiled / cache
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/

# Virtual environments & Node
.venv/
venv/
env/
node_modules/

# Build & distribution
build/
dist/
*.egg-info/
android/.gradle/
android/app/build/

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
"""MusicLinker: Offline-first Last.fm extractor, album downloader, LLM curator, and Windows/Android APK ecosystem."""

__version__ = "3.0.0"
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
"""LAN IP resolution, pairing token validation, and loopback verification."""

from __future__ import annotations

import hmac
import secrets
import socket
from urllib.parse import quote

from fastapi import HTTPException, Request, status


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
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
    return secrets.token_urlsafe(24)


def is_loopback_host(client_host: str | None) -> bool:
    if not client_host:
        return False
    return client_host in ("127.0.0.1", "::1", "localhost") or client_host.startswith("127.")


def verify_request_access(request: Request, expected_token: str, require_auth_on_lan: bool = True) -> None:
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
            detail="LAN pairing token required. Scan or paste the pairing URL from the MusicLinker Desktop App.",
        )


def build_pairing_url(port: int, pair_token: str) -> str:
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
    artist: str = Field(..., min_length=1)
    duration: int = Field(default=0)
    youtube_url: str = Field(default="")


class AlbumDownloadRequest(BaseModel):
    """Payload to download an entire album (either resolved via MusicBrainz/Last.fm/YouTube or custom tracklist)."""

    artist: str = Field(..., min_length=1)
    album: str = Field(..., min_length=1)
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

    track_id: int
    action_type: str = Field(description="'rename' or 'duplicate_remove'")
    current_title: str
    proposed_title: str
    current_artist: str
    proposed_artist: str
    current_album: str
    proposed_album: str
    duplicate_of_track_id: int | None = None
    reason: str = ""


class LLMCurationApplyPayload(BaseModel):
    """List of approved LLM curation actions to apply to SQLite and MP3 files."""

    actions: list[LLMCurationAction]


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
```

### File: `src/musiclinker/infrastructure/__init__.py`
```python
"""External infrastructure adapters: SQLite WAL, Last.fm, yt-dlp, MusicBrainz, LRCLIB, ID3v2.4, LLM Proxy."""
```

### File: `src/musiclinker/infrastructure/hasher.py`
```python
"""Deterministic SHA-256 track normalizer, multi-artist splitter, and automatic album cleaner."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_BRACKET_NOISE_RE = re.compile(
    r"[\(\[\{][^\)\]\}]*?\b("
    r"remaster(?:ed)?|deluxe|anniversary|expanded|edition|version|edit|"
    r"feat\.?|ft\.?|featuring|with|prod\.?|official\s+video|official\s+audio|lyrics?|"
    r"bonus\s+track|explicit|clean|single|ep"
    r")\b[^\)\]\}]*?[\)\]\}]",
    re.IGNORECASE,
)
_ALBUM_EDITION_NOISE_RE = re.compile(
    r"(?:\s*[\(\[\{][^\)\]\}]*?\b(?:"
    r"deluxe|remaster(?:ed)?|expanded|anniversary|special\s+edition|collector'?s\s+edition|"
    r"bonus\s+track\s+version|explicit\s+version|international\s+version|super\s+deluxe|"
    r"\d{4}\s+remaster|original\s+motion\s+picture\s+soundtrack"
    r")\b[^\)\]\}]*?[\)\]\}])|"
    r"(?:\s+-\s+(?:single|ep|deluxe\s+edition|remastered(?:\s+\d{4})?)$)",
    re.IGNORECASE,
)
_FEAT_NOISE_RE = re.compile(
    r"\b(?:feat\.?|ft\.?|featuring|with)\s+.+$",
    re.IGNORECASE,
)
_DASH_SUFFIX_RE = re.compile(
    r"\s+-\s+.*?\b(?:remaster(?:ed)?|live|mono|stereo|single\s+version|radio\s+edit|acoustic|\d{4})\b.*$",
    re.IGNORECASE,
)
_MULTI_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,\s*|\s+(?:feat\.?|ft\.?|featuring|with|&|x|vs\.?)\s+|\s*;\s*|\s*/\s*)\s*",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
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


def clean_album_name(raw_album: str, track_title: str = "") -> str:
    if not raw_album:
        return "Singles"
    cleaned = raw_album.strip()
    if cleaned.lower() in ("unknown", "unknown album", "none", "n/a", "youtube"):
        return "Singles"

    cleaned = _ALBUM_EDITION_NOISE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—")
    if not cleaned:
        return track_title.strip() or "Singles"
    return cleaned


def clean_track_title(raw_title: str) -> str:
    if not raw_title:
        return "Unknown Title"
    text = raw_title.strip()
    text = re.sub(
        r"[\(\[\{]\s*(?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|lyrics?|hd|hq|4k|visualizer)\s*[\)\]\}]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _DASH_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or raw_title.strip()


def split_artists(artist_raw: str, title_raw: str = "") -> tuple[str, list[str], str]:
    clean_title = clean_track_title(title_raw)
    extracted_from_title: list[str] = []

    title_feat_match = re.search(
        r"[\(\[\{]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]\}]+)[\)\]\}]",
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
    primary_artist, _, cleaned_title = split_artists(artist, title)
    norm_artist = normalize_text(primary_artist)
    norm_title = normalize_text(cleaned_title)
    payload = f"{norm_artist}::{norm_title}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
```

### File: `src/musiclinker/infrastructure/db.py`
```python
"""Thread-safe SQLite WAL repository with track_number indexing, multi-mode sorting, album aliases, and LLM settings."""

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
from musiclinker.infrastructure.hasher import clean_album_name, normalize_text

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
    "auto_clean_albums": True,
    "llm_base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    "llm_api_key": os.environ.get("LLM_API_KEY", ""),
    "llm_model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    "lan_pair_token": generate_pair_token(),
}


class DatabaseRepository:
    """Thread-local SQLite WAL repository managing tracks, album aliases, seen hashes, scrobbles, transitions, and settings."""

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
                    track_number INTEGER NOT NULL DEFAULT 0,
                    release_year TEXT NOT NULL DEFAULT '',
                    original_year TEXT NOT NULL DEFAULT '',
                    duration INTEGER NOT NULL DEFAULT 0,
                    file_path TEXT NOT NULL UNIQUE,
                    lrc_path TEXT NOT NULL DEFAULT '',
                    lyric_sync_mode TEXT NOT NULL DEFAULT 'none',
                    cover_mime TEXT NOT NULL DEFAULT '',
                    has_cover INTEGER NOT NULL DEFAULT 0,
                    lastfm_playcount INTEGER NOT NULL DEFAULT 0,
                    local_playcount INTEGER NOT NULL DEFAULT 0,
                    replaygain_db REAL NOT NULL DEFAULT 0.0,
                    bitrate_kbps INTEGER NOT NULL DEFAULT 320,
                    added_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);
                CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);
                CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);

                CREATE TABLE IF NOT EXISTS album_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_norm TEXT NOT NULL DEFAULT '',
                    raw_album_norm TEXT NOT NULL,
                    raw_album_display TEXT NOT NULL,
                    canonical_album TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(artist_norm, raw_album_norm)
                );

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
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
            if "lyric_sync_mode" not in cols:
                conn.execute("ALTER TABLE tracks ADD COLUMN lyric_sync_mode TEXT NOT NULL DEFAULT 'none'")
            if "track_number" not in cols:
                conn.execute("ALTER TABLE tracks ADD COLUMN track_number INTEGER NOT NULL DEFAULT 0")

            for key, default_val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
                    (key, json.dumps(default_val)),
                )
            conn.commit()

    def _migrate_legacy_files(self) -> None:
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

    # ---- Album Alias, Artist Summary & Hierarchy Operations ----

    def resolve_album_alias(self, artist: str, raw_album: str, track_title: str = "") -> str:
        auto_clean = bool(self.get_settings().get("auto_clean_albums", True))
        candidate = clean_album_name(raw_album, track_title) if auto_clean else (raw_album.strip() or "Singles")

        artist_norm = normalize_text(artist)
        raw_norm = normalize_text(raw_album)
        cand_norm = normalize_text(candidate)

        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT canonical_album FROM album_aliases
            WHERE (artist_norm = ? OR artist_norm = '')
              AND (raw_album_norm = ? OR raw_album_norm = ?)
            ORDER BY CASE WHEN artist_norm != '' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (artist_norm, raw_norm, cand_norm),
        ).fetchone()
        return str(row["canonical_album"]) if row else candidate

    def save_album_alias(self, artist: str, raw_album: str, canonical_album: str) -> None:
        artist_norm = normalize_text(artist)
        raw_norm = normalize_text(raw_album)
        if not raw_norm or not canonical_album.strip():
            return
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO album_aliases (artist_norm, raw_album_norm, raw_album_display, canonical_album, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artist_norm, raw_album_norm) DO UPDATE SET
                    canonical_album = excluded.canonical_album,
                    updated_at = excluded.updated_at
                """,
                (artist_norm, raw_norm, raw_album.strip(), canonical_album.strip(), int(time.time())),
            )
            conn.commit()

    def list_artists_summary(self) -> list[dict[str, Any]]:
        """Return distinct album artists with album and track counts for Layer 1 of the Layered Selector."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT album_artist AS artist,
                   COUNT(DISTINCT album) AS album_count,
                   COUNT(*) AS track_count
            FROM tracks
            GROUP BY album_artist
            ORDER BY LOWER(album_artist) ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def list_albums_summary(self) -> list[dict[str, Any]]:
        """Return distinct albums in the library with track counts and primary artist."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT album, album_artist, COUNT(*) AS track_count,
                   MAX(release_year) AS release_year,
                   MIN(id) AS sample_track_id,
                   MAX(has_cover) AS has_cover
            FROM tracks
            GROUP BY album, album_artist
            ORDER BY LOWER(album) ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def list_album_aliases(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM album_aliases ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

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
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
        duration: int = 0,
        file_path: str,
        lrc_path: str = "",
        lyric_sync_mode: str = "none",
        cover_mime: str = "",
        has_cover: bool = False,
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        bitrate_kbps: int = 320,
    ) -> TrackEntity:
        now_ts = int(time.time())
        canonical_album = self.resolve_album_alias(album_artist, album, title)
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO tracks (
                    track_hash, title, artist_display, artists_json, album_artist,
                    album, track_number, release_year, original_year, duration, file_path,
                    lrc_path, lyric_sync_mode, cover_mime, has_cover, lastfm_playcount,
                    replaygain_db, bitrate_kbps, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_hash) DO UPDATE SET
                    title = excluded.title,
                    artist_display = excluded.artist_display,
                    artists_json = excluded.artists_json,
                    album_artist = excluded.album_artist,
                    album = excluded.album,
                    track_number = CASE WHEN excluded.track_number > 0 THEN excluded.track_number ELSE tracks.track_number END,
                    release_year = CASE WHEN excluded.release_year != '' THEN excluded.release_year ELSE tracks.release_year END,
                    original_year = CASE WHEN excluded.original_year != '' THEN excluded.original_year ELSE tracks.original_year END,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END,
                    file_path = excluded.file_path,
                    lrc_path = CASE WHEN excluded.lrc_path != '' THEN excluded.lrc_path ELSE tracks.lrc_path END,
                    lyric_sync_mode = CASE WHEN excluded.lyric_sync_mode != 'none' THEN excluded.lyric_sync_mode ELSE tracks.lyric_sync_mode END,
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
                    canonical_album,
                    int(track_number or 0),
                    release_year,
                    original_year,
                    duration,
                    file_path,
                    lrc_path,
                    lyric_sync_mode,
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

    def update_track_row(
        self,
        track_id: int,
        *,
        track_hash: str,
        title: str,
        artist_display: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int,
        release_year: str,
        original_year: str,
        file_path: str,
        lrc_path: str,
    ) -> TrackEntity | None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                UPDATE tracks
                SET track_hash = ?, title = ?, artist_display = ?, artists_json = ?,
                    album_artist = ?, album = ?, track_number = ?, release_year = ?,
                    original_year = ?, file_path = ?, lrc_path = ?
                WHERE id = ?
                """,
                (
                    track_hash,
                    title,
                    artist_display,
                    json.dumps(artists, ensure_ascii=False),
                    album_artist,
                    album,
                    int(track_number or 0),
                    release_year,
                    original_year,
                    file_path,
                    lrc_path,
                    track_id,
                ),
            )
            conn.commit()
            return self.get_track(track_id)

    def delete_track(self, track_id: int) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            conn.commit()

    def list_tracks(
        self,
        search: str = "",
        artist: str = "",
        album: str = "",
        sort_by: str = "track_num",
    ) -> list[TrackEntity]:
        """
        List tracks with flexible ordering:
          - `track_num`: Album + Track # (`1..N`) + Title (ideal for Play by Album)
          - `alpha_asc`: Alphabetical by Title (`A -> Z`)
          - `alpha_desc`: Alphabetical by Title (`Z -> A`)
          - `plays_desc`: Most played (`lastfm_playcount + local_playcount DESC`)
          - `recent`: Recently added (`added_at DESC`)
        """
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
        if album.strip():
            clauses.append("LOWER(album) = ?")
            params.append(album.strip().lower())

        order_map = {
            "track_num": "LOWER(album) ASC, CASE WHEN track_number > 0 THEN track_number ELSE 9999 END ASC, LOWER(title) ASC",
            "alpha_asc": "LOWER(title) ASC, LOWER(artist_display) ASC",
            "alpha_desc": "LOWER(title) DESC, LOWER(artist_display) ASC",
            "plays_desc": "(lastfm_playcount + local_playcount) DESC, LOWER(title) ASC",
            "recent": "added_at DESC, LOWER(title) ASC",
        }
        order_sql = order_map.get(sort_by, order_map["track_num"])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM tracks {where_sql} ORDER BY {order_sql}",
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
        keys = row.keys()
        return TrackEntity(
            id=int(row["id"]),
            track_hash=str(row["track_hash"]),
            title=str(row["title"]),
            artist_display=str(row["artist_display"]),
            artists=artists if isinstance(artists, list) else [str(row["artist_display"])],
            album_artist=str(row["album_artist"]),
            album=str(row["album"]),
            track_number=int(row["track_number"]) if "track_number" in keys else 0,
            release_year=str(row["release_year"]),
            original_year=str(row["original_year"]),
            duration=int(row["duration"]),
            file_path=str(row["file_path"]),
            lrc_path=str(row["lrc_path"]),
            lyric_sync_mode=str(row["lyric_sync_mode"]) if "lyric_sync_mode" in keys else "none",
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
"""Bi-directional Last.fm API client supporting chart extraction, album tracklist lookup, and signed scrobbling."""

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
USER_AGENT = "MusicLinker/3.0 (+https://github.com/FabriThomas/musicLink)"


class LastFMClient:
    """Handles Last.fm read queries (Top Tracks, Calendar Year Scrobbles, Album Tracklists) and write calls (Now Playing, Scrobble)."""

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

    def _sign_params(self, params: dict[str, Any]) -> str:
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

    def get_auth_token(self) -> dict[str, str]:
        data = self._call("auth.getToken", {}, signed=True, http_post=False)
        token = str(data.get("token", ""))
        if not token:
            raise LastFMAuthError("Failed to obtain Last.fm auth token.")
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"
        return {"token": token, "auth_url": auth_url}

    def exchange_token_for_session(self, token: str) -> dict[str, str]:
        data = self._call("auth.getSession", {"token": token.strip()}, signed=True, http_post=False)
        session_obj = data.get("session", {})
        key = str(session_obj.get("key", ""))
        name = str(session_obj.get("name", ""))
        if not key:
            raise LastFMAuthError("User has not authorized the token in the browser yet.")
        self.session_key = key
        return {"session_key": key, "username": name}

    def fetch_album_tracklist(self, artist: str, album: str) -> list[dict[str, Any]]:
        """Query `album.getInfo` on Last.fm as a fallback tracklist source when downloading full albums."""
        if not self.api_key:
            return []
        try:
            data = self._call("album.getInfo", {"artist": artist.strip(), "album": album.strip()})
            album_obj = data.get("album", {})
            tracks_container = album_obj.get("tracks", {})
            raw_tracks = tracks_container.get("track", [])
            if isinstance(raw_tracks, dict):
                raw_tracks = [raw_tracks]
            results: list[dict[str, Any]] = []
            for idx, item in enumerate(raw_tracks, start=1):
                attr = item.get("@attr") or {}
                rank = int(attr.get("rank") or idx)
                title = str(item.get("name") or "").strip()
                if not title:
                    continue
                dur = int(item.get("duration") or 0)
                results.append(
                    {
                        "track_number": rank,
                        "title": title,
                        "artist": artist.strip(),
                        "duration": dur,
                    }
                )
            return results
        except Exception as exc:
            logger.debug("Last.fm album.getInfo lookup skipped: %s", exc)
            return []

    def fetch_top_tracks_by_period(
        self,
        username: str,
        period: str = "12month",
        min_plays: int = 10,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[CandidateTrack]:
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
        start_ts = int(datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())

        counts: Counter[str] = Counter()
        exemplars: dict[str, tuple[str, str, str]] = {}
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
"""Keyless yt-dlp search engine, playlist/album expander, FFmpeg MP3 downloader, and VTT subtitle extractor."""

from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp

from musiclinker.core.exceptions import YouTubeResolutionError
from musiclinker.core.logging_config import logger

_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class YouTubeClient:
    """Provides cached flat-search queries, playlist/album expansion, MP3 extraction, and VTT subtitle extraction."""

    def __init__(self) -> None:
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            logger.warning("imageio_ffmpeg lookup failed (%s); falling back to system ffmpeg", exc)
            self.ffmpeg_exe = "ffmpeg"

    def search_videos(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
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

    def expand_playlist_or_album_url(self, playlist_url: str, default_artist: str = "") -> list[dict[str, Any]]:
        """Extract all tracks in order (`1..N`) from a YouTube Album or Playlist URL."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": False,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
        except Exception as exc:
            raise YouTubeResolutionError(f"Failed expanding playlist/album URL '{playlist_url}': {exc}") from exc

        entries = info.get("entries", []) if isinstance(info, dict) else []
        tracks: list[dict[str, Any]] = []
        for idx, entry in enumerate(entries, start=1):
            if not entry:
                continue
            vid_id = entry.get("id", "")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
            raw_title = str(entry.get("title") or f"Track {idx}").strip()
            artist = default_artist or str(entry.get("uploader") or "Unknown Artist").replace(" - Topic", "").strip()
            if " - " in raw_title and not default_artist:
                artist, raw_title = raw_title.split(" - ", 1)
            tracks.append(
                {
                    "track_number": idx,
                    "title": raw_title.strip(),
                    "artist": artist.strip(),
                    "duration": int(entry.get("duration") or 0),
                    "youtube_url": url,
                }
            )
        return tracks

    def resolve_best_audio_match(self, artist: str, title: str) -> dict[str, Any]:
        query = f"{artist} - {title} official audio"
        matches = self.search_videos(query, max_results=3)
        if not matches:
            raise YouTubeResolutionError(f"No YouTube results found for '{artist} - {title}'")
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
    ) -> tuple[Path, dict[str, Any], str]:
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
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "pt.*", "es.*", "orig"],
            "subtitlesformat": "vtt",
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
            candidates = list(staging_dir.glob(f"{stem_name}*.mp3"))
            if not candidates:
                raise YouTubeResolutionError("FFmpeg post-processing did not produce an MP3 file.")
            mp3_path = candidates[0]

        yt_lrc_fallback = ""
        vtt_files = list(staging_dir.glob(f"{stem_name}*.vtt"))
        if vtt_files:
            try:
                raw_vtt = vtt_files[0].read_text(encoding="utf-8", errors="replace")
                yt_lrc_fallback = self.convert_vtt_to_lrc(raw_vtt)
            except Exception as exc:
                logger.debug("VTT caption conversion skipped: %s", exc)

        return mp3_path, (info if isinstance(info, dict) else {}), yt_lrc_fallback

    @staticmethod
    def convert_vtt_to_lrc(raw_vtt: str) -> str:
        lrc_lines: list[str] = []
        last_text = ""
        lines = raw_vtt.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = _VTT_TIMESTAMP_RE.search(line)
            if match:
                hours = int(match.group(1).rstrip(":")) if match.group(1) else 0
                minutes = int(match.group(2)) + hours * 60
                seconds = int(match.group(3))
                millis = int(match.group(4))
                hundredths = millis // 10
                i += 1
                cue_parts: list[str] = []
                while i < len(lines) and lines[i].strip():
                    cleaned = _HTML_TAG_RE.sub("", lines[i]).strip()
                    if cleaned and not cleaned.startswith("WEBVTT"):
                        cue_parts.append(cleaned)
                    i += 1
                text = " ".join(cue_parts).strip()
                if text and text.lower() != last_text.lower():
                    lrc_lines.append(f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}] {text}")
                    last_text = text
            i += 1
        return "\n".join(lrc_lines)


youtube_client = YouTubeClient()
```

### File: `src/musiclinker/infrastructure/musicbrainz_client.py`
```python
"""MusicBrainz & Cover Art Archive client resolving original release years (TDOR), track numbers, and full album tracklists."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import ReleaseMetadata
from musiclinker.infrastructure.hasher import clean_album_name

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
MB_USER_AGENT = "MusicLinker/3.0 ( https://github.com/FabriThomas/musicLink )"


class MusicBrainzClient:
    """Rate-limited (1 req/sec) MusicBrainz metadata and full album tracklist resolver."""

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

    def fetch_album_tracklist(self, artist: str, album: str) -> dict[str, Any]:
        """
        Resolve a complete studio album's ordered tracklist (`1..N`), release year, and front cover URL from MusicBrainz.
        """
        clean_alb = clean_album_name(album)
        result: dict[str, Any] = {
            "artist": artist.strip(),
            "album": clean_alb,
            "release_year": "",
            "release_mbid": "",
            "cover_url": "",
            "tracks": [],
        }
        try:
            self._throttle()
            query = f'artist:"{artist.strip()}" AND release:"{clean_alb}" AND status:official'
            resp = self.session.get(
                f"{MB_BASE_URL}/release",
                params={"query": query, "limit": 5, "fmt": "json"},
                timeout=10.0,
            )
            if not resp.ok:
                return result
            releases = resp.json().get("releases", [])
            if not releases:
                return result

            chosen = releases[0]
            release_mbid = str(chosen.get("id") or "")
            result["release_mbid"] = release_mbid
            result["album"] = clean_album_name(str(chosen.get("title") or clean_alb))
            rel_date = str(chosen.get("date") or "")
            if len(rel_date) >= 4:
                result["release_year"] = rel_date[:4]
            if release_mbid:
                result["cover_url"] = f"{CAA_BASE_URL}/release/{release_mbid}/front-500"

            # Fetch full medium/track listing for the chosen release
            self._throttle()
            detail_resp = self.session.get(
                f"{MB_BASE_URL}/release/{release_mbid}",
                params={"inc": "recordings+artist-credits", "fmt": "json"},
                timeout=10.0,
            )
            if detail_resp.ok:
                media = detail_resp.json().get("media", [])
                tracks_out: list[dict[str, Any]] = []
                running_num = 1
                for medium in media:
                    for tr in medium.get("tracks", []) or []:
                        title = str(tr.get("title") or "").strip()
                        if not title:
                            continue
                        pos = int(tr.get("position") or running_num)
                        length_ms = int(tr.get("length") or 0)
                        tracks_out.append(
                            {
                                "track_number": pos,
                                "title": title,
                                "artist": artist.strip(),
                                "duration": round(length_ms / 1000) if length_ms > 0 else 0,
                                "youtube_url": "",
                            }
                        )
                        running_num += 1
                result["tracks"] = tracks_out
        except Exception as exc:
            logger.warning("MusicBrainz album tracklist lookup failed for %s - %s: %s", artist, album, exc)

        return result

    def resolve_release_metadata(
        self,
        artist: str,
        title: str,
        fallback_album: str = "",
        fallback_thumb_url: str = "",
        fallback_track_number: int = 0,
    ) -> ReleaseMetadata:
        cache_key = f"{artist.lower().strip()}::{title.lower().strip()}::{fallback_album.lower().strip()}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if fallback_track_number > 0 and cached.track_number == 0:
                cached.track_number = fallback_track_number
            return cached

        meta = ReleaseMetadata(
            album=clean_album_name(fallback_album, title),
            album_artist=artist,
            track_number=fallback_track_number,
        )
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
                        raw_mb_album = str(chosen.get("title") or meta.album)
                        meta.album = clean_album_name(raw_mb_album, title)
                        meta.release_mbid = str(chosen.get("id") or "")
                        rel_date = str(chosen.get("date") or "")
                        if len(rel_date) >= 4:
                            meta.release_year = rel_date[:4]
                            if not meta.original_year:
                                meta.original_year = meta.release_year

                        # Extract track number from media -> track[0].number if present
                        if meta.track_number == 0:
                            media_list = chosen.get("media") or []
                            if media_list and isinstance(media_list[0], dict):
                                tr_list = media_list[0].get("track") or []
                                if tr_list and isinstance(tr_list[0], dict):
                                    num_str = str(tr_list[0].get("number") or "")
                                    if num_str.isdigit():
                                        meta.track_number = int(num_str)
        except Exception as exc:
            logger.debug("MusicBrainz lookup skipped for %s - %s: %s", artist, title, exc)

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
            secondary = rg.get("secondary-types") or []
            if status == "official" and primary_type == "album" and not secondary:
                return rel
        for rel in releases:
            status = str(rel.get("status", "")).lower()
            rg = rel.get("release-group", {})
            if status == "official" and str(rg.get("primary-type", "")).lower() == "album":
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
"""3-Tier Synced & Rough Lyrics Resolver (LRCLIB Exact -> YouTube Auto-Captions -> Proportional Rough Interpolation)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import requests

from musiclinker.core.logging_config import logger

LRCLIB_API_URL = "https://lrclib.net/api"
_LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\](.*)")
_META_MODE_RE = re.compile(r"^\[x-ml-sync-mode:([a-z_]+)\]$", re.IGNORECASE)


class LyricsClient:
    """
    Resolves lyrics across three tiers so the user always sees where the song is at:
      1. `exact_lrc`: Official timestamped `.lrc` lines from LRCLIB.
      2. `youtube_captions`: Timestamped subtitle cues extracted from the downloaded YouTube stream.
      3. `rough_interpolated`: Plain lyrics automatically distributed across the vocal window (`8%`–`92%` of duration).
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MusicLinker/3.0 (https://github.com/FabriThomas/musicLink)"})

    def fetch_and_save_lrc(
        self,
        artist: str,
        title: str,
        album: str,
        duration: int,
        target_lrc_path: Path,
        youtube_lrc_fallback: str = "",
    ) -> tuple[str, str, str]:
        synced_text, plain_text = self.fetch_lyrics(artist, title, album, duration)
        sync_mode = "none"
        final_body = ""
        summary = "No lyrics found"

        if synced_text.strip():
            sync_mode = "exact_lrc"
            final_body = synced_text.strip()
            line_count = len([l for l in final_body.splitlines() if _LRC_LINE_RE.match(l.strip())])
            summary = f"Exact LRC synced lyrics ({line_count} timestamped lines via LRCLIB)"
        elif youtube_lrc_fallback.strip():
            sync_mode = "youtube_captions"
            final_body = youtube_lrc_fallback.strip()
            line_count = len(final_body.splitlines())
            summary = f"Rough synced captions from YouTube stream ({line_count} cues)"
        elif plain_text.strip():
            sync_mode = "rough_interpolated"
            final_body = self.interpolate_plain_lyrics_to_lrc(plain_text.strip(), duration)
            line_count = len(final_body.splitlines())
            summary = f"Rough interpolated sync ({line_count} plain lines mapped across {duration or 180}s)"

        if final_body:
            full_text = f"[x-ml-sync-mode:{sync_mode}]\n{final_body}\n"
            try:
                target_lrc_path.write_text(full_text, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed writing LRC file %s: %s", target_lrc_path, exc)
            return full_text, sync_mode, summary

        return "", "none", summary

    @staticmethod
    def interpolate_plain_lyrics_to_lrc(plain_text: str, duration: int) -> str:
        raw_lines = [l.strip() for l in plain_text.splitlines() if l.strip()]
        if not raw_lines:
            return ""
        effective_dur = max(60, duration if duration > 0 else 200)
        start_sec = effective_dur * 0.08
        end_sec = effective_dur * 0.92
        span = max(10.0, end_sec - start_sec)
        step = span / max(1, len(raw_lines))

        out: list[str] = []
        for idx, text in enumerate(raw_lines):
            t = start_sec + idx * step
            mins = int(t // 60)
            secs = t % 60
            out.append(f"[{mins:02d}:{secs:05.2f}] {text}")
        return "\n".join(out)

    def fetch_lyrics(self, artist: str, title: str, album: str = "", duration: int = 0) -> tuple[str, str]:
        try:
            params: dict[str, Any] = {"artist_name": artist, "track_name": title}
            if album:
                params["album_name"] = album
            if duration > 0:
                params["duration"] = duration
            resp = self.session.get(f"{LRCLIB_API_URL}/get", params=params, timeout=8.0)
            if resp.status_code == 404:
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

    @classmethod
    def parse_lrc_content(cls, raw_lrc: str, fallback_duration: int = 200) -> dict[str, Any]:
        if not raw_lrc.strip():
            return {"synced": False, "sync_mode": "none", "lines": []}

        detected_mode = "exact_lrc"
        synced_lines: list[dict[str, Any]] = []
        plain_lines: list[str] = []

        for raw_line in raw_lrc.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            mode_match = _META_MODE_RE.match(line)
            if mode_match:
                detected_mode = mode_match.group(1).lower()
                continue
            match = _LRC_LINE_RE.match(line)
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                text = match.group(3).strip()
                total_sec = round(minutes * 60.0 + seconds, 2)
                synced_lines.append({"time": total_sec, "text": text or "♪"})
            elif not line.startswith("["):
                plain_lines.append(line)

        if synced_lines:
            synced_lines.sort(key=lambda item: float(item["time"]))
            return {
                "synced": True,
                "sync_mode": detected_mode,
                "lines": synced_lines,
            }

        if plain_lines:
            interpolated_lrc = cls.interpolate_plain_lyrics_to_lrc("\n".join(plain_lines), fallback_duration)
            return cls.parse_lrc_content(f"[x-ml-sync-mode:rough_interpolated]\n{interpolated_lrc}", fallback_duration)

        return {"synced": False, "sync_mode": "none", "lines": []}


lyrics_client = LyricsClient()
```

### File: `src/musiclinker/infrastructure/tagger.py`
```python
"""Advanced ID3v2.4 tagger supporting TRCK track numbers, null-delimited multi-artist frames, ReplayGain, and cover art."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TALB,
    TDRC,
    TDOR,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TXXX,
    USLT,
)
from mutagen.mp3 import MP3

from musiclinker.core.exceptions import TaggingError
from musiclinker.infrastructure.hasher import clean_album_name, compute_track_hash, split_artists


class AudioTagger:
    """Reads and writes audiophile-grade ID3v2.4 metadata frames (`TIT2`, `TPE1`, `TPE2`, `TALB`, `TRCK`, `TDRC`, `TDOR`, `APIC`)."""

    def write_id3v24_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
        lastfm_playcount: int = 0,
        replaygain_db: float = 0.0,
        lyrics_text: str = "",
        cover_bytes: bytes | None = None,
        cover_mime: str = "image/jpeg",
    ) -> tuple[int, int]:
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if track_number and track_number > 0:
                tags.delall("TRCK")
                tags.add(TRCK(encoding=3, text=[str(track_number)]))

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
                        type=3,
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

    def update_existing_mp3_tags(
        self,
        mp3_path: Path,
        *,
        title: str,
        artists: list[str],
        album_artist: str,
        album: str,
        track_number: int = 0,
        release_year: str = "",
        original_year: str = "",
    ) -> None:
        try:
            try:
                tags = ID3(str(mp3_path))
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=artists if artists else [album_artist]))

            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[album_artist]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[album or "Singles"]))

            if track_number and track_number > 0:
                tags.delall("TRCK")
                tags.add(TRCK(encoding=3, text=[str(track_number)]))

            if release_year:
                tags.delall("TDRC")
                tags.add(TDRC(encoding=3, text=[str(release_year)]))

            if original_year:
                tags.delall("TDOR")
                tags.add(TDOR(encoding=3, text=[str(original_year)]))

            tags.save(str(mp3_path), v2_version=4)
        except Exception as exc:
            raise TaggingError(f"Failed updating ID3 tags on {mp3_path.name}: {exc}") from exc

    def read_mp3_metadata(self, mp3_path: Path) -> dict[str, Any]:
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
            raw_album = mp3_path.parent.name if mp3_path.parent.name != "downloads" else "Singles"
            return {
                "track_hash": compute_track_hash(album_artist, clean_title),
                "title": clean_title,
                "artist_display": ", ".join(artists),
                "artists": artists,
                "album_artist": album_artist,
                "album": clean_album_name(raw_album, clean_title),
                "track_number": 0,
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

        trck = tags.get("TRCK")
        track_number = 0
        if trck and trck.text:
            raw_trck = str(trck.text[0]).split("/")[0].strip()
            if raw_trck.isdigit():
                track_number = int(raw_trck)

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
            "track_number": track_number,
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

### File: `src/musiclinker/infrastructure/llm_client.py`
```python
"""OpenAI-compatible LLM API / Proxy client for track deduplication, title cleaning, and album canonicalization."""

from __future__ import annotations

import json
import re
from typing import Any

import requests


class LLMClient:
    """Calls any OpenAI-compatible API endpoint or reverse proxy URL for track deduplication and album canonicalization."""

    def __init__(self, base_url: str, api_key: str = "", model: str = "gpt-4o-mini", timeout: float = 35.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-4o-mini"
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.base_url) and (
            bool(self.api_key)
            or "localhost" in self.base_url
            or "127.0.0.1" in self.base_url
            or not self.base_url.startswith("https://api.openai.com")
        )

    def _endpoint_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def curate_tracks_json(self, tracks_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_prompt = (
            "You are an expert music discography curator and audio library deduplicator.\n"
            "Given a JSON list of tracks (each with id, title, artist, album, playcount), analyze them and return ONLY a valid JSON object with a single key \"actions\" containing an array of objects.\n"
            "Rules:\n"
            "1. Detect repeated/duplicate versions of the same song by the same artist (e.g. Remastered, Radio Edit, Deluxe, Live, Single Version, or slight spelling variants). Keep the version with the highest playcount (or lowest id) and mark the redundant version(s) with action_type='duplicate_remove' and duplicate_of_track_id=<kept_id>.\n"
            "2. Clean messy track titles (strip '(Official Video)', '- 2011 Remaster', '[Explicit]', trailing release noise) and fix wrong or messy album names (e.g. replace '<Song> - Single', 'Greatest Hits (Deluxe Edition)', or 'Unknown Album' with the proper studio album name).\n"
            "3. Only emit entries in \"actions\" for tracks that need renaming/album-fixing (action_type='rename') or duplicate removal (action_type='duplicate_remove').\n"
            "Schema per item: {\"track_id\": int, \"action_type\": \"rename\"|\"duplicate_remove\", \"proposed_title\": str, \"proposed_artist\": str, \"proposed_album\": str, \"duplicate_of_track_id\": int|null, \"reason\": str}"
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({"tracks": tracks_payload}, ensure_ascii=False)},
            ],
        }

        resp = requests.post(self._endpoint_url(), headers=headers, json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = self._extract_json(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            return parsed["actions"]
        if isinstance(parsed, list):
            return parsed
        return []

    @staticmethod
    def _extract_json(raw_text: str) -> Any:
        text = raw_text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise
```


### File: `src/musiclinker/services/__init__.py`
```python
"""Application use-case services orchestrating domain models and infrastructure adapters."""
```

### File: `src/musiclinker/services/library_service.py`
```python
"""Library synchronizer, external album tracklist resolver, internal album/track renamer, and 3-tier lyrics loader."""

from __future__ import annotations

import os
from pathlib import Path
import random
import re
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.domain.models import AlbumBulkRenamePayload, TrackEntity, TrackMetadataUpdatePayload
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import compute_track_hash, split_artists
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.lyrics_client import lyrics_client
from musiclinker.infrastructure.musicbrainz_client import musicbrainz_client
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client

_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or fallback


class LibraryService:
    """Manages local music library indexing, external album lookups, internal renaming, and song transitions."""

    def __init__(self, repo: DatabaseRepository = db_repo) -> None:
        self.repo = repo

    def scan_and_sync_downloads(self) -> dict[str, int]:
        pruned = self.repo.remove_missing_files()
        indexed = 0

        for mp3_path in PATHS.downloads_dir.rglob("*.mp3"):
            if ".staging" in mp3_path.parts:
                continue
            try:
                meta = audio_tagger.read_mp3_metadata(mp3_path)
                canonical_album = self.repo.resolve_album_alias(
                    meta["album_artist"], meta["album"], meta["title"]
                )
                lrc_candidate = mp3_path.with_suffix(".lrc")
                lrc_path = str(lrc_candidate.resolve()) if lrc_candidate.exists() else ""
                sync_mode = "none"
                if lrc_candidate.exists():
                    parsed_lrc = lyrics_client.parse_lrc_content(
                        lrc_candidate.read_text(encoding="utf-8", errors="replace"),
                        fallback_duration=meta["duration"],
                    )
                    sync_mode = str(parsed_lrc.get("sync_mode", "exact_lrc"))

                self.repo.upsert_track(
                    track_hash=meta["track_hash"],
                    title=meta["title"],
                    artist_display=meta["artist_display"],
                    artists=meta["artists"],
                    album_artist=meta["album_artist"],
                    album=canonical_album,
                    track_number=int(meta.get("track_number") or 0),
                    release_year=meta["release_year"],
                    original_year=meta["original_year"],
                    duration=meta["duration"],
                    file_path=str(mp3_path.resolve()),
                    lrc_path=lrc_path,
                    lyric_sync_mode=sync_mode,
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

    def lookup_external_album_tracklist(self, artist: str, album_or_url: str) -> dict[str, Any]:
        """
        Resolve the full tracklist (`1..N`) for an album so the user can preview and download the entire album:
          1. If `album_or_url` is a YouTube playlist/album URL, expand via `yt-dlp`.
          2. Otherwise query MusicBrainz for the official release tracklist.
          3. Fallback to Last.fm `album.getInfo` if MusicBrainz has no match.
        """
        if album_or_url.strip().startswith(("http://", "https://")):
            yt_tracks = youtube_client.expand_playlist_or_album_url(album_or_url.strip(), default_artist=artist)
            return {
                "artist": artist.strip() or (yt_tracks[0]["artist"] if yt_tracks else "Unknown Artist"),
                "album": "Downloaded Album",
                "release_year": "",
                "source": "youtube_playlist",
                "tracks": yt_tracks,
            }

        mb_data = musicbrainz_client.fetch_album_tracklist(artist, album_or_url)
        if mb_data.get("tracks"):
            mb_data["source"] = "musicbrainz"
            return mb_data

        cfg = self.repo.get_settings()
        lfm = LastFMClient(api_key=str(cfg.get("lastfm_api_key", "")))
        lfm_tracks = lfm.fetch_album_tracklist(artist, album_or_url)
        if lfm_tracks:
            return {
                "artist": artist.strip(),
                "album": album_or_url.strip(),
                "release_year": "",
                "source": "lastfm",
                "tracks": lfm_tracks,
            }

        # Final fallback: search YouTube for tracks matching "<Artist> <Album>"
        yt_matches = youtube_client.search_videos(f"{artist} {album_or_url} audio", max_results=10)
        fallback_tracks = [
            {
                "track_number": idx,
                "title": m["title"],
                "artist": artist.strip(),
                "duration": int(m.get("duration") or 0),
                "youtube_url": m["url"],
            }
            for idx, m in enumerate(yt_matches, start=1)
        ]
        return {
            "artist": artist.strip(),
            "album": album_or_url.strip(),
            "release_year": "",
            "source": "youtube_search",
            "tracks": fallback_tracks,
        }

    def update_track_metadata(self, track_id: int, payload: TrackMetadataUpdatePayload) -> TrackEntity:
        track = self.repo.get_track(track_id)
        if not track:
            raise ValueError(f"Track ID {track_id} not found.")

        old_album = track.album
        album_artist, artists_list, clean_title = split_artists(payload.artist_display, payload.title)
        artist_display = ", ".join(artists_list)
        new_album = payload.album.strip() or "Singles"
        new_track_num = payload.track_number if payload.track_number > 0 else track.track_number
        new_hash = compute_track_hash(album_artist, clean_title)

        if payload.remember_album_alias and old_album.lower() != new_album.lower():
            self.repo.save_album_alias(album_artist, old_album, new_album)

        old_mp3 = Path(track.file_path)
        new_mp3 = old_mp3
        new_lrc_str = track.lrc_path

        if old_mp3.exists():
            audio_tagger.update_existing_mp3_tags(
                old_mp3,
                title=clean_title,
                artists=artists_list,
                album_artist=album_artist,
                album=new_album,
                track_number=new_track_num,
                release_year=payload.release_year or track.release_year,
                original_year=payload.original_year or track.original_year,
            )
            dest_dir = (
                PATHS.downloads_dir
                / sanitize_filename(album_artist, "Unknown Artist")
                / sanitize_filename(new_album, "Singles")
            )
            dest_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{new_track_num:02d} - " if new_track_num > 0 else ""
            base_name = f"{prefix}{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
            target_mp3 = dest_dir / f"{base_name}.mp3"

            if target_mp3.resolve() != old_mp3.resolve():
                os.replace(str(old_mp3), str(target_mp3))
                new_mp3 = target_mp3
                old_lrc = Path(track.lrc_path) if track.lrc_path else old_mp3.with_suffix(".lrc")
                if old_lrc.exists():
                    target_lrc = dest_dir / f"{base_name}.lrc"
                    os.replace(str(old_lrc), str(target_lrc))
                    new_lrc_str = str(target_lrc.resolve())

        updated = self.repo.update_track_row(
            track_id,
            track_hash=new_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=new_album,
            track_number=new_track_num,
            release_year=payload.release_year or track.release_year,
            original_year=payload.original_year or track.original_year,
            file_path=str(new_mp3.resolve()),
            lrc_path=new_lrc_str,
        )
        return updated or track

    def bulk_rename_album(self, payload: AlbumBulkRenamePayload) -> dict[str, Any]:
        tracks = self.repo.list_tracks(album=payload.old_album)
        if payload.album_artist.strip():
            tracks = [
                t for t in tracks if t.album_artist.lower() == payload.album_artist.strip().lower()
            ]

        if payload.remember_alias:
            self.repo.save_album_alias(payload.album_artist, payload.old_album, payload.new_album)

        updated_count = 0
        for tr in tracks:
            self.update_track_metadata(
                tr.id,
                TrackMetadataUpdatePayload(
                    title=tr.title,
                    artist_display=tr.artist_display,
                    album=payload.new_album,
                    track_number=tr.track_number,
                    release_year=tr.release_year,
                    original_year=tr.original_year,
                    remember_album_alias=payload.remember_alias,
                ),
            )
            updated_count += 1

        return {
            "old_album": payload.old_album,
            "new_album": payload.new_album,
            "updated_tracks": updated_count,
        }

    def get_track_lyrics(self, track: TrackEntity) -> dict[str, Any]:
        if track.lrc_path and Path(track.lrc_path).exists():
            raw = Path(track.lrc_path).read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw, fallback_duration=track.duration)

        mp3_file = Path(track.file_path)
        lrc_file = mp3_file.with_suffix(".lrc")
        if lrc_file.exists():
            raw = lrc_file.read_text(encoding="utf-8", errors="replace")
            return lyrics_client.parse_lrc_content(raw, fallback_duration=track.duration)

        fetched_text, sync_mode, _summary = lyrics_client.fetch_and_save_lrc(
            artist=track.album_artist or track.artist_display,
            title=track.title,
            album=track.album,
            duration=track.duration,
            target_lrc_path=lrc_file,
        )
        if fetched_text:
            self.repo.upsert_track(
                track_hash=track.track_hash,
                title=track.title,
                artist_display=track.artist_display,
                artists=track.artists,
                album_artist=track.album_artist,
                album=track.album,
                track_number=track.track_number,
                release_year=track.release_year,
                original_year=track.original_year,
                duration=track.duration,
                file_path=track.file_path,
                lrc_path=str(lrc_file.resolve()),
                lyric_sync_mode=sync_mode,
                cover_mime=track.cover_mime,
                has_cover=track.has_cover,
                lastfm_playcount=track.lastfm_playcount,
                replaygain_db=track.replaygain_db,
                bitrate_kbps=track.bitrate_kbps,
            )
            return lyrics_client.parse_lrc_content(fetched_text, fallback_duration=track.duration)
        return {"synced": False, "sync_mode": "none", "lines": []}

    def resolve_next_transition(self, current_track_id: int) -> dict[str, Any] | None:
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

### File: `src/musiclinker/services/curation_service.py`
```python
"""LLM Smart Curator & Heuristic Deduplicator for detecting repeated tracks and canonicalizing albums."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import (
    CandidateTrack,
    LLMCurationAction,
    TrackMetadataUpdatePayload,
)
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import (
    clean_album_name,
    clean_track_title,
    compute_track_hash,
    normalize_text,
)
from musiclinker.infrastructure.llm_client import LLMClient
from musiclinker.services.library_service import LibraryService, library_service


class CurationService:
    """Provides LLM API/Proxy and fuzzy-matching deduplication and track/album canonicalization."""

    def __init__(
        self,
        repo: DatabaseRepository = db_repo,
        lib_service: LibraryService = library_service,
    ) -> None:
        self.repo = repo
        self.lib_service = lib_service

    def _build_llm(self) -> LLMClient:
        cfg = self.repo.get_settings()
        return LLMClient(
            base_url=str(cfg.get("llm_base_url", "https://api.openai.com/v1")),
            api_key=str(cfg.get("llm_api_key", "")),
            model=str(cfg.get("llm_model", "gpt-4o-mini")),
        )

    def filter_candidates_before_download(
        self,
        candidates: list[CandidateTrack],
        log_cb: Any = None,
    ) -> list[CandidateTrack]:
        if not candidates:
            return []

        llm = self._build_llm()
        if llm.is_configured():
            try:
                if log_cb:
                    log_cb(f"Running LLM Smart Filter via {llm.base_url} (model={llm.model}) on {len(candidates)} candidate(s)...")
                payload = [
                    {
                        "id": idx,
                        "title": c.title,
                        "artist": c.artist,
                        "album": c.album,
                        "playcount": c.playcount,
                    }
                    for idx, c in enumerate(candidates)
                ]
                actions = llm.curate_tracks_json(payload[:80])
                action_map = {int(a["track_id"]): a for a in actions if "track_id" in a}
                curated: list[CandidateTrack] = []
                removed_dups = 0
                for idx, cand in enumerate(candidates):
                    act = action_map.get(idx)
                    if act and act.get("action_type") == "duplicate_remove":
                        removed_dups += 1
                        continue
                    if act and act.get("action_type") == "rename":
                        cand.title = str(act.get("proposed_title") or cand.title).strip()
                        cand.artist = str(act.get("proposed_artist") or cand.artist).strip()
                        cand.album = str(act.get("proposed_album") or cand.album).strip()
                    else:
                        cand.title = clean_track_title(cand.title)
                        cand.album = clean_album_name(cand.album, cand.title)
                    cand.track_hash = compute_track_hash(cand.artist, cand.title)
                    curated.append(cand)
                if log_cb:
                    log_cb(f"LLM Filter complete: removed {removed_dups} duplicate version(s), {len(curated)} unique track(s) retained.")
                return curated
            except Exception as exc:
                logger.warning("LLM proxy call failed (%s); falling back to local fuzzy deduplicator", exc)
                if log_cb:
                    log_cb(f"LLM proxy unreachable ({exc}); using built-in fuzzy deduplicator.")

        deduped: list[CandidateTrack] = []
        for cand in candidates:
            cand.title = clean_track_title(cand.title)
            cand.album = self.repo.resolve_album_alias(cand.artist, cand.album, cand.title)
            cand.track_hash = compute_track_hash(cand.artist, cand.title)

            matched_existing = None
            for kept in deduped:
                if normalize_text(kept.artist) == normalize_text(cand.artist):
                    ratio = SequenceMatcher(None, normalize_text(kept.title), normalize_text(cand.title)).ratio()
                    if ratio >= 0.88:
                        matched_existing = kept
                        break
            if matched_existing:
                matched_existing.playcount += cand.playcount
            else:
                deduped.append(cand)
        return deduped

    def analyze_library(self) -> dict[str, Any]:
        tracks = self.repo.list_tracks()
        if not tracks:
            return {"used_llm": False, "actions": []}

        track_map = {t.id: t for t in tracks}
        llm = self._build_llm()
        used_llm = False
        actions: list[LLMCurationAction] = []

        if llm.is_configured():
            try:
                payload = [
                    {
                        "id": t.id,
                        "title": t.title,
                        "artist": t.artist_display,
                        "album": t.album,
                        "playcount": t.lastfm_playcount + t.local_playcount,
                    }
                    for t in tracks[:100]
                ]
                raw_actions = llm.curate_tracks_json(payload)
                used_llm = True
                for item in raw_actions:
                    tid = int(item.get("track_id", 0))
                    orig = track_map.get(tid)
                    if not orig:
                        continue
                    act_type = str(item.get("action_type", "rename"))
                    if act_type not in ("rename", "duplicate_remove"):
                        continue
                    actions.append(
                        LLMCurationAction(
                            track_id=tid,
                            action_type=act_type,
                            current_title=orig.title,
                            proposed_title=str(item.get("proposed_title") or orig.title).strip(),
                            current_artist=orig.artist_display,
                            proposed_artist=str(item.get("proposed_artist") or orig.artist_display).strip(),
                            current_album=orig.album,
                            proposed_album=str(item.get("proposed_album") or orig.album).strip(),
                            duplicate_of_track_id=item.get("duplicate_of_track_id"),
                            reason=str(item.get("reason") or "LLM metadata normalization"),
                        )
                    )
            except Exception as exc:
                logger.warning("Library LLM audit fallback triggered: %s", exc)

        if not actions:
            seen_by_artist: dict[str, list[Any]] = {}
            for t in tracks:
                cleaned_title = clean_track_title(t.title)
                cleaned_album = self.repo.resolve_album_alias(t.album_artist, t.album, cleaned_title)
                art_key = normalize_text(t.album_artist or t.artist_display)

                dup_target = None
                for prev in seen_by_artist.get(art_key, []):
                    sim = SequenceMatcher(None, normalize_text(prev.title), normalize_text(cleaned_title)).ratio()
                    if sim >= 0.88:
                        dup_target = prev
                        break

                if dup_target is not None:
                    actions.append(
                        LLMCurationAction(
                            track_id=t.id,
                            action_type="duplicate_remove",
                            current_title=t.title,
                            proposed_title=dup_target.title,
                            current_artist=t.artist_display,
                            proposed_artist=dup_target.artist_display,
                            current_album=t.album,
                            proposed_album=dup_target.album,
                            duplicate_of_track_id=dup_target.id,
                            reason=f"Duplicate version of '{dup_target.title}' (ID #{dup_target.id})",
                        )
                    )
                    continue

                seen_by_artist.setdefault(art_key, []).append(t)
                if cleaned_title != t.title or cleaned_album != t.album:
                    actions.append(
                        LLMCurationAction(
                            track_id=t.id,
                            action_type="rename",
                            current_title=t.title,
                            proposed_title=cleaned_title,
                            current_artist=t.artist_display,
                            proposed_artist=t.artist_display,
                            current_album=t.album,
                            proposed_album=cleaned_album,
                            reason="Auto-cleaned edition/remaster noise",
                        )
                    )

        return {
            "used_llm": used_llm,
            "llm_endpoint": llm.base_url if used_llm else "local-heuristic",
            "actions": [a.model_dump() for a in actions],
        }

    def apply_curation_actions(self, actions: list[LLMCurationAction]) -> dict[str, int]:
        renamed = 0
        removed_duplicates = 0

        for act in actions:
            track = self.repo.get_track(act.track_id)
            if not track:
                continue

            if act.action_type == "duplicate_remove":
                mp3_file = Path(track.file_path)
                lrc_file = Path(track.lrc_path) if track.lrc_path else mp3_file.with_suffix(".lrc")
                if mp3_file.exists():
                    mp3_file.unlink(missing_ok=True)
                if lrc_file.exists():
                    lrc_file.unlink(missing_ok=True)
                self.repo.delete_track(track.id)
                removed_duplicates += 1
            elif act.action_type == "rename":
                self.lib_service.update_track_metadata(
                    track.id,
                    TrackMetadataUpdatePayload(
                        title=act.proposed_title,
                        artist_display=act.proposed_artist,
                        album=act.proposed_album,
                        track_number=track.track_number,
                        release_year=track.release_year,
                        original_year=track.original_year,
                        remember_album_alias=True,
                    ),
                )
                renamed += 1

        return {"renamed": renamed, "removed_duplicates": removed_duplicates}


curation_service = CurationService()
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
            logger.debug("Now Playing update skipped: %s", exc)
            return {"status": "offline", "detail": str(exc)}

    def record_scrobble(self, payload: ScrobblePayload) -> dict[str, Any]:
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
"""Background extraction, direct download, and Full Album Download pipeline with SSE telemetry."""

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
    AlbumDownloadRequest,
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
from musiclinker.services.curation_service import curation_service
from musiclinker.services.library_service import library_service, sanitize_filename


@dataclass
class JobRecord:
    """In-memory state of an active or historical extraction, track download, or full album download job."""

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    current_step: str = "Queued..."
    current_stage: str = "idle"
    lyric_sync_status: str = "Waiting..."
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
            "current_stage": self.current_stage,
            "lyric_sync_status": self.lyric_sync_status,
            "total_candidates": self.total_candidates,
            "downloaded_count": self.downloaded_count,
            "skipped_seen_count": self.skipped_seen_count,
            "excess_saved_path": self.excess_saved_path,
            "logs": self.logs[-60:],
            "tracks_added": self.tracks_added,
            "created_at": self.created_at,
        }


class JobService:
    """Executes Last.fm extraction, single track downloads, and Full Album Downloads with atomic staging."""

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

    def start_album_download_job(self, req: AlbumDownloadRequest) -> JobRecord:
        """Start a background job that resolves and downloads an entire album in track # order (`1..N`)."""
        job_id = uuid.uuid4().hex[:10]
        record = JobRecord(job_id=job_id, job_type="album_download")
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run_album_download_pipeline, record, req)
        return record

    def _run_album_download_pipeline(self, job: JobRecord, req: AlbumDownloadRequest) -> None:
        job.status = JobStatus.RUNNING
        job.current_stage = "resolving_album"
        job_staging = PATHS.staging_dir / job.job_id
        try:
            tracks_to_dl = req.tracks
            canonical_album = self.repo.resolve_album_alias(req.artist, req.album)

            if not tracks_to_dl:
                job.append_log(f"Resolving official tracklist for album '{req.artist} — {req.album}'...")
                lookup = library_service.lookup_external_album_tracklist(req.artist, req.album)
                canonical_album = str(lookup.get("album") or canonical_album)
                raw_list = lookup.get("tracks") or []
                from musiclinker.domain.models import AlbumTrackItem

                tracks_to_dl = [
                    AlbumTrackItem(
                        track_number=int(item.get("track_number") or idx),
                        title=str(item.get("title") or f"Track {idx}"),
                        artist=str(item.get("artist") or req.artist),
                        duration=int(item.get("duration") or 0),
                        youtube_url=str(item.get("youtube_url") or ""),
                    )
                    for idx, item in enumerate(raw_list, start=1)
                ]

            if not tracks_to_dl:
                raise ValueError(f"Could not find tracklist for album '{req.artist} — {req.album}'.")

            job.total_candidates = len(tracks_to_dl)
            job.append_log(f"Album '{canonical_album}' resolved: {len(tracks_to_dl)} track(s) queued.")

            total = len(tracks_to_dl)
            for idx, item in enumerate(tracks_to_dl, start=1):
                if job.cancel_requested:
                    raise JobCancelledError("Album download cancelled by user.")

                thash = compute_track_hash(item.artist, item.title)
                if req.skip_existing and self.repo.is_hash_seen(thash):
                    job.skipped_seen_count += 1
                    job.append_log(f"[{idx}/{total}] Skipping #{item.track_number:02d} {item.title} (already in library)")
                    continue

                base_pct = ((idx - 1) / total) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total}] Downloading Track #{item.track_number:02d}: {item.artist} - {item.title}")
                try:
                    entity = self._acquire_and_tag_single_track(
                        job=job,
                        artist_raw=item.artist,
                        title_raw=item.title,
                        album_hint=canonical_album,
                        track_number_hint=item.track_number,
                        query_or_url=item.youtube_url,
                        playcount=0,
                        bitrate_kbps=req.bitrate_kbps,
                        staging_dir=job_staging,
                        progress_cb=lambda p, b=base_pct, s=total: setattr(
                            job, "progress_pct", b + (p / s)
                        ),
                    )
                    job.downloaded_count += 1
                    job.tracks_added.append(entity.to_dict())
                    job.append_log(
                        f"Indexed #{entity.track_number:02d}: {entity.title} [{entity.album} | Lyrics: {entity.lyric_sync_mode}]"
                    )
                except Exception as track_exc:
                    logger.warning("Album track download failed (%s): %s", item.title, track_exc)
                    job.append_log(f"Skipped #{item.track_number:02d} '{item.title}': {track_exc}")

            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(
                f"Album '{canonical_album}' complete: {job.downloaded_count}/{total} track(s) downloaded."
            )
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.current_stage = "cancelled"
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Album download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _run_extraction_pipeline(self, job: JobRecord, req: ExtractionJobRequest) -> None:
        job.status = JobStatus.RUNNING
        job.current_stage = "extracting_lastfm"
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

            if req.use_llm_filter and raw_candidates:
                job.current_stage = "llm_curation"
                raw_candidates = curation_service.filter_candidates_before_download(
                    raw_candidates, log_cb=job.append_log
                )

            job.total_candidates = len(raw_candidates)
            job.append_log(f"Found {len(raw_candidates)} qualifying track(s).")

            unseen: list[CandidateTrack] = []
            for cand in raw_candidates:
                if req.skip_seen_hashes and self.repo.is_hash_seen(cand.track_hash):
                    job.skipped_seen_count += 1
                else:
                    unseen.append(cand)

            job.append_log(
                f"Deduplication check: {len(unseen)} new track(s), {job.skipped_seen_count} already in library/seen."
            )

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
                    job.append_log(f"Saved {len(export_list)} excess track(s) to {list_path.name}")

            if not req.download_enabled or not selected:
                job.progress_pct = 100.0
                job.current_stage = "completed"
                job.status = JobStatus.COMPLETED
                job.append_log("Extraction finished.")
                return

            total_to_dl = len(selected)
            for idx, cand in enumerate(selected, start=1):
                if job.cancel_requested:
                    raise JobCancelledError("Job cancelled by user.")
                base_pct = ((idx - 1) / total_to_dl) * 100.0
                job.progress_pct = base_pct
                job.append_log(f"[{idx}/{total_to_dl}] Downloading: {cand.artist} - {cand.title} ({cand.playcount} plays)")
                try:
                    entity = self._acquire_and_tag_single_track(
                        job=job,
                        artist_raw=cand.artist,
                        title_raw=cand.title,
                        album_hint=cand.album,
                        track_number_hint=cand.track_number,
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
                    job.append_log(
                        f"Indexed: {entity.artist_display} - {entity.title} [Album: {entity.album} | Lyrics: {entity.lyric_sync_mode}]"
                    )
                except Exception as track_exc:
                    logger.warning("Failed processing track %s - %s: %s", cand.artist, cand.title, track_exc)
                    job.append_log(f"Skipped '{cand.artist} - {cand.title}': {track_exc}")

            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(f"Complete: {job.downloaded_count}/{total_to_dl} track(s) downloaded and tagged.")
        except JobCancelledError as exc:
            job.status = JobStatus.CANCELLED
            job.current_stage = "cancelled"
            job.append_log(str(exc))
        except Exception as exc:
            logger.exception("Extraction job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
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
                job=job,
                artist_raw=req.artist_override,
                title_raw=req.title_override,
                album_hint=req.album_override,
                track_number_hint=req.track_number_override,
                query_or_url=req.query_or_url,
                playcount=0,
                bitrate_kbps=req.bitrate_kbps,
                staging_dir=job_staging,
                progress_cb=lambda p: setattr(job, "progress_pct", p),
            )
            job.downloaded_count = 1
            job.tracks_added.append(entity.to_dict())
            job.progress_pct = 100.0
            job.current_stage = "completed"
            job.status = JobStatus.COMPLETED
            job.append_log(f"Downloaded & tagged: {entity.artist_display} - {entity.title} [{entity.album}]")
        except Exception as exc:
            logger.exception("Direct download job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            job.current_stage = "failed"
            job.append_log(f"Error: {exc}")
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    def _acquire_and_tag_single_track(
        self,
        *,
        job: JobRecord,
        artist_raw: str,
        title_raw: str,
        album_hint: str,
        track_number_hint: int = 0,
        query_or_url: str,
        playcount: int,
        bitrate_kbps: int,
        staging_dir: Path,
        progress_cb: Any = None,
    ) -> TrackEntity:
        job.current_stage = "youtube_audio_and_captions"
        job.lyric_sync_status = "Checking YouTube stream & subtitle cues..."
        if not query_or_url:
            yt_match = youtube_client.resolve_best_audio_match(artist_raw, title_raw)
            target_url = yt_match["url"]
            thumb_url = yt_match.get("thumbnail", "")
        else:
            target_url = query_or_url
            thumb_url = ""

        stem = uuid.uuid4().hex[:8]
        staged_mp3, yt_info, yt_lrc_fallback = youtube_client.download_mp3_to_staging(
            url_or_query=target_url,
            staging_dir=staging_dir,
            stem_name=stem,
            bitrate_kbps=bitrate_kbps,
            progress_cb=progress_cb,
        )

        if not thumb_url and isinstance(yt_info, dict):
            thumb_url = str(yt_info.get("thumbnail") or "")

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

        job.current_stage = "musicbrainz_metadata"
        mb_meta = musicbrainz_client.resolve_release_metadata(
            artist=album_artist,
            title=clean_title,
            fallback_album=album_hint or "Singles",
            fallback_thumb_url=thumb_url,
            fallback_track_number=track_number_hint,
        )
        raw_album = album_hint or mb_meta.album or "Singles"
        final_album = self.repo.resolve_album_alias(album_artist, raw_album, clean_title)
        final_track_num = track_number_hint if track_number_hint > 0 else mb_meta.track_number

        dest_dir = (
            PATHS.downloads_dir
            / sanitize_filename(album_artist, "Unknown Artist")
            / sanitize_filename(final_album, "Singles")
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        num_prefix = f"{final_track_num:02d} - " if final_track_num > 0 else ""
        base_filename = f"{num_prefix}{sanitize_filename(artist_display)} - {sanitize_filename(clean_title)}"
        final_mp3_path = dest_dir / f"{base_filename}.mp3"
        final_lrc_path = dest_dir / f"{base_filename}.lrc"

        job.current_stage = "lyrics_sync"
        approx_duration = int(yt_info.get("duration") or 0)
        lyrics_text, sync_mode, sync_summary = lyrics_client.fetch_and_save_lrc(
            artist=album_artist,
            title=clean_title,
            album=final_album,
            duration=approx_duration,
            target_lrc_path=final_lrc_path,
            youtube_lrc_fallback=yt_lrc_fallback,
        )
        job.lyric_sync_status = sync_summary
        job.append_log(f"Lyric Sync: {sync_summary}")

        job.current_stage = "id3_tagging"
        duration, actual_bitrate = audio_tagger.write_id3v24_tags(
            staged_mp3,
            title=clean_title,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            track_number=final_track_num,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            lastfm_playcount=playcount,
            replaygain_db=-1.5,
            lyrics_text=lyrics_text,
            cover_bytes=mb_meta.cover_bytes,
            cover_mime=mb_meta.cover_mime,
        )

        os.replace(str(staged_mp3), str(final_mp3_path))

        entity = self.repo.upsert_track(
            track_hash=track_hash,
            title=clean_title,
            artist_display=artist_display,
            artists=artists_list,
            album_artist=album_artist,
            album=final_album,
            track_number=final_track_num,
            release_year=mb_meta.release_year,
            original_year=mb_meta.original_year,
            duration=duration or approx_duration,
            file_path=str(final_mp3_path.resolve()),
            lrc_path=str(final_lrc_path.resolve()) if final_lrc_path.exists() else "",
            lyric_sync_mode=sync_mode,
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
"""FastAPI server delivering HTTP 206 streaming, SSE jobs, Layered Hierarchy, Album Downloader, and LLM Curation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS
from musiclinker.core.security import build_pairing_url, detect_lan_ip, verify_request_access
from musiclinker.domain.models import (
    AlbumBulkRenamePayload,
    AlbumDownloadRequest,
    BatchScrobblePayload,
    DirectDownloadRequest,
    ExtractionJobRequest,
    JobStatus,
    LLMCurationApplyPayload,
    ScrobblePayload,
    SettingsUpdatePayload,
    TrackMetadataUpdatePayload,
    TransitionRulePayload,
)
from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient
from musiclinker.infrastructure.tagger import audio_tagger
from musiclinker.infrastructure.youtube_client import youtube_client
from musiclinker.services.curation_service import curation_service
from musiclinker.services.job_service import job_service
from musiclinker.services.library_service import library_service
from musiclinker.services.scrobble_service import scrobble_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        library_service.scan_and_sync_downloads()
        scrobble_service.flush_pending_queue()
    except Exception as exc:
        logger.warning("Startup sync warning: %s", exc)
    yield


def require_paired_client(request: Request) -> None:
    cfg = db_repo.get_settings()
    verify_request_access(
        request=request,
        expected_token=str(cfg.get("lan_pair_token", "")),
        require_auth_on_lan=bool(cfg.get("require_lan_token", True)),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="MusicLinker Personal Server",
        version="3.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Library, Sorting, Layered Hierarchy & Album Endpoints ----

    @app.get("/api/tracks", dependencies=[Depends(require_paired_client)])
    async def list_tracks(
        search: str = "",
        artist: str = "",
        album: str = "",
        sort_by: str = "track_num",
    ) -> dict[str, Any]:
        tracks = db_repo.list_tracks(search=search, artist=artist, album=album, sort_by=sort_by)
        return {"tracks": [t.to_dict() for t in tracks], "total": len(tracks)}

    @app.get("/api/artists", dependencies=[Depends(require_paired_client)])
    async def list_artists() -> dict[str, Any]:
        return {"artists": db_repo.list_artists_summary()}

    @app.get("/api/albums", dependencies=[Depends(require_paired_client)])
    async def list_albums() -> dict[str, Any]:
        return {
            "albums": db_repo.list_albums_summary(),
            "aliases": db_repo.list_album_aliases(),
        }

    @app.get("/api/albums/lookup", dependencies=[Depends(require_paired_client)])
    async def lookup_external_album(
        artist: str = Query(default=""),
        album: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        """Resolve an album's tracklist (`1..N`) from MusicBrainz, Last.fm, or YouTube prior to downloading."""
        return library_service.lookup_external_album_tracklist(artist=artist, album_or_url=album)

    @app.post("/api/albums/rename", dependencies=[Depends(require_paired_client)])
    async def rename_album_endpoint(payload: AlbumBulkRenamePayload) -> dict[str, Any]:
        return library_service.bulk_rename_album(payload)

    @app.put("/api/tracks/{track_id}/metadata", dependencies=[Depends(require_paired_client)])
    async def update_track_metadata_endpoint(track_id: int, payload: TrackMetadataUpdatePayload) -> dict[str, Any]:
        try:
            updated = library_service.update_track_metadata(track_id, payload)
            return {"track": updated.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/library/rescan", dependencies=[Depends(require_paired_client)])
    async def rescan_library() -> dict[str, int]:
        return library_service.scan_and_sync_downloads()

    # ---- LLM Smart Curator & Deduplication Endpoints ----

    @app.post("/api/curation/analyze", dependencies=[Depends(require_paired_client)])
    async def analyze_library_with_llm() -> dict[str, Any]:
        return curation_service.analyze_library()

    @app.post("/api/curation/apply", dependencies=[Depends(require_paired_client)])
    async def apply_llm_curation(payload: LLMCurationApplyPayload) -> dict[str, int]:
        return curation_service.apply_curation_actions(payload.actions)

    # ---- HTTP 206 Audio Streaming, Cover Art & 3-Tier Lyrics ----

    @app.get("/api/tracks/{track_id}/stream", dependencies=[Depends(require_paired_client)])
    async def stream_track(track_id: int, request: Request) -> Response:
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

    # ---- YouTube Search, Last.fm Extraction & Full Album Download Jobs ----

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

    @app.post("/api/jobs/download-album", dependencies=[Depends(require_paired_client)])
    async def start_album_download(req: AlbumDownloadRequest) -> dict[str, Any]:
        job = job_service.start_album_download_job(req)
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
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        async def _event_generator() -> AsyncIterator[str]:
            while True:
                payload = json.dumps(job.to_dict())
                yield f"data: {payload}\n\n"
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(_event_generator(), media_type="text/event-stream")

    # ---- Phone / APK LAN Pairing & Settings Endpoints ----

    @app.get("/api/sync/info", dependencies=[Depends(require_paired_client)])
    async def get_sync_info(request: Request) -> dict[str, Any]:
        cfg = db_repo.get_settings()
        port = request.url.port or 8000
        token = str(cfg.get("lan_pair_token", ""))
        pairing_url = build_pairing_url(port=port, pair_token=token)
        return {
            "lan_ip": detect_lan_ip(),
            "port": port,
            "server_origin": f"http://{detect_lan_ip()}:{port}",
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
            "auto_clean_albums": cfg.get("auto_clean_albums", True),
            "llm_base_url": cfg.get("llm_base_url", "https://api.openai.com/v1"),
            "llm_api_key_configured": bool(cfg.get("llm_api_key")),
            "llm_model": cfg.get("llm_model", "gpt-4o-mini"),
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

    # ---- PWA / APK Static Files & Root Entrypoint ----

    if (PATHS.web_dir / "js").exists():
        app.mount("/js", StaticFiles(directory=str(PATHS.web_dir / "js")), name="js")
    if (PATHS.web_dir / "css").exists():
        app.mount("/css", StaticFiles(directory=str(PATHS.web_dir / "css")), name="css")

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


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
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
    img = Image.new("RGBA", (64, 64), (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=(16, 185, 129, 255))
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
        _wait_for_server("127.0.0.1", self.port)
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
                pystray.MenuItem("Toggle Randomizer", lambda: self._eval_js("window.MusicLinkerApp?.toggleShuffle()")),
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
                title="MusicLinker Studio v3.0",
                url=self.local_url,
                width=1380,
                height=880,
                min_size=(980, 660),
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
"""Interactive CLI Wizard and scriptable headless runner for Last.fm extraction and Full Album downloading."""

from __future__ import annotations

import argparse
import sys
import time

from musiclinker.domain.models import AlbumDownloadRequest, ExtractionJobRequest, JobStatus
from musiclinker.infrastructure.db import db_repo
from musiclinker.services.job_service import job_service


def run_interactive_wizard() -> ExtractionJobRequest:
    cfg = db_repo.get_settings()
    print("=== MusicLinker v3.0 Interactive CLI Wizard ===")
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

    llm_choice = input("Use LLM Smart Filter to deduplicate & fix albums before download? (Y/n): ").strip().lower()
    use_llm = llm_choice not in ("n", "no")

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
        use_llm_filter=use_llm,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MusicLinker Headless CLI & Album Downloader")
    parser.add_argument("--username", type=str, help="Last.fm username")
    parser.add_argument("--period", type=str, default="12month", help="Last.fm period or 'custom_year'")
    parser.add_argument("--year", type=int, default=None, help="Calendar year when --period custom_year")
    parser.add_argument("--min-plays", type=int, default=10, help="Minimum play count filter (default: 10)")
    parser.add_argument("--cap", type=int, default=25, help="Maximum tracks to download (default: 25)")
    parser.add_argument("--bitrate", type=int, default=320, choices=[192, 320], help="Target MP3 bitrate")
    parser.add_argument("--llm-filter", action="store_true", help="Enable LLM deduplication & album fixing")
    parser.add_argument("--download", action="store_true", help="Enable MP3 downloading in non-interactive mode")
    parser.add_argument("--artist", type=str, help="Artist name for full album download mode")
    parser.add_argument("--album", type=str, help="Album title or playlist URL for full album download mode")
    args = parser.parse_args()

    if args.album:
        job = job_service.start_album_download_job(
            AlbumDownloadRequest(
                artist=args.artist or "Unknown Artist",
                album=args.album,
                bitrate_kbps=args.bitrate,
            )
        )
    elif not args.username:
        req = run_interactive_wizard()
        job = job_service.start_lastfm_extraction_job(req)
    else:
        req = ExtractionJobRequest(
            username=args.username,
            period=args.period,
            year=args.year,
            min_plays=args.min_plays,
            download_cap=args.cap,
            bitrate_kbps=args.bitrate,
            download_enabled=args.download,
            use_llm_filter=args.llm_filter,
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
    col2.metric("Distinct Albums", len(db_repo.list_albums_summary()))
    col3.metric("Pending Offline Scrobbles", len(db_repo.get_pending_scrobbles(limit=500)))

    st.subheader("Indexed Local Library")
    if tracks:
        st.dataframe(
            tracks,
            column_order=[
                "id",
                "track_number",
                "artist_display",
                "title",
                "album",
                "lyric_sync_mode",
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
  "name": "MusicLinker Studio & APK Player",
  "short_name": "MusicLinker",
  "description": "Offline-first personal music player with 3-Layer Selection, Play by Album, Randomizer, Album Downloader, and AI Curator.",
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
  <defs>
    <linearGradient id="mlGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#10b981"/>
      <stop offset="100%" stop-color="#059669"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="56" fill="#09090b"/>
  <circle cx="128" cy="128" r="92" fill="none" stroke="url(#mlGrad)" stroke-width="14"/>
  <circle cx="128" cy="128" r="30" fill="url(#mlGrad)"/>
  <path d="M110 94 L166 128 L110 162 Z" fill="#09090b"/>
</svg>
```

### File: `src/musiclinker/web/css/styles.css`
```css
/* Custom Range Sliders (Seek Bar, Volume Slider, Crossfade Slider) */
input[type="range"].ml-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 9999px;
  background: linear-gradient(
    90deg,
    #10b981 0%,
    #34d399 var(--progress, 0%),
    #27272a var(--progress, 0%),
    #27272a 100%
  );
  outline: none;
  cursor: pointer;
  transition: height 0.15s ease, box-shadow 0.15s ease;
}

input[type="range"].ml-slider:hover {
  height: 8px;
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.25);
}

input[type="range"].ml-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #ecfdf5;
  border: 2.5px solid #10b981;
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.8);
  cursor: pointer;
  transition: transform 0.12s ease, background-color 0.12s ease, box-shadow 0.12s ease;
}

input[type="range"].ml-slider:hover::-webkit-slider-thumb,
input[type="range"].ml-slider:active::-webkit-slider-thumb {
  transform: scale(1.25);
  background: #10b981;
  border-color: #ffffff;
  box-shadow: 0 0 16px rgba(16, 185, 129, 1);
}

input[type="range"].ml-slider::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #ecfdf5;
  border: 2.5px solid #10b981;
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.8);
  cursor: pointer;
  transition: transform 0.12s ease;
}

input[type="range"].ml-slider:hover::-moz-range-thumb {
  transform: scale(1.25);
  background: #10b981;
}

/* Isolated Dark Glass Scrollbars */
.ml-scroll::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.ml-scroll::-webkit-scrollbar-track {
  background: rgba(24, 24, 27, 0.4);
  border-radius: 9999px;
}

.ml-scroll::-webkit-scrollbar-thumb {
  background: rgba(82, 82, 91, 0.65);
  border-radius: 9999px;
}

.ml-scroll::-webkit-scrollbar-thumb:hover {
  background: rgba(16, 185, 129, 0.75);
}

/* Lyric Line Transitions inside Isolated Scroll Box */
#lyrics-container {
  position: relative;
  scroll-behavior: smooth;
  overscroll-behavior: contain;
}

.lyric-line {
  padding: 0.45rem 0.75rem;
  border-radius: 0.75rem;
  transition: color 0.2s ease, background-color 0.2s ease, transform 0.2s ease;
  cursor: pointer;
}

.lyric-line:hover {
  background: rgba(39, 39, 42, 0.55);
  color: #e4e4e7;
}

.lyric-line-active {
  color: #34d399;
  font-weight: 700;
  font-size: 1.125rem;
  background: rgba(16, 185, 129, 0.12);
  border-left: 3px solid #10b981;
}
```

### File: `src/musiclinker/web/sw.js`
```javascript
const STATIC_CACHE = "musiclinker-static-v3.0";
const AUDIO_CACHE = "musiclinker-audio-v3.0";

const APP_SHELL = [
  "/",
  "/manifest.json",
  "/icon.svg",
  "/css/styles.css",
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
 * APK-aware API client supporting dynamic PC Server Origin (`http://<LAN_IP>:8000`) and pairing tokens.
 */
class MusicLinkerAPI {
  constructor() {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("pair_token");
    const urlServer = params.get("server");
    if (urlToken) {
      localStorage.setItem("ml_pair_token", urlToken);
    }
    if (urlServer) {
      this.setServerOrigin(urlServer);
    }
    if (urlToken || urlServer) {
      window.history.replaceState({}, document.title, "/");
    }
  }

  getPairToken() {
    return localStorage.getItem("ml_pair_token") || "";
  }

  setPairToken(token) {
    if (token) {
      localStorage.setItem("ml_pair_token", token.trim());
    }
  }

  /**
   * Returns the configured remote PC server origin when running inside the Capacitor Android APK,
   * or "" (relative current origin) when running directly on the PC or LAN browser.
   */
  getServerOrigin() {
    const saved = (localStorage.getItem("ml_server_origin") || "").trim().replace(/\/+$/, "");
    if (saved) return saved;
    const proto = window.location.protocol;
    const host = window.location.hostname;
    // Inside Capacitor APK, protocol is capacitor: or hostname is localhost without port 8000
    if (proto === "capacitor:" || proto === "file:") {
      return "";
    }
    return "";
  }

  setServerOrigin(originUrl) {
    if (!originUrl) {
      localStorage.removeItem("ml_server_origin");
      return;
    }
    try {
      const parsed = new URL(originUrl.trim());
      const tokenParam = parsed.searchParams.get("pair_token");
      if (tokenParam) {
        this.setPairToken(tokenParam);
      }
      localStorage.setItem("ml_server_origin", `${parsed.protocol}//${parsed.host}`);
    } catch (_err) {
      localStorage.setItem("ml_server_origin", originUrl.trim().replace(/\/+$/, ""));
    }
  }

  resolveUrl(path) {
    if (!path) return "";
    if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("blob:")) {
      return path;
    }
    const base = this.getServerOrigin();
    return `${base}${path.startsWith("/") ? path : `/${path}`}`;
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

    const fullUrl = this.resolveUrl(path);
    const resp = await fetch(fullUrl, {
      ...options,
      headers,
    });
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  subscribeJobEvents(jobId, onUpdate) {
    const token = encodeURIComponent(this.getPairToken());
    const url = this.resolveUrl(`/api/jobs/${jobId}/events${token ? `?pair_token=${token}` : ""}`);
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
 * IndexedDB + Cache API offline storage manager for Android APK / PWA:
 *  - Caches single tracks or entire albums for 100% offline playback
 *  - Serves direct Blob URLs when offline inside the native Android APK
 *  - Queues offline scrobbles in IndexedDB and flushes them in batch when reconnected
 */
const DB_NAME = "musiclinker_pwa_db";
const DB_VERSION = 1;
const AUDIO_CACHE_NAME = "musiclinker-audio-v3.0";

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

  canonicalCacheKey(trackId) {
    return `/api/tracks/${trackId}/file`;
  }

  async saveTrackForOffline(track, apiClient) {
    const canonicalKey = this.canonicalCacheKey(track.id);
    const fetchUrl = apiClient.resolveUrl(canonicalKey);
    const token = apiClient.getPairToken();
    const headers = token ? { "X-Pair-Token": token } : {};

    const response = await fetch(fetchUrl, { headers });
    if (!response.ok) {
      throw new Error(`Failed to fetch audio blob (${response.status})`);
    }
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.put(canonicalKey, response.clone());

    // Cache lyrics JSON inside track record so lyrics work 100% offline in the APK
    let offlineLyrics = null;
    if (track.lyrics_url) {
      try {
        offlineLyrics = await apiClient.request(track.lyrics_url);
      } catch (_err) {}
    }

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").put({
        ...track,
        offline_lyrics: offlineLyrics,
        saved_offline_at: Date.now(),
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async saveBatchTracksForOffline(tracks, apiClient, onProgress) {
    let saved = 0;
    for (let i = 0; i < tracks.length; i++) {
      await this.saveTrackForOffline(tracks[i], apiClient);
      saved++;
      onProgress?.(saved, tracks.length, tracks[i]);
    }
    return saved;
  }

  async getOfflineAudioBlobUrl(trackId) {
    try {
      const cache = await caches.open(AUDIO_CACHE_NAME);
      const match = await cache.match(this.canonicalCacheKey(trackId));
      if (!match) return null;
      const blob = await match.blob();
      return URL.createObjectURL(blob);
    } catch (_err) {
      return null;
    }
  }

  async getOfflineTrackRecord(trackId) {
    const db = await this.dbPromise;
    return new Promise((resolve) => {
      const tx = db.transaction("offline_tracks", "readonly");
      const req = tx.objectStore("offline_tracks").get(Number(trackId));
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
  }

  async removeOfflineTrack(trackId) {
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.delete(this.canonicalCacheKey(trackId));

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
 * HTML5 Audio + APK Offline Blob Playback + ReplayGain Volume Normalization + Scrobble Tracker + MediaSession Bridge.
 */
import { offlineStore } from "./offline-store.js";

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
    this.currentBlobUrl = null;
    this.normalizeEnabled = true;
    this.baseVolume = 0.85;
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

  setBaseVolume(fraction) {
    this.baseVolume = Math.max(0, Math.min(1, fraction));
    this.applyReplayGain(this.currentTrack);
  }

  applyReplayGain(track) {
    if (!this.normalizeEnabled || !track) {
      this.audio.volume = this.baseVolume;
      return;
    }
    const db = Number(track.replaygain_db || 0);
    const linearFactor = Math.pow(10, db / 20);
    const targetVolume = Math.max(0.05, Math.min(1.0, this.baseVolume * linearFactor));
    this.audio.volume = targetVolume;
  }

  async loadAndPlay(track, apiClient) {
    this.currentTrack = track;
    this.scrobbledCurrentTrack = false;
    this.accumulatedPlaySeconds = 0;
    this.lastTickTimestamp = null;

    if (this.currentBlobUrl) {
      URL.revokeObjectURL(this.currentBlobUrl);
      this.currentBlobUrl = null;
    }

    this.applyReplayGain(track);

    // 1. Check if track is cached locally in CacheStorage (instant 100% offline playback on Android APK)
    const offlineBlobUrl = await offlineStore.getOfflineAudioBlobUrl(track.id);
    if (offlineBlobUrl) {
      this.currentBlobUrl = offlineBlobUrl;
      this.audio.removeAttribute("crossorigin");
      this.audio.src = offlineBlobUrl;
    } else {
      this.audio.crossOrigin = "anonymous";
      const baseStreamUrl = apiClient.resolveUrl(track.stream_url);
      const pairToken = apiClient.getPairToken();
      const sep = baseStreamUrl.includes("?") ? "&" : "?";
      this.audio.src = pairToken
        ? `${baseStreamUrl}${sep}pair_token=${encodeURIComponent(pairToken)}`
        : baseStreamUrl;
    }

    await this.audio.play();
    this._setupMediaSession(track, apiClient);
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

  seekToSeconds(seconds) {
    const dur = this.audio.duration || (this.currentTrack?.duration || 0);
    if (dur > 0) {
      this.audio.currentTime = Math.max(0, Math.min(dur, seconds));
    }
  }

  seekToFraction(fraction) {
    const dur = this.audio.duration || (this.currentTrack?.duration || 0);
    if (dur > 0) {
      this.audio.currentTime = Math.max(0, Math.min(dur, dur * fraction));
    }
  }

  _setupMediaSession(track, apiClient) {
    if (!("mediaSession" in navigator)) return;
    const coverSrc = track.cover_url ? apiClient.resolveUrl(track.cover_url) : "/icon.svg";
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist_display,
      album: track.album || "MusicLinker",
      artwork: [{ src: coverSrc, sizes: "512x512", type: "image/jpeg" }],
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
 * Main UI controller for MusicLinker Studio & Android APK v3.0:
 *  - Play by Album (1..N track_number order), Randomizer (Shuffle), and Sort by Track # / Alphabetical / Plays
 *  - 3-Layer Selection Mode:
 *      Layer 1 (Artists): starts 100% unselected
 *      Layer 2 (Albums, optional): starts 100% unselected (if 0 selected, all albums for selected artists pass through)
 *      Layer 3 (Tracks): starts 100% SELECTED for all matching tracks
 *  - Full Album Downloader (MusicBrainz / Last.fm / YouTube playlist lookup + batch download) & Offline Album Caching
 *  - Isolated #lyrics-container scroll math + 3-Tier Synced/Rough Lyrics
 */
import { api } from "./api.js";
import { offlineStore } from "./offline-store.js";
import { AudioEngine } from "./audio-engine.js";

class MusicLinkerController {
  constructor() {
    this.allTracks = [];
    this.tracks = [];
    this.playQueue = [];
    this.albums = [];
    this.selectedAlbum = "";
    this.sortBy = "track_num";
    this.shuffleEnabled = false;
    this.offlineIds = new Set();
    this.currentIndex = -1;

    // 3-Layer Selection Mode State
    // Layer 1 (Artists): starts 100% UNSELECTED
    // Layer 2 (Albums): starts 100% UNSELECTED
    // Layer 3 (Tracks): starts 100% SELECTED (we track only explicitly unchecked track IDs)
    this.viewMode = "standard"; // 'standard' | 'layered'
    this.layerSelectedArtists = new Set();
    this.layerSelectedAlbums = new Set();
    this.layerExcludedTrackIds = new Set();

    // External Album Downloader Preview State
    this.previewedAlbumTracks = [];

    // Lyrics & Non-Hijacking Scroll State
    this.lyricsData = { synced: false, sync_mode: "none", lines: [] };
    this.lastActiveLyricIdx = -1;
    this.lyricOffsetSec = 0.0;
    this.userScrollLockUntil = 0;

    // LLM Curator State
    this.proposedCurationActions = [];

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
    if ("serviceWorker" in navigator && window.location.protocol.startsWith("http")) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    this.bindDOMEvents();
    this.initAllSliderFills();
    await this.refreshOfflineState();
    await this.loadSettingsIntoUI();
    await this.loadAlbums();
    await this.loadLibrary();
    offlineStore.flushOfflineScrobbles(api).catch(() => {});
  }

  // ---- Custom Slider Fill Helper ----

  updateSliderFill(sliderEl) {
    if (!sliderEl) return;
    const min = Number(sliderEl.min || 0);
    const max = Number(sliderEl.max || 100);
    const val = Number(sliderEl.value || 0);
    const pct = max > min ? ((val - min) / (max - min)) * 100 : 0;
    sliderEl.style.setProperty("--progress", `${Math.max(0, Math.min(100, pct))}%`);
  }

  initAllSliderFills() {
    document.querySelectorAll("input.ml-slider").forEach((el) => {
      this.updateSliderFill(el);
      el.addEventListener("input", () => this.updateSliderFill(el));
    });
  }

  // ---- Library, Sorting & Play by Album ----

  async refreshOfflineState() {
    const cached = await offlineStore.getOfflineTracks();
    this.offlineIds = new Set(cached.map((t) => t.id));
  }

  async loadAlbums() {
    try {
      const data = await api.request("/api/albums");
      this.albums = data.albums || [];
    } catch (_err) {
      const cached = await offlineStore.getOfflineTracks();
      const map = new Map();
      cached.forEach((t) => {
        const key = t.album || "Singles";
        const cur = map.get(key) || { album: key, album_artist: t.album_artist || t.artist_display, track_count: 0 };
        cur.track_count++;
        map.set(key, cur);
      });
      this.albums = Array.from(map.values());
    }
    this.renderAlbumFilterBar();
  }

  renderAlbumFilterBar() {
    const bar = document.getElementById("album-filter-bar");
    const albumActions = document.getElementById("selected-album-actions");
    if (!bar) return;

    if (albumActions) {
      albumActions.classList.toggle("hidden", !this.selectedAlbum);
    }

    const allActive = !this.selectedAlbum;
    let html = `
      <button
        data-album-filter=""
        class="px-3 py-1 rounded-full text-xs font-semibold shrink-0 transition border ${
          allActive
            ? "bg-emerald-500 text-zinc-950 border-emerald-400 shadow-sm"
            : "bg-zinc-900/90 text-zinc-300 border-zinc-800 hover:border-zinc-600"
        }">
        All Albums
      </button>
    `;

    html += this.albums
      .map((a) => {
        const active = this.selectedAlbum.toLowerCase() === (a.album || "").toLowerCase();
        return `
          <button
            data-album-filter="${this.escapeAttr(a.album)}"
            title="${this.escapeAttr(a.album_artist)} — ${this.escapeAttr(a.album)}"
            class="px-3 py-1 rounded-full text-xs font-medium shrink-0 transition border flex items-center gap-1.5 ${
              active
                ? "bg-emerald-500 text-zinc-950 border-emerald-400 font-semibold"
                : "bg-zinc-900/80 text-zinc-300 border-zinc-800 hover:border-zinc-600"
            }">
            <span class="truncate max-w-[140px]">${this.escape(a.album)}</span>
            <span class="opacity-70 text-[10px]">(${a.track_count})</span>
          </button>
        `;
      })
      .join("");

    bar.innerHTML = html;
    bar.querySelectorAll("[data-album-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this.selectedAlbum = btn.dataset.albumFilter || "";
        this.renderAlbumFilterBar();
        const searchVal = document.getElementById("search-input")?.value || "";
        this.loadLibrary(searchVal);
      });
    });
  }

  sortTracksClientSide(list, sortMode) {
    const copy = [...list];
    if (sortMode === "track_num") {
      copy.sort((a, b) => {
        const albCmp = (a.album || "").localeCompare(b.album || "", undefined, { sensitivity: "base" });
        if (albCmp !== 0) return albCmp;
        const tnA = a.track_number > 0 ? a.track_number : 9999;
        const tnB = b.track_number > 0 ? b.track_number : 9999;
        if (tnA !== tnB) return tnA - tnB;
        return (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" });
      });
    } else if (sortMode === "alpha_asc") {
      copy.sort((a, b) => (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" }));
    } else if (sortMode === "alpha_desc") {
      copy.sort((a, b) => (b.title || "").localeCompare(a.title || "", undefined, { sensitivity: "base" }));
    } else if (sortMode === "plays_desc") {
      copy.sort((a, b) => (b.lastfm_playcount + b.local_playcount) - (a.lastfm_playcount + a.local_playcount));
    } else if (sortMode === "recent") {
      copy.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    }
    return copy;
  }

  async loadLibrary(search = "") {
    try {
      const allRes = await api.request(`/api/tracks?sort_by=${encodeURIComponent(this.sortBy)}`);
      this.allTracks = allRes.tracks || [];
      const qs = new URLSearchParams({
        search,
        album: this.selectedAlbum,
        sort_by: this.sortBy,
      });
      const data = await api.request(`/api/tracks?${qs.toString()}`);
      this.tracks = data.tracks || [];
    } catch (_err) {
      const cached = await offlineStore.getOfflineTracks();
      this.allTracks = this.sortTracksClientSide(cached, this.sortBy);
      let filtered = this.allTracks;
      if (this.selectedAlbum) {
        filtered = filtered.filter((t) => (t.album || "").toLowerCase() === this.selectedAlbum.toLowerCase());
      }
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        filtered = filtered.filter(
          (t) =>
            (t.title || "").toLowerCase().includes(q) ||
            (t.artist_display || "").toLowerCase().includes(q) ||
            (t.album || "").toLowerCase().includes(q)
        );
      }
      this.tracks = filtered;
    }

    if (!this.playQueue.length) {
      this.playQueue = [...this.tracks];
    }

    const countBadge = document.getElementById("library-count-badge");
    if (countBadge) countBadge.textContent = `${this.tracks.length} tracks`;
    this.renderTrackList();
    this.renderLayeredSelector();
  }

  renderTrackList() {
    const container = document.getElementById("track-list");
    if (!container) return;
    if (!this.tracks.length) {
      container.innerHTML = `
        <div class="p-10 text-center text-zinc-400 space-y-2">
          <div class="text-2xl">🎵</div>
          <div class="text-sm">No tracks match the current filter.</div>
          <div class="text-xs text-zinc-500">Use the <b>Extractor & Album Downloader</b> tab to pull tracks or full studio albums.</div>
        </div>`;
      return;
    }

    const activeTrackId = this.playQueue[this.currentIndex]?.id;

    container.innerHTML = this.tracks
      .map((t, idx) => {
        const isOffline = this.offlineIds.has(t.id);
        const isCurrent = t.id === activeTrackId;
        const yearBadge = t.original_year || t.release_year || "";
        const numBadge = t.track_number > 0 ? `#${String(t.track_number).padStart(2, "0")}` : "";
        const coverSrc = t.cover_url ? api.resolveUrl(t.cover_url) : "";
        const syncIcon =
          t.lyric_sync_mode === "exact_lrc"
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" title="Exact LRCLIB Synced Lyrics">LRC</span>`
            : t.lyric_sync_mode === "youtube_captions" || t.lyric_sync_mode === "rough_interpolated"
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30" title="Rough Synced Lyrics">≈Sync</span>`
            : "";

        return `
          <div class="group flex items-center justify-between p-2.5 rounded-xl transition border ${
            isCurrent
              ? "bg-emerald-950/50 border-emerald-500/40 shadow-sm"
              : "border-transparent hover:bg-zinc-800/60 hover:border-zinc-800"
          }">
            <div class="flex items-center gap-3 min-w-0 flex-1 cursor-pointer" data-play-idx="${idx}">
              <div class="w-11 h-11 rounded-lg bg-zinc-800 flex items-center justify-center overflow-hidden shrink-0 border border-zinc-700/50">
                ${
                  coverSrc
                    ? `<img src="${coverSrc}" class="w-full h-full object-cover" alt="cover"/>`
                    : `<span class="text-emerald-400 font-bold">♪</span>`
                }
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  ${numBadge ? `<span class="text-[11px] font-mono text-emerald-400 font-semibold">${numBadge}</span>` : ""}
                  <span class="font-semibold text-sm text-zinc-100 truncate">${this.escape(t.title)}</span>
                  ${syncIcon}
                </div>
                <div class="text-xs text-zinc-400 truncate">
                  <span class="text-zinc-300">${this.escape(t.artist_display)}</span>
                  <span class="mx-1 text-zinc-600">•</span>
                  <span class="text-emerald-400/90 hover:underline" data-filter-album="${this.escapeAttr(t.album)}">${this.escape(t.album)}</span>
                  ${yearBadge ? `<span class="text-zinc-500 ml-1">(${yearBadge})</span>` : ""}
                </div>
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0 ml-2">
              <span class="text-[11px] text-zinc-500 hidden md:inline font-mono">${t.lastfm_playcount || 0}p</span>
              <button
                data-edit-idx="${idx}"
                title="Rename Track, Track #, or Album Internally"
                class="px-2 py-1 rounded-lg text-xs text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700/60 border border-transparent hover:border-zinc-600 transition">
                ✎
              </button>
              <button
                data-offline-idx="${idx}"
                title="${isOffline ? "Saved for Offline (tap to remove)" : "Save for Offline"}"
                class="px-2.5 py-1 rounded-lg text-xs font-medium border transition ${
                  isOffline
                    ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                    : "border-zinc-700/80 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
                }">
                ${isOffline ? "✓ Offline" : "↓"}
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    container.querySelectorAll("[data-play-idx]").forEach((el) => {
      el.addEventListener("click", (e) => {
        const albumSpan = e.target.closest("[data-filter-album]");
        if (albumSpan) {
          e.stopPropagation();
          this.selectedAlbum = albumSpan.dataset.filterAlbum || "";
          this.renderAlbumFilterBar();
          this.loadLibrary(document.getElementById("search-input")?.value || "");
          return;
        }
        this.playQueue = [...this.tracks];
        this.playByIndex(Number(el.dataset.playIdx));
      });
    });

    container.querySelectorAll("[data-edit-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const track = this.tracks[Number(btn.dataset.editIdx)];
        if (track) this.openEditTrackModal(track);
      });
    });

    container.querySelectorAll("[data-offline-idx]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = Number(btn.dataset.offlineIdx);
        await this.toggleOfflineTrack(this.tracks[idx], btn);
      });
    });
  }

  // ---- Play by Album, Randomizer (Shuffle), and Offline Album Caching ----

  async playSelectedAlbumInOrder() {
    if (!this.selectedAlbum) return;
    const albumTracks = this.sortTracksClientSide(
      this.allTracks.filter((t) => (t.album || "").toLowerCase() === this.selectedAlbum.toLowerCase()),
      "track_num"
    );
    if (!albumTracks.length) return;
    this.shuffleEnabled = false;
    this.updateShuffleButtonUI();
    this.playQueue = albumTracks;
    await this.playByIndex(0);
  }

  async cacheSelectedAlbumOffline() {
    if (!this.selectedAlbum) return;
    const albumTracks = this.allTracks.filter(
      (t) => (t.album || "").toLowerCase() === this.selectedAlbum.toLowerCase()
    );
    if (!albumTracks.length) return;
    const btn = document.getElementById("btn-cache-selected-album");
    if (btn) {
      btn.disabled = true;
      btn.textContent = `Caching 0/${albumTracks.length}...`;
    }
    try {
      await offlineStore.saveBatchTracksForOffline(albumTracks, api, (done, total) => {
        if (btn) btn.textContent = `Caching ${done}/${total}...`;
      });
      await this.refreshOfflineState();
      this.renderTrackList();
      if (btn) btn.textContent = "✓ Album Cached Offline";
    } catch (err) {
      alert(`Failed caching album offline: ${err.message}`);
      if (btn) btn.textContent = "↓ Cache Album Offline";
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  toggleShuffle() {
    this.shuffleEnabled = !this.shuffleEnabled;
    this.updateShuffleButtonUI();
  }

  randomizeAndPlayCurrentView() {
    const pool = this.viewMode === "layered" ? this.getLayerSelectedTracks() : this.tracks;
    if (!pool.length) return;
    const shuffled = [...pool];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    this.shuffleEnabled = true;
    this.updateShuffleButtonUI();
    this.playQueue = shuffled;
    this.playByIndex(0);
  }

  updateShuffleButtonUI() {
    const btn = document.getElementById("btn-shuffle");
    if (!btn) return;
    btn.className = this.shuffleEnabled
      ? "px-3 py-1.5 rounded-xl bg-emerald-500/20 border border-emerald-500/50 text-emerald-300 text-xs font-semibold transition"
      : "px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-xs font-medium transition";
  }

  // ---- 3-Layer Hierarchical Selection Mode (Artists [Unselected] -> Albums [Unselected] -> Tracks [Selected]) ----

  switchLibraryViewMode(mode) {
    this.viewMode = mode;
    const stdPanel = document.getElementById("standard-library-view");
    const layerPanel = document.getElementById("layered-selector-view");
    const btnStd = document.getElementById("btn-view-standard");
    const btnLayer = document.getElementById("btn-view-layered");

    const isLayered = mode === "layered";
    if (stdPanel) stdPanel.classList.toggle("hidden", isLayered);
    if (layerPanel) layerPanel.classList.toggle("hidden", !isLayered);

    if (btnStd) {
      btnStd.className = !isLayered
        ? "px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-500 text-zinc-950"
        : "px-3 py-1 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200";
    }
    if (btnLayer) {
      btnLayer.className = isLayered
        ? "px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-500 text-zinc-950"
        : "px-3 py-1 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200";
    }
    if (isLayered) {
      this.renderLayeredSelector();
    }
  }

  /**
   * Computes candidate tracks for Layer 3 based on:
   *  - Layer 1 (`this.layerSelectedArtists`): starts empty (unselected).
   *  - Layer 2 (`this.layerSelectedAlbums`): starts empty (unselected).
   *    If 0 albums are selected in Layer 2, ALL albums by the selected artists pass through!
   *    If >=1 albums are selected in Layer 2, only those albums pass through.
   *  - Layer 3 (`tracks`): ALL matching tracks are SELECTED by default unless in `this.layerExcludedTrackIds`.
   */
  getLayerCandidateTracks() {
    if (this.layerSelectedArtists.size === 0) {
      return [];
    }
    let pool = this.allTracks.filter((t) =>
      this.layerSelectedArtists.has((t.album_artist || t.artist_display || "").toLowerCase())
    );
    if (this.layerSelectedAlbums.size > 0) {
      pool = pool.filter((t) => this.layerSelectedAlbums.has((t.album || "").toLowerCase()));
    }
    return this.sortTracksClientSide(pool, this.sortBy);
  }

  getLayerSelectedTracks() {
    return this.getLayerCandidateTracks().filter((t) => !this.layerExcludedTrackIds.has(t.id));
  }

  renderLayeredSelector() {
    const artistsBox = document.getElementById("layer-1-artists");
    const albumsBox = document.getElementById("layer-2-albums");
    const tracksBox = document.getElementById("layer-3-tracks");
    const summaryEl = document.getElementById("layer-selection-summary");
    if (!artistsBox || !albumsBox || !tracksBox) return;

    // Group distinct artists from allTracks
    const artistMap = new Map();
    this.allTracks.forEach((t) => {
      const name = t.album_artist || t.artist_display || "Unknown Artist";
      const key = name.toLowerCase();
      const entry = artistMap.get(key) || { name, key, count: 0 };
      entry.count++;
      artistMap.set(key, entry);
    });
    const artists = Array.from(artistMap.values()).sort((a, b) => a.name.localeCompare(b.name));

    // Render Layer 1: Artists (Default: ALL UNSELECTED)
    artistsBox.innerHTML = artists.length
      ? artists
          .map((art) => {
            const checked = this.layerSelectedArtists.has(art.key);
            return `
              <label class="flex items-center justify-between p-2 rounded-xl border text-xs cursor-pointer transition ${
                checked
                  ? "bg-emerald-500/15 border-emerald-500/50 text-emerald-200 font-semibold"
                  : "bg-zinc-950/60 border-zinc-800/80 text-zinc-400 hover:border-zinc-700"
              }">
                <div class="flex items-center gap-2 truncate">
                  <input type="checkbox" data-layer-artist="${this.escapeAttr(art.key)}" ${checked ? "checked" : ""} class="accent-emerald-500" />
                  <span class="truncate">${this.escape(art.name)}</span>
                </div>
                <span class="text-[10px] font-mono opacity-75">${art.count}</span>
              </label>
            `;
          })
          .join("")
      : `<div class="text-xs text-zinc-500 p-3">No artists in library.</div>`;

    // Group albums belonging to selected artists in Layer 1
    const albumMap = new Map();
    this.allTracks.forEach((t) => {
      const artKey = (t.album_artist || t.artist_display || "Unknown Artist").toLowerCase();
      if (!this.layerSelectedArtists.has(artKey)) return;
      const albName = t.album || "Singles";
      const albKey = albName.toLowerCase();
      const entry = albumMap.get(albKey) || {
        name: albName,
        key: albKey,
        artist: t.album_artist || t.artist_display,
        count: 0,
      };
      entry.count++;
      albumMap.set(albKey, entry);
    });
    const candidateAlbums = Array.from(albumMap.values()).sort((a, b) => a.name.localeCompare(b.name));

    // Render Layer 2: Albums (Optional Filter — Default: ALL UNSELECTED)
    if (this.layerSelectedArtists.size === 0) {
      albumsBox.innerHTML = `<div class="text-xs text-zinc-500 p-3 italic">Select 1 or more artists in Layer 1 first.</div>`;
    } else {
      albumsBox.innerHTML = candidateAlbums
        .map((alb) => {
          const checked = this.layerSelectedAlbums.has(alb.key);
          return `
            <label class="flex items-center justify-between p-2 rounded-xl border text-xs cursor-pointer transition ${
              checked
                ? "bg-emerald-500/15 border-emerald-500/50 text-emerald-200 font-semibold"
                : "bg-zinc-950/60 border-zinc-800/80 text-zinc-400 hover:border-zinc-700"
            }">
              <div class="flex items-center gap-2 truncate">
                <input type="checkbox" data-layer-album="${this.escapeAttr(alb.key)}" ${checked ? "checked" : ""} class="accent-emerald-500" />
                <div class="truncate">
                  <div class="truncate">${this.escape(alb.name)}</div>
                  <div class="text-[10px] text-zinc-500 truncate">${this.escape(alb.artist)}</div>
                </div>
              </div>
              <span class="text-[10px] font-mono opacity-75">${alb.count}</span>
            </label>
          `;
        })
        .join("");
    }

    // Render Layer 3: Tracks (Default: 100% SELECTED for all matching tracks)
    const candidateTracks = this.getLayerCandidateTracks();
    const selectedTracks = candidateTracks.filter((t) => !this.layerExcludedTrackIds.has(t.id));

    if (!candidateTracks.length) {
      tracksBox.innerHTML = `<div class="text-xs text-zinc-500 p-3 italic">Pick artists in Layer 1 to populate tracks (all matching tracks start 100% selected).</div>`;
    } else {
      tracksBox.innerHTML = candidateTracks
        .map((t) => {
          const checked = !this.layerExcludedTrackIds.has(t.id);
          const numBadge = t.track_number > 0 ? `#${String(t.track_number).padStart(2, "0")} ` : "";
          return `
            <label class="flex items-center justify-between p-2 rounded-xl border text-xs cursor-pointer transition ${
              checked
                ? "bg-emerald-950/40 border-emerald-500/40 text-zinc-100"
                : "bg-zinc-950/40 border-zinc-800/60 text-zinc-500"
            }">
              <div class="flex items-center gap-2 min-w-0">
                <input type="checkbox" data-layer-track="${t.id}" ${checked ? "checked" : ""} class="accent-emerald-500" />
                <div class="truncate">
                  <span class="font-mono text-emerald-400">${numBadge}</span>
                  <span class="font-medium">${this.escape(t.title)}</span>
                  <span class="text-zinc-500"> • ${this.escape(t.album)}</span>
                </div>
              </div>
            </label>
          `;
        })
        .join("");
    }

    if (summaryEl) {
      summaryEl.textContent = `${this.layerSelectedArtists.size} artist(s) • ${
        this.layerSelectedAlbums.size === 0 ? "All albums" : `${this.layerSelectedAlbums.size} album(s)`
      } • ${selectedTracks.length}/${candidateTracks.length} track(s) selected`;
    }

    // Bind Layer 1, 2, 3 checkbox events
    artistsBox.querySelectorAll("[data-layer-artist]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const key = cb.dataset.layerArtist;
        if (cb.checked) {
          this.layerSelectedArtists.add(key);
        } else {
          this.layerSelectedArtists.delete(key);
        }
        // Keep Layer 3 tracks 100% selected when changing artist filters
        this.layerExcludedTrackIds.clear();
        this.renderLayeredSelector();
      });
    });

    albumsBox.querySelectorAll("[data-layer-album]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const key = cb.dataset.layerAlbum;
        if (cb.checked) {
          this.layerSelectedAlbums.add(key);
        } else {
          this.layerSelectedAlbums.delete(key);
        }
        this.layerExcludedTrackIds.clear();
        this.renderLayeredSelector();
      });
    });

    tracksBox.querySelectorAll("[data-layer-track]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = Number(cb.dataset.layerTrack);
        if (cb.checked) {
          this.layerExcludedTrackIds.delete(id);
        } else {
          this.layerExcludedTrackIds.add(id);
        }
        this.renderLayeredSelector();
      });
    });
  }

  playLayeredSelectionQueue() {
    const selected = this.getLayerSelectedTracks();
    if (!selected.length) {
      alert("Select at least one artist in Layer 1 first.");
      return;
    }
    this.playQueue = selected;
    this.playByIndex(0);
  }

  async cacheLayeredSelectionOffline() {
    const selected = this.getLayerSelectedTracks();
    if (!selected.length) {
      alert("Select at least one artist in Layer 1 first.");
      return;
    }
    const btn = document.getElementById("btn-layer-cache-offline");
    if (btn) {
      btn.disabled = true;
      btn.textContent = `Caching 0/${selected.length}...`;
    }
    try {
      await offlineStore.saveBatchTracksForOffline(selected, api, (done, total) => {
        if (btn) btn.textContent = `Caching ${done}/${total}...`;
      });
      await this.refreshOfflineState();
      this.renderTrackList();
      if (btn) btn.textContent = `✓ Cached ${selected.length} Tracks`;
    } catch (err) {
      alert(`Offline cache error: ${err.message}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ---- Full Album Downloader (Preview & Batch Download) ----

  async previewAlbumForDownload() {
    const artist = document.getElementById("dl-album-artist")?.value.trim() || "";
    const album = document.getElementById("dl-album-title")?.value.trim() || "";
    const box = document.getElementById("dl-album-preview-list");
    if (!album) {
      alert("Enter an Album name or YouTube Album/Playlist URL.");
      return;
    }
    if (box) box.innerHTML = `<div class="text-xs text-zinc-400 p-2">Looking up official album tracklist...</div>`;

    try {
      const qs = new URLSearchParams({ artist, album });
      const res = await api.request(`/api/albums/lookup?${qs.toString()}`);
      this.previewedAlbumTracks = res.tracks || [];
      if (!this.previewedAlbumTracks.length) {
        if (box) box.innerHTML = `<div class="text-xs text-amber-400 p-2">No tracks found. Try entering both Artist and Album name.</div>`;
        return;
      }
      if (box) {
        box.innerHTML = this.previewedAlbumTracks
          .map(
            (tr, idx) => `
            <label class="flex items-center justify-between p-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs">
              <div class="flex items-center gap-2 truncate">
                <input type="checkbox" data-dl-album-track="${idx}" checked class="accent-emerald-500" />
                <span class="font-mono text-emerald-400">#${String(tr.track_number || idx + 1).padStart(2, "0")}</span>
                <span class="truncate">${this.escape(tr.title)}</span>
              </div>
            </label>
          `
          )
          .join("");
      }
    } catch (err) {
      if (box) box.innerHTML = `<div class="text-xs text-rose-400 p-2">Lookup failed: ${this.escape(err.message)}</div>`;
    }
  }

  async startAlbumDownloadJob() {
    const artist = document.getElementById("dl-album-artist")?.value.trim() || "Unknown Artist";
    const album = document.getElementById("dl-album-title")?.value.trim() || "";
    const bitrate = Number(document.getElementById("ext-bitrate")?.value || 320);
    if (!album) {
      alert("Please enter an Album Title or Playlist URL to download.");
      return;
    }

    let selectedTracks = [];
    const checkboxes = document.querySelectorAll("[data-dl-album-track]:checked");
    if (checkboxes.length && this.previewedAlbumTracks.length) {
      selectedTracks = Array.from(checkboxes).map(
        (cb) => this.previewedAlbumTracks[Number(cb.dataset.dlAlbumTrack)]
      );
    }

    const logBox = document.getElementById("job-logs");
    const banner = document.getElementById("live-job-banner");
    if (logBox) logBox.textContent = `Starting full album download for '${artist} — ${album}'...`;
    if (banner) banner.classList.remove("hidden");

    try {
      const job = await api.request("/api/jobs/download-album", {
        method: "POST",
        body: JSON.stringify({
          artist,
          album,
          bitrate_kbps: bitrate,
          tracks: selectedTracks,
          skip_existing: true,
        }),
      });
      this.subscribeToJobProgress(job.job_id);
    } catch (err) {
      alert(`Album download failed to start: ${err.message}`);
    }
  }

  subscribeToJobProgress(jobId) {
    const logBox = document.getElementById("job-logs");
    api.subscribeJobEvents(jobId, (state) => {
      if (logBox) {
        logBox.textContent = (state.logs || []).join("\n");
        logBox.scrollTop = logBox.scrollHeight;
      }
      const pct = `${state.progress_pct}%`;
      const bar = document.getElementById("job-progress-bar");
      const bannerBar = document.getElementById("banner-progress-bar");
      const bannerStep = document.getElementById("banner-step-text");
      const bannerLyric = document.getElementById("banner-lyric-status");

      if (bar) bar.style.width = pct;
      if (bannerBar) bannerBar.style.width = pct;
      if (bannerStep) {
        bannerStep.textContent = `${state.current_step} (${state.progress_pct}%)`;
      }
      if (bannerLyric) {
        bannerLyric.textContent = `Lyrics: ${state.lyric_sync_status || "Checking..."}`;
      }

      if (state.status === "completed") {
        this.loadAlbums();
        this.loadLibrary(document.getElementById("search-input")?.value || "");
      }
    });
  }

  // ---- Internal Track & Bulk Album Renaming ----

  openEditTrackModal(track) {
    const modal = document.getElementById("edit-track-modal");
    if (!modal) return;
    document.getElementById("edit-track-id").value = String(track.id);
    document.getElementById("edit-track-title").value = track.title || "";
    document.getElementById("edit-track-artist").value = track.artist_display || "";
    document.getElementById("edit-track-album").value = track.album || "";
    document.getElementById("edit-track-number").value = String(track.track_number || 0);
    document.getElementById("edit-track-year").value = track.original_year || track.release_year || "";
    modal.classList.remove("hidden");
  }

  async saveEditedTrackMetadata() {
    const trackId = Number(document.getElementById("edit-track-id")?.value || 0);
    const title = document.getElementById("edit-track-title")?.value.trim();
    const artist = document.getElementById("edit-track-artist")?.value.trim();
    const album = document.getElementById("edit-track-album")?.value.trim();
    const trackNumber = Number(document.getElementById("edit-track-number")?.value || 0);
    const year = document.getElementById("edit-track-year")?.value.trim();
    const rememberAlias = document.getElementById("edit-remember-alias")?.checked ?? true;

    if (!trackId || !title || !artist || !album) {
      alert("Title, Artist, and Album are required.");
      return;
    }

    try {
      await api.request(`/api/tracks/${trackId}/metadata`, {
        method: "PUT",
        body: JSON.stringify({
          title,
          artist_display: artist,
          album,
          track_number: trackNumber,
          release_year: year,
          original_year: year,
          remember_album_alias: rememberAlias,
        }),
      });
      document.getElementById("edit-track-modal")?.classList.add("hidden");
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "");
    } catch (err) {
      alert(`Failed saving metadata: ${err.message}`);
    }
  }

  openBulkAlbumRenameModal() {
    if (!this.selectedAlbum) return;
    const modal = document.getElementById("rename-album-modal");
    if (!modal) return;
    document.getElementById("bulk-old-album").value = this.selectedAlbum;
    document.getElementById("bulk-new-album").value = this.selectedAlbum;
    modal.classList.remove("hidden");
  }

  async submitBulkAlbumRename() {
    const oldAlbum = document.getElementById("bulk-old-album")?.value.trim();
    const newAlbum = document.getElementById("bulk-new-album")?.value.trim();
    if (!oldAlbum || !newAlbum) return;

    try {
      await api.request("/api/albums/rename", {
        method: "POST",
        body: JSON.stringify({
          old_album: oldAlbum,
          new_album: newAlbum,
          album_artist: "",
          remember_alias: true,
        }),
      });
      document.getElementById("rename-album-modal")?.classList.add("hidden");
      this.selectedAlbum = newAlbum;
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "");
    } catch (err) {
      alert(`Album rename failed: ${err.message}`);
    }
  }

  // ---- Playback & Isolated Synced/Rough Lyrics ----

  async toggleOfflineTrack(track, btnEl) {
    if (!track) return;
    btnEl.disabled = true;
    btnEl.textContent = "...";
    try {
      if (this.offlineIds.has(track.id)) {
        await offlineStore.removeOfflineTrack(track.id);
      } else {
        await offlineStore.saveTrackForOffline(track, api);
      }
      await this.refreshOfflineState();
      this.renderTrackList();
    } catch (err) {
      alert(`Offline cache error: ${err.message}`);
      this.renderTrackList();
    }
  }

  async playByIndex(index) {
    if (!this.playQueue.length) {
      this.playQueue = [...this.tracks];
    }
    if (index < 0 || index >= this.playQueue.length) return;
    this.currentIndex = index;
    this.lastActiveLyricIdx = -1;
    this.lyricOffsetSec = 0.0;
    this.updateLyricOffsetLabel();

    const track = this.playQueue[index];
    this.renderTrackList();
    this.updateNowPlayingBar(track);
    await this.engine.loadAndPlay(track, api);
    this.loadLyricsForTrack(track);
  }

  togglePlay() {
    if (this.currentIndex === -1 && this.tracks.length) {
      this.playQueue = [...this.tracks];
      this.playByIndex(0);
      return;
    }
    this.engine.togglePlay();
  }

  prevTrack() {
    if (!this.playQueue.length) return;
    const nextIdx = (this.currentIndex - 1 + this.playQueue.length) % this.playQueue.length;
    this.playByIndex(nextIdx);
  }

  async nextTrack() {
    if (!this.playQueue.length) return;
    const current = this.playQueue[this.currentIndex];
    if (current) {
      try {
        const res = await api.request(`/api/tracks/${current.id}/next-transition`);
        if (res.transition?.next_track) {
          const foundIdx = this.playQueue.findIndex((t) => t.id === res.transition.next_track.id);
          if (foundIdx !== -1) {
            await this.playByIndex(foundIdx);
            return;
          }
        }
      } catch (_err) {}
    }

    if (this.shuffleEnabled && this.playQueue.length > 1) {
      let randIdx = this.currentIndex;
      while (randIdx === this.currentIndex) {
        randIdx = Math.floor(Math.random() * this.playQueue.length);
      }
      await this.playByIndex(randIdx);
      return;
    }

    const nextIdx = (this.currentIndex + 1) % this.playQueue.length;
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
    } catch (_err) {}
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
    if (box) box.innerHTML = `<p class="text-zinc-500 text-sm py-8">Loading synced or rough-interpolated lyrics...</p>`;
    try {
      this.lyricsData = await api.request(track.lyrics_url);
    } catch (_err) {
      const offlineRec = await offlineStore.getOfflineTrackRecord(track.id);
      this.lyricsData = offlineRec?.offline_lyrics || { synced: false, sync_mode: "none", lines: [] };
    }
    this.lastActiveLyricIdx = -1;
    this.renderLyrics();
  }

  renderLyrics() {
    const box = document.getElementById("lyrics-container");
    const badge = document.getElementById("lyric-sync-badge");
    const roughPos = document.getElementById("lyric-rough-pos");
    if (!box) return;

    const mode = this.lyricsData.sync_mode || "none";
    if (badge) {
      if (mode === "exact_lrc") {
        badge.className = "px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40";
        badge.textContent = "✓ Exact LRC Sync (LRCLIB)";
      } else if (mode === "youtube_captions") {
        badge.className = "px-2 py-0.5 rounded-full text-[11px] font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40";
        badge.textContent = "≈ Rough Sync (YouTube Stream Captions)";
      } else if (mode === "rough_interpolated") {
        badge.className = "px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40";
        badge.textContent = "≈ Rough Sync (Estimated Position)";
      } else {
        badge.className = "px-2 py-0.5 rounded-full text-[11px] font-medium bg-zinc-800 text-zinc-400";
        badge.textContent = "No Synced Lyrics";
      }
    }

    if (!this.lyricsData.lines?.length) {
      box.innerHTML = `<p class="text-zinc-500 text-sm italic py-10">No lyrics found for this track.</p>`;
      if (roughPos) roughPos.textContent = "—";
      return;
    }

    box.innerHTML = this.lyricsData.lines
      .map((l, idx) => {
        const tsLabel = l.time !== null && l.time !== undefined ? this.formatTime(l.time) : "";
        return `
          <div
            id="lyric-line-${idx}"
            data-lyric-time="${l.time ?? 0}"
            class="lyric-line text-zinc-400 flex items-center justify-between gap-3">
            <span>${this.escape(l.text)}</span>
            <span class="text-[10px] font-mono text-zinc-600 shrink-0">${tsLabel}</span>
          </div>
        `;
      })
      .join("");

    box.querySelectorAll("[data-lyric-time]").forEach((el) => {
      el.addEventListener("click", () => {
        const t = Number(el.dataset.lyricTime || 0);
        this.engine.seekToSeconds(Math.max(0, t - this.lyricOffsetSec));
      });
    });
  }

  handleTimeUpdate(cur, dur) {
    const seekEl = document.getElementById("seek-bar");
    const timeEl = document.getElementById("time-display");
    if (seekEl && dur > 0) {
      seekEl.value = String(Math.round((cur / dur) * 1000));
      this.updateSliderFill(seekEl);
    }
    if (timeEl) {
      timeEl.textContent = `${this.formatTime(cur)} / ${this.formatTime(dur)}`;
    }

    if (!this.lyricsData.synced || !this.lyricsData.lines?.length) return;

    const adjustedTime = cur + this.lyricOffsetSec;
    const totalLines = this.lyricsData.lines.length;
    let activeIdx = 0;
    for (let i = 0; i < totalLines; i++) {
      if (adjustedTime >= this.lyricsData.lines[i].time) {
        activeIdx = i;
      } else {
        break;
      }
    }

    const roughPct = Math.round(((activeIdx + 1) / totalLines) * 100);
    const roughPosEl = document.getElementById("lyric-rough-pos");
    const roughBarEl = document.getElementById("lyric-progress-fill");
    if (roughPosEl) {
      roughPosEl.textContent = `Line ${activeIdx + 1}/${totalLines} (~${roughPct}%)`;
    }
    if (roughBarEl) {
      roughBarEl.style.width = `${roughPct}%`;
    }

    if (activeIdx === this.lastActiveLyricIdx) return;

    const prevEl = document.getElementById(`lyric-line-${this.lastActiveLyricIdx}`);
    if (prevEl) {
      prevEl.classList.remove("lyric-line-active");
      prevEl.classList.add("text-zinc-400");
    }

    const activeEl = document.getElementById(`lyric-line-${activeIdx}`);
    const box = document.getElementById("lyrics-container");
    if (activeEl) {
      activeEl.classList.remove("text-zinc-400");
      activeEl.classList.add("lyric-line-active");
    }

    this.lastActiveLyricIdx = activeIdx;

    if (box && activeEl && Date.now() > this.userScrollLockUntil) {
      const targetScrollTop =
        activeEl.offsetTop - box.clientHeight / 2 + activeEl.clientHeight / 2;
      box.scrollTo({
        top: Math.max(0, targetScrollTop),
        behavior: "smooth",
      });
    }
  }

  adjustLyricOffset(deltaSec) {
    this.lyricOffsetSec = Math.round((this.lyricOffsetSec + deltaSec) * 10) / 10;
    this.updateLyricOffsetLabel();
    this.lastActiveLyricIdx = -1;
  }

  updateLyricOffsetLabel() {
    const el = document.getElementById("lyric-offset-label");
    if (el) {
      const sign = this.lyricOffsetSec >= 0 ? "+" : "";
      el.textContent = `${sign}${this.lyricOffsetSec.toFixed(1)}s`;
    }
  }

  updateNowPlayingBar(track) {
    const titleEl = document.getElementById("np-title");
    const artistEl = document.getElementById("np-artist");
    const coverEl = document.getElementById("np-cover");
    const carTitle = document.getElementById("car-title");
    const carArtist = document.getElementById("car-artist");
    const numPrefix = track.track_number > 0 ? `#${String(track.track_number).padStart(2, "0")} ` : "";
    if (titleEl) titleEl.textContent = `${numPrefix}${track.title}`;
    if (artistEl) artistEl.textContent = `${track.artist_display} • ${track.album}`;
    if (coverEl) {
      coverEl.src = track.cover_url ? api.resolveUrl(track.cover_url) : "/icon.svg";
    }
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

  // ---- Studio Right-Column Tabs ----

  switchStudioTab(tabName) {
    const tabs = ["lyrics", "extractor", "curator", "settings"];
    tabs.forEach((name) => {
      const panel = document.getElementById(`tab-panel-${name}`);
      const btn = document.getElementById(`tab-btn-${name}`);
      const active = name === tabName;
      if (panel) panel.classList.toggle("hidden", !active);
      if (btn) {
        btn.className = active
          ? "px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-zinc-950 shadow-sm transition"
          : "px-3 py-1.5 rounded-xl text-xs font-medium text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/70 transition";
      }
    });
  }

  // ---- Extraction Job ----

  async startExtractionJob() {
    const username = document.getElementById("ext-username")?.value.trim();
    const period = document.getElementById("ext-period")?.value || "12month";
    const minPlays = Number(document.getElementById("ext-min-plays")?.value || 10);
    const cap = Number(document.getElementById("ext-cap")?.value || 25);
    const bitrate = Number(document.getElementById("ext-bitrate")?.value || 320);
    const useLlmFilter = document.getElementById("ext-use-llm")?.checked ?? false;

    if (!username) {
      alert("Please enter a Last.fm username.");
      return;
    }

    const logBox = document.getElementById("job-logs");
    const banner = document.getElementById("live-job-banner");
    if (logBox) logBox.textContent = "Starting extraction job...";
    if (banner) banner.classList.remove("hidden");

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
          use_llm_filter: useLlmFilter,
        }),
      });
      this.subscribeToJobProgress(job.job_id);
    } catch (err) {
      alert(`Failed starting job: ${err.message}`);
    }
  }

  // ---- LLM Smart Curator & Deduplicator ----

  async runLLMLibraryAudit() {
    const statusEl = document.getElementById("curator-status");
    const listEl = document.getElementById("curator-actions-list");
    const applyBtn = document.getElementById("btn-apply-curation");
    if (statusEl) statusEl.textContent = "Analyzing library via LLM API / Proxy & fuzzy deduplicator...";
    if (listEl) listEl.innerHTML = "";

    try {
      await this.saveSettings(true);
      const res = await api.request("/api/curation/analyze", { method: "POST" });
      this.proposedCurationActions = res.actions || [];
      const engineLabel = res.used_llm ? `LLM Proxy (${res.llm_endpoint})` : "Built-in Fuzzy Deduplicator";
      if (statusEl) {
        statusEl.textContent = `Audit complete via ${engineLabel}: ${this.proposedCurationActions.length} suggestion(s) found.`;
      }
      if (applyBtn) {
        applyBtn.classList.toggle("hidden", !this.proposedCurationActions.length);
      }
      this.renderCurationActions();
    } catch (err) {
      if (statusEl) statusEl.textContent = `Audit error: ${err.message}`;
    }
  }

  renderCurationActions() {
    const listEl = document.getElementById("curator-actions-list");
    if (!listEl) return;
    if (!this.proposedCurationActions.length) {
      listEl.innerHTML = `<div class="p-4 text-xs text-zinc-400 text-center">✓ No duplicate tracks or misnamed albums detected!</div>`;
      return;
    }

    listEl.innerHTML = this.proposedCurationActions
      .map((act, idx) => {
        const isDup = act.action_type === "duplicate_remove";
        return `
          <label class="flex items-start gap-2.5 p-2.5 rounded-xl bg-zinc-950/80 border border-zinc-800 text-xs cursor-pointer hover:border-zinc-700">
            <input type="checkbox" data-curate-idx="${idx}" checked class="mt-0.5 accent-emerald-500" />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 font-semibold ${isDup ? "text-rose-400" : "text-emerald-400"}">
                <span>${isDup ? "🗑 Remove Duplicate Version" : "✨ Rename Track / Canonicalize Album"}</span>
              </div>
              <div class="text-zinc-300 truncate mt-0.5">
                <b>Before:</b> ${this.escape(act.current_artist)} — ${this.escape(act.current_title)} <span class="text-zinc-500">[${this.escape(act.current_album)}]</span>
              </div>
              ${
                !isDup
                  ? `<div class="text-emerald-300 truncate">
                      <b>After:</b> ${this.escape(act.proposed_artist)} — ${this.escape(act.proposed_title)} <span class="text-emerald-400">[${this.escape(act.proposed_album)}]</span>
                    </div>`
                  : ""
              }
              <div class="text-[11px] text-zinc-500 mt-0.5">${this.escape(act.reason || "")}</div>
            </div>
          </label>
        `;
      })
      .join("");
  }

  async applySelectedCurationActions() {
    const checkboxes = document.querySelectorAll("[data-curate-idx]:checked");
    const selected = Array.from(checkboxes).map(
      (cb) => this.proposedCurationActions[Number(cb.dataset.curateIdx)]
    );
    if (!selected.length) return;

    try {
      const res = await api.request("/api/curation/apply", {
        method: "POST",
        body: JSON.stringify({ actions: selected }),
      });
      alert(
        `Applied fixes: ${res.renamed} track(s)/album(s) renamed, ${res.removed_duplicates} duplicate(s) removed.`
      );
      this.proposedCurationActions = [];
      this.renderCurationActions();
      document.getElementById("btn-apply-curation")?.classList.add("hidden");
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "");
    } catch (err) {
      alert(`Failed applying curation actions: ${err.message}`);
    }
  }

  // ---- Settings & APK / Phone LAN Sync ----

  async loadSettingsIntoUI() {
    const originInput = document.getElementById("apk-server-url-input");
    if (originInput) {
      originInput.value = api.getServerOrigin();
    }
    try {
      const cfg = await api.request("/api/settings");
      const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el && v !== undefined && v !== null) el.value = String(v);
      };
      setVal("set-api-key", cfg.lastfm_api_key || "");
      setVal("set-username", cfg.lastfm_username || "");
      setVal("ext-username", cfg.lastfm_username || "");
      setVal("llm-base-url", cfg.llm_base_url || "https://api.openai.com/v1");
      setVal("llm-model", cfg.llm_model || "gpt-4o-mini");
    } catch (_err) {}
  }

  async saveSettings(silent = false) {
    const apiKey = document.getElementById("set-api-key")?.value.trim();
    const apiSecret = document.getElementById("set-api-secret")?.value.trim();
    const username = document.getElementById("set-username")?.value.trim();
    const llmBaseUrl = document.getElementById("llm-base-url")?.value.trim();
    const llmApiKey = document.getElementById("llm-api-key")?.value.trim();
    const llmModel = document.getElementById("llm-model")?.value.trim();

    await api.request("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        lastfm_api_key: apiKey || undefined,
        lastfm_api_secret: apiSecret || undefined,
        lastfm_username: username || undefined,
        llm_base_url: llmBaseUrl || undefined,
        llm_api_key: llmApiKey || undefined,
        llm_model: llmModel || undefined,
      }),
    });
    if (!silent) {
      alert("Settings saved to SQLite WAL database.");
    }
  }

  async openSyncModal() {
    const modal = document.getElementById("sync-modal");
    const urlBox = document.getElementById("sync-pairing-url");
    const originInput = document.getElementById("apk-server-url-input");
    if (!modal) return;
    modal.classList.remove("hidden");
    if (originInput) {
      originInput.value = api.getServerOrigin();
    }
    try {
      const info = await api.request("/api/sync/info");
      if (urlBox) urlBox.textContent = info.pairing_url;
    } catch (_err) {
      if (urlBox) {
        urlBox.textContent = "Offline / APK Standalone Mode — Paste your PC's Pairing URL below to sync.";
      }
    }
  }

  async saveApkServerConnection() {
    const rawUrl = document.getElementById("apk-server-url-input")?.value.trim() || "";
    api.setServerOrigin(rawUrl);
    document.getElementById("sync-modal")?.classList.add("hidden");
    await this.loadAlbums();
    await this.loadLibrary();
    offlineStore.flushOfflineScrobbles(api).catch(() => {});
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
    // Player controls & Randomizer
    document.getElementById("btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-shuffle")?.addEventListener("click", () => this.toggleShuffle());
    document.getElementById("btn-randomize-now")?.addEventListener("click", () => this.randomizeAndPlayCurrentView());
    document.getElementById("car-btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("car-btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("car-btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-car-mode")?.addEventListener("click", () => this.toggleCarMode());
    document.getElementById("btn-exit-car")?.addEventListener("click", () => this.toggleCarMode());

    // Sort order selector (Track #, Alphabetical, Plays, Recent)
    document.getElementById("sort-select")?.addEventListener("change", (e) => {
      this.sortBy = e.target.value || "track_num";
      this.loadLibrary(document.getElementById("search-input")?.value || "");
    });

    // View mode switcher: Standard Library vs 3-Layer Selector
    document.getElementById("btn-view-standard")?.addEventListener("click", () => this.switchLibraryViewMode("standard"));
    document.getElementById("btn-view-layered")?.addEventListener("click", () => this.switchLibraryViewMode("layered"));

    // 3-Layer Selector action buttons
    document.getElementById("btn-layer-play")?.addEventListener("click", () => this.playLayeredSelectionQueue());
    document.getElementById("btn-layer-shuffle")?.addEventListener("click", () => this.randomizeAndPlayCurrentView());
    document.getElementById("btn-layer-cache-offline")?.addEventListener("click", () => this.cacheLayeredSelectionOffline());
    document.getElementById("btn-layer-clear-artists")?.addEventListener("click", () => {
      this.layerSelectedArtists.clear();
      this.layerSelectedAlbums.clear();
      this.layerExcludedTrackIds.clear();
      this.renderLayeredSelector();
    });

    // Play by Album & Offline Album Cache buttons
    document.getElementById("btn-play-selected-album")?.addEventListener("click", () => this.playSelectedAlbumInOrder());
    document.getElementById("btn-cache-selected-album")?.addEventListener("click", () => this.cacheSelectedAlbumOffline());

    // Styled Seek & Volume Sliders
    document.getElementById("seek-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.seekToFraction(Number(e.target.value) / 1000.0);
    });
    document.getElementById("volume-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.setBaseVolume(Number(e.target.value) / 100.0);
    });

    // Pause Lyric Auto-Scroll on manual interaction
    const lyricsBox = document.getElementById("lyrics-container");
    if (lyricsBox) {
      const lockScroll = () => {
        this.userScrollLockUntil = Date.now() + 4500;
      };
      lyricsBox.addEventListener("wheel", lockScroll, { passive: true });
      lyricsBox.addEventListener("touchmove", lockScroll, { passive: true });
      lyricsBox.addEventListener("mousedown", lockScroll);
    }

    document.getElementById("btn-lyric-earlier")?.addEventListener("click", () => this.adjustLyricOffset(-0.5));
    document.getElementById("btn-lyric-later")?.addEventListener("click", () => this.adjustLyricOffset(0.5));

    // Studio Right-Hand Navigation Tabs
    ["lyrics", "extractor", "curator", "settings"].forEach((tab) => {
      document.getElementById(`tab-btn-${tab}`)?.addEventListener("click", () => this.switchStudioTab(tab));
    });

    // Library Search & Album Bulk Rename
    document.getElementById("search-input")?.addEventListener("input", (e) => {
      this.loadLibrary(e.target.value);
    });
    document.getElementById("btn-rename-selected-album")?.addEventListener("click", () => this.openBulkAlbumRenameModal());
    document.getElementById("btn-confirm-album-rename")?.addEventListener("click", () => this.submitBulkAlbumRename());
    document.getElementById("btn-save-track-edit")?.addEventListener("click", () => this.saveEditedTrackMetadata());

    // Jobs, Full Album Downloader, LLM Curation & APK Pairing
    document.getElementById("btn-start-extract")?.addEventListener("click", () => this.startExtractionJob());
    document.getElementById("btn-preview-album")?.addEventListener("click", () => this.previewAlbumForDownload());
    document.getElementById("btn-download-album")?.addEventListener("click", () => this.startAlbumDownloadJob());
    document.getElementById("btn-run-curator")?.addEventListener("click", () => this.runLLMLibraryAudit());
    document.getElementById("btn-apply-curation")?.addEventListener("click", () => this.applySelectedCurationActions());
    document.getElementById("btn-show-sync")?.addEventListener("click", () => this.openSyncModal());
    document.getElementById("btn-save-apk-server")?.addEventListener("click", () => this.saveApkServerConnection());
    document.getElementById("btn-save-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-llm-settings")?.addEventListener("click", () => this.saveSettings(false));
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

  escapeAttr(str) {
    return String(str || "").replace(/"/g, "&quot;");
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
  <title>MusicLinker Studio & APK v3.0 — Layered Player, Album Downloader & AI Curator</title>
  <link rel="manifest" href="/manifest.json" />
  <link rel="icon" href="/icon.svg" type="image/svg+xml" />
  <link rel="stylesheet" href="/css/styles.css" />
  <meta name="theme-color" content="#10b981" />
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-zinc-950 text-zinc-100 h-screen flex flex-col overflow-hidden select-none">
  <!-- Top Studio Header -->
  <header class="border-b border-zinc-800/80 bg-zinc-900/70 backdrop-blur-md px-4 sm:px-5 py-2.5 flex flex-wrap items-center justify-between gap-3 shrink-0">
    <div class="flex items-center gap-3">
      <img src="/icon.svg" alt="MusicLinker" class="w-8 h-8 shadow-md rounded-lg" />
      <div>
        <div class="flex items-center gap-2">
          <h1 class="font-bold text-base sm:text-lg tracking-tight leading-none">MusicLinker</h1>
          <span class="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">v3.0 APK + Desktop</span>
        </div>
        <p class="text-[11px] text-zinc-400 hidden sm:block">3-Layer Selector • Play by Album • Track # / Alpha Order • Album Downloader</p>
      </div>
    </div>

    <div class="flex items-center gap-2">
      <input
        id="search-input"
        type="search"
        placeholder="Filter songs, artists, albums..."
        class="bg-zinc-900/90 border border-zinc-700/80 rounded-xl px-3 py-1.5 text-xs focus:outline-none focus:border-emerald-500 w-36 sm:w-56 transition"
      />
      <button id="btn-randomize-now" class="px-2.5 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium border border-zinc-700/60" title="Randomize & Play Queue">
        🎲 Randomize
      </button>
      <button id="btn-car-mode" class="px-2.5 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium border border-zinc-700/60 hidden sm:inline-block">
        🚗 Car
      </button>
      <button id="btn-show-sync" class="px-3 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-xs shadow-sm transition">
        📱 APK / Sync
      </button>
    </div>
  </header>

  <!-- Main Fixed-Height Split Workspace -->
  <main class="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-4 p-4 min-h-0 overflow-hidden">
    <!-- Left Column: Standard Library OR 3-Layer Hierarchical Selector (7 cols) -->
    <section class="lg:col-span-7 bg-zinc-900/45 border border-zinc-800/80 rounded-2xl p-4 flex flex-col min-h-0 overflow-hidden">
      <!-- Library Toolbar: View Mode Toggle + Sort Selector -->
      <div class="flex flex-wrap items-center justify-between gap-2 mb-2.5 shrink-0">
        <div class="flex items-center gap-1.5 bg-zinc-950/80 border border-zinc-800 p-1 rounded-xl">
          <button id="btn-view-standard" class="px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-500 text-zinc-950">
            🎵 Standard Library
          </button>
          <button id="btn-view-layered" class="px-3 py-1 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200">
            🗂 3-Layer Selector
          </button>
        </div>

        <div class="flex items-center gap-2">
          <select id="sort-select" class="bg-zinc-950 border border-zinc-800 rounded-xl px-2.5 py-1 text-xs text-zinc-300">
            <option value="track_num">Order: Album & Track # (1..N)</option>
            <option value="alpha_asc">Order: Alphabetical (A → Z)</option>
            <option value="alpha_desc">Order: Alphabetical (Z → A)</option>
            <option value="plays_desc">Order: Most Played</option>
            <option value="recent">Order: Recently Added</option>
          </select>
          <span id="library-count-badge" class="text-[11px] font-mono px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-300">0 tracks</span>
        </div>
      </div>

      <!-- View 1: Standard Library with Play by Album Controls -->
      <div id="standard-library-view" class="flex-1 flex flex-col min-h-0 overflow-hidden">
        <div id="album-filter-bar" class="flex items-center gap-1.5 overflow-x-auto ml-scroll pb-2 mb-2 border-b border-zinc-800/70 shrink-0"></div>

        <!-- Contextual Action Bar when an Album is selected -->
        <div id="selected-album-actions" class="hidden flex flex-wrap items-center justify-between gap-2 bg-emerald-950/30 border border-emerald-500/30 rounded-xl p-2 mb-2 shrink-0">
          <div class="flex items-center gap-2">
            <button id="btn-play-selected-album" class="px-3 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs">
              ▶ Play Album in Track # Order
            </button>
            <button id="btn-cache-selected-album" class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-emerald-300 border border-emerald-500/30 text-xs font-medium">
              ↓ Cache Album Offline (APK)
            </button>
          </div>
          <button id="btn-rename-selected-album" class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs">
            ✎ Rename Album
          </button>
        </div>

        <div id="track-list" class="flex-1 overflow-y-auto ml-scroll space-y-1 pr-1 min-h-0"></div>
      </div>

      <!-- View 2: 3-Layer Hierarchical Selection Mode (Artists [Unselected] -> Albums [Unselected] -> Tracks [Selected]) -->
      <div id="layered-selector-view" class="hidden flex-1 flex flex-col min-h-0 overflow-hidden gap-2.5">
        <div class="flex flex-wrap items-center justify-between gap-2 bg-zinc-950/70 border border-zinc-800 rounded-xl px-3 py-2 shrink-0">
          <div id="layer-selection-summary" class="text-xs text-emerald-400 font-mono">
            0 artist(s) • All albums • 0 track(s) selected
          </div>
          <div class="flex items-center gap-1.5">
            <button id="btn-layer-clear-artists" class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs">
              Reset
            </button>
            <button id="btn-layer-play" class="px-3 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs">
              ▶ Play Selected
            </button>
            <button id="btn-layer-shuffle" class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-emerald-300 text-xs font-semibold">
              🎲 Shuffle Selected
            </button>
            <button id="btn-layer-cache-offline" class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs">
              ↓ Offline
            </button>
          </div>
        </div>

        <div class="flex-1 grid grid-cols-1 md:grid-cols-3 gap-2.5 min-h-0 overflow-hidden">
          <!-- Layer 1: Artists (Starts 100% Unselected) -->
          <div class="bg-zinc-950/40 border border-zinc-800/80 rounded-xl p-2.5 flex flex-col min-h-0 overflow-hidden">
            <div class="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5 shrink-0">
              1. Artists <span class="text-zinc-500 font-normal">(Unselected)</span>
            </div>
            <div id="layer-1-artists" class="flex-1 overflow-y-auto ml-scroll space-y-1 pr-1 min-h-0"></div>
          </div>

          <!-- Layer 2: Albums (Optional Filter — Starts 100% Unselected) -->
          <div class="bg-zinc-950/40 border border-zinc-800/80 rounded-xl p-2.5 flex flex-col min-h-0 overflow-hidden">
            <div class="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5 shrink-0">
              2. Albums <span class="text-zinc-500 font-normal">(Optional)</span>
            </div>
            <div id="layer-2-albums" class="flex-1 overflow-y-auto ml-scroll space-y-1 pr-1 min-h-0"></div>
          </div>

          <!-- Layer 3: Tracks (Starts 100% Selected) -->
          <div class="bg-zinc-950/40 border border-zinc-800/80 rounded-xl p-2.5 flex flex-col min-h-0 overflow-hidden">
            <div class="text-[11px] font-semibold uppercase tracking-wider text-emerald-400 mb-1.5 shrink-0">
              3. Tracks <span class="text-emerald-500/80 font-normal">(100% Selected)</span>
            </div>
            <div id="layer-3-tracks" class="flex-1 overflow-y-auto ml-scroll space-y-1 pr-1 min-h-0"></div>
          </div>
        </div>
      </div>
    </section>

    <!-- Right Column: Pinned Live Download Telemetry + Tabbed Studio Workspace (5 cols) -->
    <section class="lg:col-span-5 flex flex-col gap-3 min-h-0 overflow-hidden">
      <!-- Pinned Live Download & Lyric Sync Progress Banner -->
      <div id="live-job-banner" class="hidden bg-zinc-900/90 border border-emerald-500/40 rounded-2xl p-3 space-y-1.5 shrink-0 shadow-lg">
        <div class="flex items-center justify-between text-xs">
          <span class="font-semibold text-emerald-400 flex items-center gap-1.5">
            <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Active Download & Lyric Sync
          </span>
          <span id="banner-lyric-status" class="text-[11px] text-zinc-300 font-mono truncate max-w-[220px]">Waiting...</span>
        </div>
        <div id="banner-step-text" class="text-xs text-zinc-200 truncate">Starting...</div>
        <div class="w-full bg-zinc-800 h-1.5 rounded-full overflow-hidden">
          <div id="banner-progress-bar" class="bg-emerald-500 h-full w-0 transition-all duration-300"></div>
        </div>
      </div>

      <!-- Studio Navigation Tabs -->
      <div class="flex items-center gap-1.5 bg-zinc-900/60 border border-zinc-800/80 p-1.5 rounded-2xl shrink-0">
        <button id="tab-btn-lyrics" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-zinc-950 shadow-sm transition">
          🎤 Lyrics
        </button>
        <button id="tab-btn-extractor" class="px-3 py-1.5 rounded-xl text-xs font-medium text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/70 transition">
          💿 Albums & Last.fm
        </button>
        <button id="tab-btn-curator" class="px-3 py-1.5 rounded-xl text-xs font-medium text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/70 transition">
          ✨ AI Curator
        </button>
        <button id="tab-btn-settings" class="px-3 py-1.5 rounded-xl text-xs font-medium text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/70 transition">
          ⚙ Settings
        </button>
      </div>

      <!-- Tab Panel 1: Non-Hijacking Synced & Rough Lyrics Viewport -->
      <div id="tab-panel-lyrics" class="flex-1 bg-zinc-900/45 border border-zinc-800/80 rounded-2xl p-4 flex flex-col min-h-0 overflow-hidden">
        <div class="flex flex-wrap items-center justify-between gap-2 pb-2.5 mb-2 border-b border-zinc-800/80 shrink-0">
          <div class="flex items-center gap-2">
            <span id="lyric-sync-badge" class="px-2 py-0.5 rounded-full text-[11px] font-medium bg-zinc-800 text-zinc-400">
              No Track Playing
            </span>
            <span id="lyric-rough-pos" class="text-[11px] font-mono text-zinc-400">—</span>
          </div>
          <div class="flex items-center gap-1 text-[11px]">
            <button id="btn-lyric-earlier" class="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300">-0.5s</button>
            <span id="lyric-offset-label" class="font-mono text-zinc-400 px-1">+0.0s</span>
            <button id="btn-lyric-later" class="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300">+0.5s</button>
          </div>
        </div>

        <div class="w-full bg-zinc-800/80 h-1 rounded-full overflow-hidden mb-3 shrink-0">
          <div id="lyric-progress-fill" class="bg-emerald-400 h-full w-0 transition-all duration-300"></div>
        </div>

        <div id="lyrics-container" class="flex-1 overflow-y-auto ml-scroll space-y-1 px-2 text-center min-h-0">
          <p class="text-zinc-500 text-sm py-12">Select a song to view exact LRCLIB or rough-interpolated karaoke lyrics.</p>
        </div>
      </div>

      <!-- Tab Panel 2: Full Album Downloader & Last.fm Extractor -->
      <div id="tab-panel-extractor" class="hidden flex-1 bg-zinc-900/45 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-3 min-h-0 overflow-y-auto ml-scroll">
        <!-- Card A: Full Studio Album Downloader -->
        <div class="bg-zinc-950/70 border border-zinc-800 rounded-xl p-3 space-y-2.5">
          <h2 class="font-semibold text-xs uppercase tracking-wider text-emerald-400">💿 Full Album Downloader (Track # 1..N)</h2>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <input id="dl-album-artist" type="text" placeholder="Artist (e.g. Daft Punk)" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5" />
            <input id="dl-album-title" type="text" placeholder="Album Name or Playlist URL" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5" />
          </div>
          <div class="flex gap-2">
            <button id="btn-preview-album" class="flex-1 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs font-medium">
              🔍 Preview Tracklist
            </button>
            <button id="btn-download-album" class="flex-1 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs">
              ⬇ Download Full Album
            </button>
          </div>
          <div id="dl-album-preview-list" class="max-h-28 overflow-y-auto ml-scroll space-y-1"></div>
        </div>

        <!-- Card B: Last.fm Top Tracks Extractor -->
        <div class="bg-zinc-950/70 border border-zinc-800 rounded-xl p-3 space-y-2.5">
          <h2 class="font-semibold text-xs uppercase tracking-wider text-zinc-400">📻 Last.fm Chart Extractor</h2>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <input id="ext-username" type="text" placeholder="Last.fm Username" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5" />
            <select id="ext-period" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5">
              <option value="12month">Last 12 Months</option>
              <option value="6month">Last 6 Months</option>
              <option value="3month">Last 3 Months</option>
              <option value="1month">Last 1 Month</option>
              <option value="overall">Overall All-Time</option>
            </select>
            <input id="ext-min-plays" type="number" value="10" min="1" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5" title="Min Plays" />
            <input id="ext-cap" type="number" value="25" min="1" max="500" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5" title="Download Cap" />
            <select id="ext-bitrate" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 col-span-2">
              <option value="320">320 kbps MP3 (Audiophile Quality)</option>
              <option value="192">192 kbps MP3 (Compact Mobile)</option>
            </select>
          </div>
          <label class="flex items-center gap-2 text-xs cursor-pointer">
            <input id="ext-use-llm" type="checkbox" checked class="accent-emerald-500" />
            <span>✨ AI Smart Filter: Deduplicate repeated versions & fix albums</span>
          </label>
          <button id="btn-start-extract" class="w-full py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-xs">
            Extract & Download Qualifying Tracks
          </button>
        </div>

        <div class="w-full bg-zinc-800 h-1.5 rounded-full overflow-hidden shrink-0">
          <div id="job-progress-bar" class="bg-emerald-500 h-full w-0 transition-all"></div>
        </div>
        <pre id="job-logs" class="flex-1 bg-zinc-950 border border-zinc-800 rounded-xl p-2.5 text-xs text-zinc-300 overflow-y-auto ml-scroll font-mono min-h-[100px]">Idle.</pre>
      </div>

      <!-- Tab Panel 3: AI Smart Curator & Deduplicator -->
      <div id="tab-panel-curator" class="hidden flex-1 bg-zinc-900/45 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-3 min-h-0 overflow-y-auto ml-scroll">
        <div>
          <h2 class="font-semibold text-xs uppercase tracking-wider text-emerald-400">✨ AI Library Curator & Deduplicator</h2>
          <p class="text-xs text-zinc-400 mt-0.5">
            Connect any OpenAI-compatible LLM API link or reverse proxy to detect repeated track versions, auto-rename messy titles, and canonicalize studio albums.
          </p>
        </div>

        <div class="space-y-2 bg-zinc-950/70 border border-zinc-800/90 rounded-xl p-3 text-xs">
          <div>
            <label class="block text-[10px] text-zinc-400 mb-1">LLM API Endpoint / Proxy Link</label>
            <input id="llm-base-url" type="text" placeholder="https://api.openai.com/v1 or custom proxy URL" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 font-mono text-xs" />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-[10px] text-zinc-400 mb-1">API / Proxy Key</label>
              <input id="llm-api-key" type="password" placeholder="sk-..." class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs" />
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 mb-1">Model Name</label>
              <input id="llm-model" type="text" value="gpt-4o-mini" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 font-mono text-xs" />
            </div>
          </div>
          <button id="btn-save-llm-settings" class="w-full py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 font-medium text-xs">
            Save Proxy Configuration
          </button>
        </div>

        <button id="btn-run-curator" class="w-full py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-xs">
          🔍 Scan Library for Duplicate Tracks & Wrong Albums
        </button>

        <div id="curator-status" class="text-xs text-zinc-400 font-mono">Ready to scan.</div>
        <div id="curator-actions-list" class="flex-1 overflow-y-auto ml-scroll space-y-2 min-h-[120px]"></div>

        <button id="btn-apply-curation" class="hidden w-full py-2 rounded-xl bg-emerald-400 hover:bg-emerald-300 text-zinc-950 font-bold text-xs">
          ✓ Apply Selected Fixes to Library & MP3 ID3 Tags
        </button>
      </div>

      <!-- Tab Panel 4: Last.fm Credentials -->
      <div id="tab-panel-settings" class="hidden flex-1 bg-zinc-900/45 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-3 min-h-0 overflow-y-auto ml-scroll">
        <h2 class="font-semibold text-xs uppercase tracking-wider text-zinc-400">Last.fm API Credentials</h2>
        <input id="set-api-key" type="text" placeholder="Last.fm API Key" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        <input id="set-api-secret" type="password" placeholder="Last.fm API Secret" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        <input id="set-username" type="text" placeholder="Default Last.fm Username" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        <button id="btn-save-settings" class="w-full py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-xs">
          Save Credentials
        </button>
      </div>
    </section>
  </main>

  <!-- Bottom Persistent Player Bar with Custom Styled Sliders & Randomizer Toggle -->
  <footer class="border-t border-zinc-800/90 bg-zinc-900/95 backdrop-blur-md px-5 py-3 flex flex-col sm:flex-row items-center justify-between gap-4 shrink-0">
    <div class="flex items-center gap-3 min-w-0 w-full sm:w-64">
      <img id="np-cover" src="/icon.svg" alt="Cover" class="w-11 h-11 rounded-xl object-cover bg-zinc-800 border border-zinc-700/60 shrink-0" />
      <div class="min-w-0">
        <div id="np-title" class="font-semibold text-sm truncate">Not Playing</div>
        <div id="np-artist" class="text-xs text-zinc-400 truncate">Select a track or album</div>
      </div>
    </div>

    <div class="flex flex-col items-center gap-2 flex-1 max-w-xl w-full">
      <div class="flex items-center gap-2.5">
        <button id="btn-prev" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium transition">⏮ Prev</button>
        <button id="btn-play" class="px-6 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-sm shadow-sm transition">▶ Play</button>
        <button id="btn-next" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium transition">Next ⏭</button>
        <button id="btn-shuffle" class="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-xs font-medium transition" title="Toggle Randomizer">
          🔀 Shuffle
        </button>
      </div>
      <div class="flex items-center gap-3 w-full">
        <input id="seek-bar" type="range" min="0" max="1000" value="0" class="ml-slider flex-1" />
        <span id="time-display" class="text-xs font-mono text-zinc-400 w-24 text-right">0:00 / 0:00</span>
      </div>
    </div>

    <div class="hidden md:flex items-center gap-2.5 w-44">
      <span class="text-xs text-zinc-400">🔊</span>
      <input id="volume-bar" type="range" min="0" max="100" value="85" class="ml-slider flex-1" />
    </div>
  </footer>

  <!-- Inline Track & Album Metadata Editor Modal -->
  <div id="edit-track-modal" class="hidden fixed inset-0 bg-black/75 z-50 flex items-center justify-center p-4">
    <div class="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 max-w-md w-full space-y-3 shadow-2xl">
      <h3 class="font-bold text-base">✎ Edit Track, Track # & Album Metadata</h3>
      <p class="text-xs text-zinc-400">Updates SQLite and rewrites MP3 ID3v2.4 frames (`TIT2`, `TPE1`, `TPE2`, `TALB`, `TRCK`) on disk.</p>
      <input id="edit-track-id" type="hidden" />
      <div class="space-y-2 text-xs">
        <div>
          <label class="block text-zinc-400 mb-1">Track Title</label>
          <input id="edit-track-title" type="text" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-zinc-400 mb-1">Artist(s)</label>
          <input id="edit-track-artist" type="text" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        </div>
        <div class="grid grid-cols-3 gap-2">
          <div class="col-span-2">
            <label class="block text-zinc-400 mb-1">Canonical Album Name</label>
            <input id="edit-track-album" type="text" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-zinc-400 mb-1">Track #</label>
            <input id="edit-track-number" type="number" min="0" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
          </div>
        </div>
        <div>
          <label class="block text-zinc-400 mb-1">Release Year</label>
          <input id="edit-track-year" type="text" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2 text-sm" />
        </div>
        <label class="flex items-center gap-2 pt-1 cursor-pointer">
          <input id="edit-remember-alias" type="checkbox" checked class="accent-emerald-500" />
          <span class="text-zinc-300">Auto-rename future tracks with this old album name</span>
        </label>
      </div>
      <div class="flex gap-2 pt-2">
        <button onclick="document.getElementById('edit-track-modal').classList.add('hidden')" class="flex-1 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold">Cancel</button>
        <button id="btn-save-track-edit" class="flex-1 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 text-xs font-bold">Save & Retag MP3</button>
      </div>
    </div>
  </div>

  <!-- Bulk Album Rename Modal -->
  <div id="rename-album-modal" class="hidden fixed inset-0 bg-black/75 z-50 flex items-center justify-center p-4">
    <div class="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 max-w-md w-full space-y-3.5 shadow-2xl">
      <h3 class="font-bold text-base">✎ Bulk Rename Album</h3>
      <div class="space-y-2 text-xs">
        <div>
          <label class="block text-zinc-400 mb-1">Current Album Name</label>
          <input id="bulk-old-album" type="text" readonly class="w-full bg-zinc-950/60 border border-zinc-800 rounded-xl px-3 py-2 text-sm text-zinc-400" />
        </div>
        <div>
          <label class="block text-zinc-400 mb-1">New Canonical Album Name</label>
          <input id="bulk-new-album" type="text" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl px-3 py-2 text-sm" />
        </div>
      </div>
      <div class="flex gap-2 pt-2">
        <button onclick="document.getElementById('rename-album-modal').classList.add('hidden')" class="flex-1 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold">Cancel</button>
        <button id="btn-confirm-album-rename" class="flex-1 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 text-xs font-bold">Rename Album Everywhere</button>
      </div>
    </div>
  </div>

  <!-- High-Contrast Car Driving Mode Overlay -->
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

  <!-- Android APK & Wi-Fi Server Pairing Modal -->
  <div id="sync-modal" class="hidden fixed inset-0 bg-black/75 z-40 flex items-center justify-center p-4">
    <div class="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 max-w-md w-full space-y-4">
      <h3 class="font-bold text-lg">📱 Android APK & Wi-Fi Server Pairing</h3>
      <p class="text-xs text-zinc-400">
        <b>On Desktop:</b> Copy or scan this LAN Pairing URL into your Android phone.<br/>
        <b>Inside the Android APK:</b> Paste your PC's Pairing URL below to stream and cache albums for 100% offline playback:
      </p>
      <div id="sync-pairing-url" class="p-3 rounded-xl bg-zinc-950 border border-zinc-800 font-mono text-xs text-emerald-400 break-all"></div>
      <div class="space-y-1.5 text-xs">
        <label class="block text-zinc-400">Remote PC Server URL (for Native Android APK)</label>
        <input id="apk-server-url-input" type="text" placeholder="http://192.168.1.50:8000/?pair_token=..." class="w-full bg-zinc-950 border border-zinc-700 rounded-xl px-3 py-2 font-mono text-xs" />
      </div>
      <div class="flex gap-2">
        <button onclick="document.getElementById('sync-modal').classList.add('hidden')" class="flex-1 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold">
          Close
        </button>
        <button id="btn-save-apk-server" class="flex-1 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 text-xs font-bold">
          Connect APK to Server
        </button>
      </div>
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
