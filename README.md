# 🎵 MusicLinker: Native Desktop App with System Tray & Mobile Sync
### Offline-First Music Player for Windows & Android with Last.fm Scrobbler

Extract your most-played songs from Last.fm, eliminate repeats via SHA-256 hash checks, auto-tag rich ID3v2.4 metadata (multi-artists, original release dates, and album art), and play offline in a **Native Windows Desktop Window** (with **System Tray Minimization & Controls**) and on **Android** with automatic **Last.fm Scrobbling**!

---

## ⚡ Key Features

### 1. 🪟 Native Windows Desktop App with System Tray
* **Standalone Window**: Runs in a dedicated native desktop window (powered by WebView2) with custom icon and no browser URL bars or tabs.
* **Minimize to Tray**: Clicking `[X]` or minimizing hides the app into the Windows notification tray (near the clock) so your music keeps playing uninterrupted in the background while you work or game.
* **Tray Controls**: Right-click the system tray icon to:
  * ▶ / ⏸ **Play / Pause**
  * ⏭ **Next Track**
  * ⏮ **Previous Track**
  * 🗔 **Show Window**
  * 🔽 **Minimize to Tray**
  * ❌ **Exit App**

### 2. 📱 Android Phone Sync & Offline Playback
* **On-Launch Personal Server**: The desktop app runs your personal server in the background on your home Wi-Fi.
* **QR Quick Connect**: Scan the in-app QR code from your phone's camera to connect instantly.
* **Offline Storage on Phone**: Tap **"Save for Offline"** next to any song to cache it directly into your phone.
* **Standalone Mobile App**: Add to Home Screen in Chrome on Android to install as an app with full lockscreen & notification media controls!

### 3. 🎤 Animated Synced Karaoke Lyrics
* Line-by-line timestamped lyrics powered by LRCLIB.
* Animated active lyric scaling and glowing.
* Click any line to seek instantly.
* Offline `.lrc` caching alongside your `.mp3` files.

### 4. 🔊 Volume Normalization & Car Mode
* **Loudness Normalizer**: Built-in dynamic compression levels uneven YouTube rips.
* **Car Driving Mode**: Oversized buttons, high-contrast ambient glow, large lyrics, and `Screen WakeLock` to keep your phone display awake while mounted in the car.

### 5. 📻 Bi-Directional Last.fm Integration & Scrobbler
* Real-time **Now Playing** updates.
* Official **Scrobbling** (50% or 4 min rule).
* **Offline Scrobble Queue**: Plays listened to while offline on your phone or PC are automatically queued and synced to Last.fm upon reconnecting!

### 6. 🎛️ Downloader & Transition Manager
* **Batch Extractor**: Extract tracks with `> 10` plays from Last.fm presets or past years, with download limits (Top 10, 25, 50).
* **Individual Search**: Search and download any individual track or album directly.
* **Random Song Transitions**: Configure custom crossfade transitions between specific songs with a random chance trigger!

---

## 🚀 Quick Start

### 1. Launch the Desktop App
Double-click [`start_player.bat`](file:///c:/Users/Fabrício%20N%20Thomas/Documents/music%20linker/start_player.bat) (or run `py desktop_app.py`).

* Opens the **Native Desktop Player** window.
* Places the **MusicLinker Icon** in your Windows System Tray.
* Closing the window minimizes to tray; right-click the tray icon for quick playback controls!

### 2. Connect Your Phone
1. In the player, click **Phone Sync**.
2. Scan the **QR Code** with your phone's camera.
3. Tap `⋮` &rarr; **Add to Home Screen** on Android.
