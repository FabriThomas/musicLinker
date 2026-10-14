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