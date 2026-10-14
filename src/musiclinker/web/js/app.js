/**
 * Main UI controller for MusicLinker Studio v2.1:
 *  - Isolated #lyrics-container scroll math (never calls scrollIntoView, never hijacks download/page scroll)
 *  - 3-Tier Synced & Rough Lyrics Position Indicator + ±0.5s offset controls
 *  - Album Filter Pill Bar + Bulk Album Rename + Inline Track Metadata Editor
 *  - LLM Smart Curator & Deduplicator (via API Link / Proxy)
 *  - Custom Slider CSS (--progress) updater
 */
import { api } from "./api.js";
import { offlineStore } from "./offline-store.js";
import { AudioEngine } from "./audio-engine.js";

class MusicLinkerController {
  constructor() {
    this.tracks = [];
    this.albums = [];
    this.selectedAlbum = "";
    this.offlineIds = new Set();
    this.currentIndex = -1;

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
    if ("serviceWorker" in navigator) {
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

  // ---- Library & Album Auto-Filtering ----

  async refreshOfflineState() {
    const cached = await offlineStore.getOfflineTracks();
    this.offlineIds = new Set(cached.map((t) => t.id));
  }

  async loadAlbums() {
    try {
      const data = await api.request("/api/albums");
      this.albums = data.albums || [];
      this.renderAlbumFilterBar();
    } catch (_err) {
      this.albums = [];
    }
  }

  renderAlbumFilterBar() {
    const bar = document.getElementById("album-filter-bar");
    const renameBtn = document.getElementById("btn-rename-selected-album");
    if (!bar) return;

    if (renameBtn) {
      renameBtn.classList.toggle("hidden", !this.selectedAlbum);
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

  async loadLibrary(search = "") {
    try {
      const qs = new URLSearchParams({
        search,
        album: this.selectedAlbum,
      });
      const data = await api.request(`/api/tracks?${qs.toString()}`);
      this.tracks = data.tracks || [];
    } catch (_err) {
      const cached = await offlineStore.getOfflineTracks();
      this.tracks = this.selectedAlbum
        ? cached.filter((t) => (t.album || "").toLowerCase() === this.selectedAlbum.toLowerCase())
        : cached;
    }
    const countBadge = document.getElementById("library-count-badge");
    if (countBadge) countBadge.textContent = `${this.tracks.length} tracks`;
    this.renderTrackList();
  }

  renderTrackList() {
    const container = document.getElementById("track-list");
    if (!container) return;
    if (!this.tracks.length) {
      container.innerHTML = `
        <div class="p-10 text-center text-zinc-400 space-y-2">
          <div class="text-2xl">🎵</div>
          <div class="text-sm">No tracks match the current filter.</div>
          <div class="text-xs text-zinc-500">Use the <b>Extractor</b> tab to pull your top Last.fm tracks or clear the album filter.</div>
        </div>`;
      return;
    }

    container.innerHTML = this.tracks
      .map((t, idx) => {
        const isOffline = this.offlineIds.has(t.id);
        const isCurrent = idx === this.currentIndex;
        const yearBadge = t.original_year || t.release_year || "";
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
                  t.cover_url
                    ? `<img src="${t.cover_url}" class="w-full h-full object-cover" alt="cover"/>`
                    : `<span class="text-emerald-400 font-bold">♪</span>`
                }
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
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
            <div class="flex items-center gap-2 shrink-0 ml-2">
              <span class="text-[11px] text-zinc-500 hidden md:inline font-mono">${t.lastfm_playcount || 0}p</span>
              <button
                data-edit-idx="${idx}"
                title="Rename Track or Album Internally (Updates SQLite & MP3 ID3 Tags)"
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

  // ---- Internal Track & Bulk Album Renaming ----

  openEditTrackModal(track) {
    const modal = document.getElementById("edit-track-modal");
    if (!modal) return;
    document.getElementById("edit-track-id").value = String(track.id);
    document.getElementById("edit-track-title").value = track.title || "";
    document.getElementById("edit-track-artist").value = track.artist_display || "";
    document.getElementById("edit-track-album").value = track.album || "";
    document.getElementById("edit-track-year").value = track.original_year || track.release_year || "";
    modal.classList.remove("hidden");
  }

  async saveEditedTrackMetadata() {
    const trackId = Number(document.getElementById("edit-track-id")?.value || 0);
    const title = document.getElementById("edit-track-title")?.value.trim();
    const artist = document.getElementById("edit-track-artist")?.value.trim();
    const album = document.getElementById("edit-track-album")?.value.trim();
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
    this.lastActiveLyricIdx = -1;
    this.lyricOffsetSec = 0.0;
    this.updateLyricOffsetLabel();

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
      } catch (_err) {}
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
      this.lyricsData = { synced: false, sync_mode: "none", lines: [] };
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

    // Clicking any lyric line jumps the player to that timestamp
    box.querySelectorAll("[data-lyric-time]").forEach((el) => {
      el.addEventListener("click", () => {
        const t = Number(el.dataset.lyricTime || 0);
        this.engine.seekToSeconds(Math.max(0, t - this.lyricOffsetSec));
      });
    });
  }

  /**
   * Crucial Bug Fix:
   *  - Never calls `el.scrollIntoView()`, which scrolled ancestor containers and prevented checking downloads.
   *  - Computes `box.scrollTo({ top })` strictly inside `#lyrics-container` ONLY when `activeIdx` changes,
   *    and respects `this.userScrollLockUntil` when the user manually scrolls.
   */
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

    // Update rough/exact lyric progress indicator bar & text
    const roughPct = Math.round(((activeIdx + 1) / totalLines) * 100);
    const roughPosEl = document.getElementById("lyric-rough-pos");
    const roughBarEl = document.getElementById("lyric-progress-fill");
    if (roughPosEl) {
      roughPosEl.textContent = `Line ${activeIdx + 1}/${totalLines} (~${roughPct}%)`;
    }
    if (roughBarEl) {
      roughBarEl.style.width = `${roughPct}%`;
    }

    // Only touch DOM classes and container scroll when the active line actually changes
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
    if (titleEl) titleEl.textContent = track.title;
    if (artistEl) artistEl.textContent = `${track.artist_display} • ${track.album}`;
    if (coverEl) {
      coverEl.src = track.cover_url || "/icon.svg";
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

  // ---- Extraction Job & Pinned Live Download Telemetry ----

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
      api.subscribeJobEvents(job.job_id, (state) => {
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
      // Save any updated proxy fields first
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

  // ---- Settings & Phone Sync ----

  async loadSettingsIntoUI() {
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
    if (!modal) return;
    modal.classList.remove("hidden");
    try {
      const info = await api.request("/api/sync/info");
      if (urlBox) urlBox.textContent = info.pairing_url;
    } catch (err) {
      if (urlBox) urlBox.textContent = err.message;
    }
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
    // Player controls
    document.getElementById("btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("car-btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("car-btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("car-btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-car-mode")?.addEventListener("click", () => this.toggleCarMode());
    document.getElementById("btn-exit-car")?.addEventListener("click", () => this.toggleCarMode());

    // Styled Seek & Volume Sliders
    document.getElementById("seek-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.seekToFraction(Number(e.target.value) / 1000.0);
    });
    document.getElementById("volume-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.setBaseVolume(Number(e.target.value) / 100.0);
    });

    // Pause Lyric Auto-Scroll for 4.5s when user manually scrolls or touches #lyrics-container
    const lyricsBox = document.getElementById("lyrics-container");
    if (lyricsBox) {
      const lockScroll = () => {
        this.userScrollLockUntil = Date.now() + 4500;
      };
      lyricsBox.addEventListener("wheel", lockScroll, { passive: true });
      lyricsBox.addEventListener("touchmove", lockScroll, { passive: true });
      lyricsBox.addEventListener("mousedown", lockScroll);
    }

    // Lyric timing offset buttons
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

    // Jobs, LLM Curation & Settings
    document.getElementById("btn-start-extract")?.addEventListener("click", () => this.startExtractionJob());
    document.getElementById("btn-run-curator")?.addEventListener("click", () => this.runLLMLibraryAudit());
    document.getElementById("btn-apply-curation")?.addEventListener("click", () => this.applySelectedCurationActions());
    document.getElementById("btn-show-sync")?.addEventListener("click", () => this.openSyncModal());
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