/**
 * Main UI controller for MusicLinker Studio & Android APK v3.0.9:
 *  - Single Track / Direct Link Downloader (Search query, YouTube/YTM URL, or Spotify Track URL)
 *  - Full Album & Playlist Downloader (Spotify Album/Playlist, YouTube Playlist/Chapters, MusicBrainz, Last.fm)
 *  - Play by Album (1..N track_number order), Randomizer (Shuffle), and Sort by Track # / Alphabetical / Plays
 *  - 3-Layer Selection Mode:
 *      Layer 1 (Artists): starts 100% unselected
 *      Layer 2 (Albums, optional): starts 100% unselected (if 0 selected, all albums for selected artists pass through)
 *      Layer 3 (Tracks): starts 100% SELECTED for all matching tracks
 *  - Isolated #lyrics-container scroll math + 3-Tier Synced/Rough Lyrics
 */
import { api } from "./api.js";
import { offlineStore } from "./offline-store.js";
import { AudioEngine, EQ_PRESETS, EQ_BANDS } from "./audio-engine.js";

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
    this.searchDebounceTimer = null;
    this.activeJobId = null;
    this.activeEventSource = null;
    this.isJobRunning = false;

    // 3-Layer Selection Mode & Playlists State
    this.viewMode = "standard"; // 'standard' | 'layered' | 'playlists'
    this.layerSelectedArtists = new Set();
    this.layerSelectedAlbums = new Set();
    this.layerExcludedTrackIds = new Set();
    this.playlists = [];
    this.activePlaylist = null;
    this.bigPlayerOpen = false;
    this.exportFormat = "m3u";
    this.exportSource = "funnel";

    // Downloader Preview States
    this.previewedSingleTracks = [];
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

    // Standalone Mobile UX, Audio Version Swap & Multi-Select Album Grouping State
    this.mobileView = "library";
    this.isMultiSelectActive = false;
    this.selectedTrackIds = new Set();
    this.previewAudio = null;
    this.activePreviewVideoId = null;
    this.swapCurrentTrack = null;
    this.bulkMatchData = null;
    this.activeSwapTrackId = null;
    this.activeSwapInterval = null;
    this.albumInconsistencies = [];
    this.activeInconsistencyIdx = 0;
    this.selectedCanonicalCoverTrackId = null;
    this.selectedCanonicalYear = "";

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
    this.initMobileNavigation();
    this.initAllSliderFills();
    this.initEqualizerUI();
    const volBar = document.getElementById("volume-bar");
    if (volBar) {
      const initVal = Number(volBar.value || 85);
      const volLabel = document.getElementById("volume-label");
      if (volLabel) volLabel.textContent = `${initVal}%`;
      this.updateVolumeIcon(initVal);
    }
    await this.checkServerConnection();
    window.addEventListener("ml:show-connect-modal", (e) => {
      this.openSyncModal();
      if (e.detail?.reason) {
        this.showToast(e.detail.reason, "warning");
      }
    });
    await this.refreshOfflineState();
    await this.loadSettingsIntoUI();
    await this.loadAlbums();
    await this.loadLibrary("", true);
    await this.checkAlbumInconsistencies();
    offlineStore.flushOfflineScrobbles(api).catch(() => {});
  }

  // ---- Toast Notification Helper ----

  showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const colorClasses =
      type === "error"
        ? "bg-red-950/95 border-red-500/50 text-red-200"
        : type === "warning"
        ? "bg-amber-950/95 border-amber-500/50 text-amber-200"
        : "bg-zinc-900/95 border-emerald-500/50 text-emerald-200";

    const iconSvg =
      type === "error"
        ? `<svg class="w-4 h-4 stroke-current fill-none shrink-0" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`
        : type === "warning"
        ? `<svg class="w-4 h-4 stroke-current fill-none shrink-0" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`
        : `<svg class="w-4 h-4 stroke-current fill-none shrink-0" viewBox="0 0 24 24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    const toast = document.createElement("div");
    toast.className = `ml-toast pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border shadow-xl backdrop-blur-md text-xs font-medium ${colorClasses}`;
    toast.innerHTML = `<span class="shrink-0">${iconSvg}</span><span class="leading-snug">${this.escape(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(8px) scale(0.95)";
      toast.style.transition = "opacity 0.2s ease, transform 0.2s ease";
      setTimeout(() => toast.remove(), 220);
    }, 3200);
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

  async rescanLibraryOnDisk() {
    const btn = document.getElementById("btn-rescan-library");
    if (btn) btn.disabled = true;
    try {
      const res = await api.request("/api/library/rescan", { method: "POST" });
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      this.showToast(`Library synced: ${res.indexed} track(s) indexed, ${res.pruned} missing pruned.`);
    } catch (err) {
      this.showToast(`Rescan failed: ${err.message}`, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
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
        class="ml-btn px-3 py-1 rounded-full text-xs font-semibold shrink-0 border ${
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
            class="ml-btn px-3 py-1 rounded-full text-xs font-medium shrink-0 border flex items-center gap-1.5 ${
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
        this.loadLibrary(searchVal, false);
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

  async loadLibrary(search = "", reloadAll = true) {
    try {
      if (reloadAll || !this.allTracks.length) {
        const allRes = await api.request(`/api/tracks?sort_by=${encodeURIComponent(this.sortBy)}`);
        this.allTracks = allRes.tracks || [];
      }
      if (!search.trim() && !this.selectedAlbum) {
        this.tracks = [...this.allTracks];
      } else {
        const qs = new URLSearchParams({
          search,
          album: this.selectedAlbum,
          sort_by: this.sortBy,
        });
        const data = await api.request(`/api/tracks?${qs.toString()}`);
        this.tracks = data.tracks || [];
      }
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
    this.checkAlbumInconsistencies().catch(() => {});
  }

  renderTrackList() {
    const container = document.getElementById("track-list");
    if (!container) return;
    if (!this.tracks.length) {
      container.innerHTML = `
        <div class="p-10 text-center text-zinc-400 space-y-2.5">
          <div class="w-12 h-12 mx-auto text-zinc-600 flex items-center justify-center">
            <svg class="w-10 h-10 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
          </div>
          <div class="text-sm font-medium text-zinc-300">No tracks match the current filter.</div>
          <div class="text-xs text-zinc-500">Open the <b>Downloader</b> tab to grab any single song, Spotify/YouTube link, playlist, or full studio album.</div>
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
        const eqIndicator = isCurrent
          ? `<span class="ml-eq-bars shrink-0" title="Now Playing"><span class="ml-eq-bar"></span><span class="ml-eq-bar"></span><span class="ml-eq-bar"></span></span>`
          : "";
        const syncIcon =
          t.lyric_sync_mode === "exact_lrc"
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0" title="Exact LRCLIB Synced Lyrics">LRC</span>`
            : t.lyric_sync_mode === "youtube_captions" || t.lyric_sync_mode === "rough_interpolated"
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 shrink-0" title="Rough Synced Lyrics">≈Sync</span>`
            : "";

        const isSelected = this.selectedTrackIds.has(t.id);
        const checkboxHtml = `<input type="checkbox" data-select-id="${t.id}" ${isSelected ? "checked" : ""} class="track-select-checkbox mr-2.5 shrink-0 ${this.isMultiSelectActive ? "" : "hidden"}" />`;

        return `
          <div data-track-row-id="${t.id}" class="ml-track-row group flex items-center justify-between p-2.5 rounded-xl border ${
            isSelected
              ? "is-selected bg-emerald-950/60 border-emerald-500/70"
              : isCurrent
              ? "bg-emerald-950/50 border-emerald-500/45 shadow-sm"
              : "bg-zinc-900/30 border-zinc-800/40 hover:bg-zinc-800/65 hover:border-zinc-700/80"
          }">
            <div class="flex items-center gap-3 min-w-0 flex-1 cursor-pointer" data-play-idx="${idx}">
              ${checkboxHtml}
              <div class="w-11 h-11 rounded-lg bg-zinc-800 flex items-center justify-center overflow-hidden shrink-0 border border-zinc-700/60 relative">
                ${
                  coverSrc
                    ? `<img src="${coverSrc}" class="w-full h-full object-cover" alt="cover"/>`
                    : `<svg class="w-5 h-5 text-emerald-400 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`
                }
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  ${eqIndicator}
                  ${numBadge ? `<span class="text-[11px] font-mono text-emerald-400 font-semibold shrink-0">${numBadge}</span>` : ""}
                  <span class="font-semibold text-sm ${isCurrent ? "text-emerald-300" : "text-zinc-100"} truncate">${this.escape(t.title)}</span>
                  ${syncIcon}
                </div>
                <div class="text-xs text-zinc-400 truncate mt-0.5">
                  <span class="text-zinc-300">${this.escape(t.artist_display)}</span>
                  <span class="mx-1 text-zinc-600">•</span>
                  <span class="text-emerald-400/90 hover:underline" data-filter-album="${this.escapeAttr(t.album)}">${this.escape(t.album)}</span>
                  ${yearBadge ? `<span class="text-zinc-500 ml-1">(${yearBadge})</span>` : ""}
                </div>
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0 ml-2">
              <span class="text-[11px] text-zinc-500 hidden md:inline font-mono mr-1">${this.formatDuration(t.duration)}</span>
              <button
                data-swap-idx="${idx}"
                title="Change Version (Re-search & Preview 15s Snippet)"
                class="ml-btn-icon w-7 h-7 text-xs flex items-center justify-center hover:!text-emerald-300"
              >
                <svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
              </button>
              <button
                data-edit-idx="${idx}"
                title="Edit Track, Track #, or Album Metadata"
                class="ml-btn-icon w-7 h-7 text-xs flex items-center justify-center">
                <svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
              </button>
              <button
                data-delete-idx="${idx}"
                title="Delete Track from Library & Disk"
                class="ml-btn-icon w-7 h-7 text-xs flex items-center justify-center hover:!text-red-300 hover:!border-red-500/50 hover:!bg-red-950/70">
                <svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
              </button>
              <button
                data-offline-idx="${idx}"
                title="${isOffline ? "Saved for Offline (tap to remove)" : "Save for Offline (APK / Device Storage)"}"
                class="ml-btn px-2 py-1 rounded-lg text-xs font-medium border flex items-center gap-1 ${
                  isOffline
                    ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                    : "bg-zinc-800/80 border-zinc-700/80 text-zinc-300 hover:border-emerald-500/50 hover:text-emerald-300"
                }">
                ${isOffline
                  ? `<svg class="w-3.5 h-3.5 stroke-current fill-none text-emerald-400" viewBox="0 0 24 24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span class="text-[11px]">Offline</span>`
                  : `<svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`}
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    container.querySelectorAll("[data-play-idx]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-select-id]")) return;
        const idx = Number(el.dataset.playIdx);
        const track = this.tracks[idx];
        if (!track) return;

        if (e.shiftKey) {
          e.stopPropagation();
          e.preventDefault();
          this.handleShiftClickSelection(idx);
          return;
        }

        if (this.isMultiSelectActive || e.ctrlKey || e.metaKey) {
          e.stopPropagation();
          e.preventDefault();
          this.handleCtrlClickSelection(idx);
          return;
        }

        const albumSpan = e.target.closest("[data-filter-album]");
        if (albumSpan) {
          e.stopPropagation();
          this.selectedAlbum = albumSpan.dataset.filterAlbum || "";
          this.renderAlbumFilterBar();
          this.loadLibrary(document.getElementById("search-input")?.value || "", false);
          return;
        }
        this.lastSelectedTrackIdx = idx;
        this.playQueue = [...this.tracks];
        this.playByIndex(idx);
      });
    });

    container.querySelectorAll("[data-select-id]").forEach((cb) => {
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", (e) => {
        e.stopPropagation();
        const trackId = Number(cb.dataset.selectId);
        this.toggleTrackSelection(trackId, cb.checked);
      });
    });

    container.querySelectorAll("[data-swap-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const track = this.tracks[Number(btn.dataset.swapIdx)];
        if (track) this.openSwapAudioModal(track);
      });
    });

    container.querySelectorAll("[data-edit-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const track = this.tracks[Number(btn.dataset.editIdx)];
        if (track) this.openEditTrackModal(track);
      });
    });

    container.querySelectorAll("[data-delete-idx]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const track = this.tracks[Number(btn.dataset.deleteIdx)];
        if (!track) return;
        if (confirm(`Delete "${track.title}" by "${track.artist_display}" from library and disk?`)) {
          await this.deleteTrack(track.id, true, false);
        }
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
    this.showToast(`Playing album "${this.selectedAlbum}" (${albumTracks.length} tracks)`);
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
      if (btn) btn.innerHTML = `<svg class="w-3.5 h-3.5 stroke-current fill-none inline mr-1 text-emerald-400" viewBox="0 0 24 24" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span>Album Cached Offline</span>`;
      this.showToast(`Cached ${albumTracks.length} track(s) for offline playback.`);
    } catch (err) {
      this.showToast(`Failed caching album offline: ${err.message}`, "error");
      if (btn) btn.innerHTML = `<svg class="w-3.5 h-3.5 stroke-current fill-none inline mr-1" viewBox="0 0 24 24" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>Cache Offline</span>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  toggleShuffle() {
    this.shuffleEnabled = !this.shuffleEnabled;
    this.updateShuffleButtonUI();
    this.showToast(this.shuffleEnabled ? "Shuffle enabled" : "Shuffle disabled");
  }

  randomizeAndPlayCurrentView() {
    const pool = this.viewMode === "layered" ? this.getLayerSelectedTracks() : this.tracks;
    if (!pool.length) {
      this.showToast("No tracks available to shuffle.", "warning");
      return;
    }
    const shuffled = [...pool];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    this.shuffleEnabled = true;
    this.updateShuffleButtonUI();
    this.playQueue = shuffled;
    this.playByIndex(0);
    this.showToast(`Shuffling ${shuffled.length} track(s)`);
  }

  updateShuffleButtonUI() {
    const btn = document.getElementById("btn-shuffle");
    if (!btn) return;
    btn.className = this.shuffleEnabled
      ? "ml-btn px-3 py-1.5 rounded-xl bg-emerald-500/20 border border-emerald-500/50 text-emerald-300 text-xs font-semibold"
      : "ml-btn ml-btn-secondary px-3 py-1.5 text-xs text-zinc-400";
  }

  // ---- 3-Layer Hierarchical Selection Mode ----

  switchLibraryViewMode(mode) {
    this.viewMode = mode;
    const stdPanel = document.getElementById("standard-library-view");
    const layerPanel = document.getElementById("layered-selector-view");
    const plPanel = document.getElementById("playlists-view");
    const btnStd = document.getElementById("btn-view-standard");
    const btnLayer = document.getElementById("btn-view-layered");
    const btnPl = document.getElementById("btn-view-playlists");

    const isStd = mode === "standard";
    const isLayered = mode === "layered";
    const isPl = mode === "playlists";

    if (stdPanel) stdPanel.classList.toggle("hidden", !isStd);
    if (layerPanel) layerPanel.classList.toggle("hidden", !isLayered);
    if (plPanel) plPanel.classList.toggle("hidden", !isPl);

    const activeClass = "ml-btn px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-500 text-zinc-950";
    const inactiveClass = "ml-btn px-3 py-1 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200";

    if (btnStd) btnStd.className = isStd ? activeClass : inactiveClass;
    if (btnLayer) btnLayer.className = isLayered ? activeClass : inactiveClass;
    if (btnPl) btnPl.className = isPl ? activeClass : inactiveClass;

    if (isLayered) {
      this.renderLayeredSelector();
    } else if (isPl) {
      this.loadPlaylists();
    }
  }

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

    const artistMap = new Map();
    this.allTracks.forEach((t) => {
      const name = t.album_artist || t.artist_display || "Unknown Artist";
      const key = name.toLowerCase();
      const entry = artistMap.get(key) || { name, key, count: 0 };
      entry.count++;
      artistMap.set(key, entry);
    });
    const artists = Array.from(artistMap.values()).sort((a, b) => a.name.localeCompare(b.name));

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

    artistsBox.querySelectorAll("[data-layer-artist]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const key = cb.dataset.layerArtist;
        if (cb.checked) {
          this.layerSelectedArtists.add(key);
        } else {
          this.layerSelectedArtists.delete(key);
        }
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
      this.showToast("Select at least one artist in Layer 1 first.", "warning");
      return;
    }
    this.playQueue = selected;
    this.playByIndex(0);
    this.showToast(`Playing ${selected.length} selected track(s)`);
  }

  async cacheLayeredSelectionOffline() {
    const selected = this.getLayerSelectedTracks();
    if (!selected.length) {
      this.showToast("Select at least one artist in Layer 1 first.", "warning");
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
      if (btn) btn.innerHTML = `<svg class="w-3.5 h-3.5 stroke-current fill-none inline mr-1 text-emerald-400" viewBox="0 0 24 24" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span>Cached ${selected.length}</span>`;
      this.showToast(`Cached ${selected.length} track(s) offline.`);
    } catch (err) {
      this.showToast(`Offline cache error: ${err.message}`, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ---- Single Track & Direct Link Downloader ----

  async previewSingleTrackOrLink() {
    const queryInput = document.getElementById("dl-single-query");
    const box = document.getElementById("dl-single-results");
    const query = queryInput?.value.trim() || "";
    if (!query) {
      this.showToast("Enter a song title, YouTube link, or Spotify link first.", "warning");
      return;
    }

    const btn = document.getElementById("btn-search-single");
    if (btn) btn.disabled = true;
    if (box) {
      box.innerHTML = `<div class="text-xs text-zinc-400 p-2.5 bg-zinc-900/60 rounded-xl border border-zinc-800 animate-pulse">Searching streams & resolving metadata...</div>`;
    }

    try {
      const res = await api.request(`/api/youtube/search?q=${encodeURIComponent(query)}`);
      this.previewedSingleTracks = res.results || [];
      if (!this.previewedSingleTracks.length) {
        if (box) {
          box.innerHTML = `<div class="text-xs text-amber-400 p-2.5 bg-zinc-900/60 rounded-xl border border-amber-500/30">No matching tracks found. Try a different query or paste a direct URL.</div>`;
        }
        return;
      }

      if (box) {
        box.innerHTML = this.previewedSingleTracks
          .map((item, idx) => {
            const thumb = item.thumbnail
              ? `<img src="${this.escapeAttr(item.thumbnail)}" class="w-10 h-10 rounded-lg object-cover border border-zinc-700 shrink-0" alt="thumb" />`
              : `<div class="w-10 h-10 rounded-lg bg-zinc-800 flex items-center justify-center text-emerald-400 font-bold shrink-0">♪</div>`;
            const durBadge = item.duration ? this.formatDuration(item.duration) : "";
            const srcBadge = (item.source || "youtube").toUpperCase();
            return `
              <div class="flex items-center justify-between gap-2.5 p-2 rounded-xl bg-zinc-900/90 border border-zinc-800 hover:border-zinc-700 transition text-xs">
                <div class="flex items-center gap-2.5 min-w-0 flex-1">
                  ${thumb}
                  <div class="min-w-0 flex-1">
                    <div class="font-semibold text-zinc-100 truncate">${this.escape(item.title)}</div>
                    <div class="text-[11px] text-zinc-400 truncate flex items-center gap-1.5 mt-0.5">
                      <span class="px-1.5 py-0.2 rounded bg-emerald-500/15 text-emerald-300 font-mono text-[9px]">${this.escape(srcBadge)}</span>
                      <span class="truncate">${this.escape(item.uploader || item.artist || "")}</span>
                      ${durBadge ? `<span class="text-zinc-500 font-mono">• ${durBadge}</span>` : ""}
                    </div>
                  </div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                  <button
                    data-fill-single-idx="${idx}"
                    class="ml-btn-icon w-7 h-7 text-xs flex items-center justify-center"
                    title="Copy metadata into override fields">
                    <svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                  </button>
                  <button
                    data-dl-single-idx="${idx}"
                    class="ml-btn ml-btn-primary px-2.5 py-1.5 text-xs flex items-center gap-1">
                    <svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    <span>Download</span>
                  </button>
                </div>
              </div>
            `;
          })
          .join("");

        box.querySelectorAll("[data-fill-single-idx]").forEach((b) => {
          b.addEventListener("click", () => {
            const item = this.previewedSingleTracks[Number(b.dataset.fillSingleIdx)];
            if (!item) return;
            const detailsEl = document.getElementById("dl-single-overrides-details");
            if (detailsEl) detailsEl.open = true;
            let artistGuess = item.artist || "";
            let titleGuess = item.track_title || item.title || "";
            if (!artistGuess && titleGuess.includes(" - ")) {
              const parts = titleGuess.split(" - ");
              artistGuess = parts[0].trim();
              titleGuess = parts.slice(1).join(" - ").trim();
            }
            const artEl = document.getElementById("dl-single-artist");
            const titEl = document.getElementById("dl-single-title");
            const albEl = document.getElementById("dl-single-album");
            const numEl = document.getElementById("dl-single-track-num");
            if (artEl) artEl.value = artistGuess;
            if (titEl) titEl.value = titleGuess;
            if (albEl && item.album) albEl.value = item.album;
            if (numEl && item.track_number) numEl.value = String(item.track_number);
            this.showToast("Populated metadata overrides from preview.");
          });
        });

        box.querySelectorAll("[data-dl-single-idx]").forEach((b) => {
          b.addEventListener("click", () => {
            const item = this.previewedSingleTracks[Number(b.dataset.dlSingleIdx)];
            if (item) this.startDirectTrackDownload(item);
          });
        });
      }
    } catch (err) {
      if (box) {
        box.innerHTML = `<div class="text-xs text-rose-400 p-2">Search failed: ${this.escape(err.message)}</div>`;
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async startDirectTrackDownload(previewItem = null) {
    const rawQuery = document.getElementById("dl-single-query")?.value.trim() || "";
    const queryOrUrl = previewItem ? (previewItem.url || previewItem.title || rawQuery) : rawQuery;
    if (!queryOrUrl) {
      this.showToast("Enter a song query, YouTube URL, or Spotify link to download.", "warning");
      return;
    }

    let artistOverride = document.getElementById("dl-single-artist")?.value.trim() || "";
    let titleOverride = document.getElementById("dl-single-title")?.value.trim() || "";
    let albumOverride = document.getElementById("dl-single-album")?.value.trim() || "";
    let trackNumOverride = Number(document.getElementById("dl-single-track-num")?.value || 0);

    if (previewItem && !artistOverride && !titleOverride && previewItem.artist && previewItem.track_title) {
      artistOverride = previewItem.artist;
      titleOverride = previewItem.track_title;
      albumOverride = albumOverride || previewItem.album || "";
      trackNumOverride = trackNumOverride || Number(previewItem.track_number || 0);
    }

    const bitrate = Number(document.getElementById("dl-single-bitrate")?.value || 320);
    const method = document.getElementById("dl-single-method")?.value || "auto";

    const logBox = document.getElementById("job-logs");
    const banner = document.getElementById("live-job-banner");
    if (logBox) logBox.textContent = `Starting track download for '${queryOrUrl}'...`;
    if (banner) banner.classList.remove("hidden");

    try {
      const job = await api.request("/api/jobs/download", {
        method: "POST",
        body: JSON.stringify({
          query_or_url: queryOrUrl,
          artist_override: artistOverride,
          title_override: titleOverride,
          album_override: albumOverride,
          track_number_override: trackNumOverride,
          bitrate_kbps: bitrate,
          download_method: method,
        }),
      });
      if (this.isJobRunning) {
        this.showToast(`Queued in FIFO buffer: ${previewItem?.title || queryOrUrl}`);
      } else {
        this.activeJobId = job.job_id;
        this.showToast(`Queued download: ${previewItem?.title || queryOrUrl}`);
        this.subscribeToJobProgress(job.job_id);
      }
    } catch (err) {
      this.showToast(`Download failed to start: ${err.message}`, "error");
    }
  }

  // ---- Full Album & Playlist Downloader (Preview & Batch Download) ----

  async previewAlbumForDownload() {
    const artistInput = document.getElementById("dl-album-artist");
    const albumInput = document.getElementById("dl-album-title");
    let artist = artistInput?.value.trim() || "";
    let album = albumInput?.value.trim() || "";

    if ((artist.startsWith("http://") || artist.startsWith("https://") || artist.startsWith("spotify:")) && !album) {
      album = artist;
      artist = "";
      if (albumInput) albumInput.value = album;
      if (artistInput) artistInput.value = "";
    }

    const box = document.getElementById("dl-album-preview-list");
    if (!album && !artist) {
      this.showToast("Enter an Album/Playlist name, Spotify link, or YouTube URL.", "warning");
      return;
    }
    const btn = document.getElementById("btn-preview-album");
    if (btn) btn.disabled = true;
    if (box) {
      box.innerHTML = `<div class="text-xs text-zinc-400 p-2.5 bg-zinc-900/60 rounded-xl border border-zinc-800 animate-pulse">Resolving tracklist from Spotify / YouTube / MusicBrainz...</div>`;
    }

    try {
      const qs = new URLSearchParams({ artist, album });
      const res = await api.request(`/api/albums/lookup?${qs.toString()}`);
      this.previewedAlbumTracks = res.tracks || [];
      if (!this.previewedAlbumTracks.length) {
        if (box) {
          box.innerHTML = `<div class="text-xs text-amber-400 p-2.5 bg-zinc-900/60 rounded-xl border border-amber-500/30">No tracks found. Try entering Artist and Album name or a direct Spotify/YouTube link.</div>`;
        }
        return;
      }

      if (res.artist && artistInput && (!artistInput.value.trim() || artistInput.value.startsWith("http"))) {
        artistInput.value = res.artist;
      }
      if (res.album && albumInput && (albumInput.value.startsWith("http") || albumInput.value.startsWith("spotify:") || !albumInput.value.trim())) {
        albumInput.value = res.album;
      }

      if (box) {
        const entityType = (res.entity_type || "album").toUpperCase();
        const sourceLabel = (res.source || "online").toUpperCase();
        const headerHtml = `
          <div class="flex items-center justify-between gap-2 p-2 rounded-xl bg-zinc-900 border border-zinc-800 text-[11px]">
            <div class="flex items-center gap-2 min-w-0">
              ${
                res.cover_url
                  ? `<img src="${this.escapeAttr(res.cover_url)}" class="w-9 h-9 rounded-lg object-cover border border-zinc-700 shrink-0" alt="Cover" />`
                  : `<div class="w-9 h-9 rounded-lg bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-300 shrink-0"><svg class="w-4 h-4 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg></div>`
              }
              <div class="min-w-0">
                <div class="font-semibold text-zinc-100 truncate">${this.escape(res.album || "Collection")}</div>
                <div class="text-indigo-400 text-[10px] font-mono truncate">${this.escape(sourceLabel)} • ${this.escape(entityType)} • ${res.tracks.length} tracks</div>
              </div>
            </div>
            <button id="btn-toggle-all-album-tracks" type="button" class="ml-btn ml-btn-secondary px-2 py-1 text-[10px] shrink-0 flex items-center gap-1">
              <svg class="w-3 h-3 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
              <span>Toggle All</span>
            </button>
          </div>
        `;

        box.innerHTML =
          headerHtml +
          this.previewedAlbumTracks
            .map(
              (tr, idx) => `
              <label class="flex items-center justify-between p-1.5 rounded-lg bg-zinc-950 border border-zinc-800/90 text-xs hover:border-zinc-700 cursor-pointer transition">
                <div class="flex items-center gap-2 truncate">
                  <input type="checkbox" data-dl-album-track="${idx}" checked class="accent-emerald-500" />
                  <span class="font-mono text-emerald-400 text-[11px]">#${String(tr.track_number || idx + 1).padStart(2, "0")}</span>
                  <span class="truncate font-medium text-zinc-200">${this.escape(tr.title)}</span>
                  ${tr.artist && tr.artist !== res.artist ? `<span class="text-zinc-400 text-[10px] truncate">— ${this.escape(tr.artist)}</span>` : ""}
                </div>
                ${tr.duration ? `<span class="font-mono text-[10px] text-zinc-500 ml-2 shrink-0">${this.formatDuration(tr.duration)}</span>` : ""}
              </label>
            `
            )
            .join("");

        document.getElementById("btn-toggle-all-album-tracks")?.addEventListener("click", () => {
          const cbs = Array.from(box.querySelectorAll("[data-dl-album-track]"));
          const allChecked = cbs.every((c) => c.checked);
          cbs.forEach((c) => {
            c.checked = !allChecked;
          });
        });
      }
      this.showToast(`Loaded ${this.previewedAlbumTracks.length} track(s) from ${res.album || "collection"}`);
    } catch (err) {
      if (box) box.innerHTML = `<div class="text-xs text-rose-400 p-2">Lookup failed: ${this.escape(err.message)}</div>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async startAlbumDownloadJob() {
    let artist = document.getElementById("dl-album-artist")?.value.trim() || "";
    let album = document.getElementById("dl-album-title")?.value.trim() || "";
    const bitrate = Number(document.getElementById("ext-bitrate")?.value || 320);

    if ((artist.startsWith("http://") || artist.startsWith("https://") || artist.startsWith("spotify:")) && !album) {
      album = artist;
      artist = "";
    }

    if (!album) {
      this.showToast("Please enter an Album/Playlist Title, Spotify URL, or YouTube URL.", "warning");
      return;
    }

    let selectedTracks = [];
    const checkboxes = document.querySelectorAll("[data-dl-album-track]:checked");
    if (checkboxes.length && this.previewedAlbumTracks.length) {
      selectedTracks = Array.from(checkboxes).map(
        (cb) => this.previewedAlbumTracks[Number(cb.dataset.dlAlbumTrack)]
      );
    }

    const method = document.getElementById("dl-album-method")?.value || "auto";

    const logBox = document.getElementById("job-logs");
    const banner = document.getElementById("live-job-banner");
    if (logBox) logBox.textContent = `Starting collection download for '${artist || "Collection"} — ${album}'...`;
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
          download_method: method,
        }),
      });
      if (this.isJobRunning) {
        this.showToast(`Queued in FIFO buffer: "${album}"`);
      } else {
        this.activeJobId = job.job_id;
        this.showToast(`Started downloading "${album}"`);
        this.subscribeToJobProgress(job.job_id);
      }
    } catch (err) {
      this.showToast(`Album download failed to start: ${err.message}`, "error");
    }
  }

  async cancelActiveJob() {
    if (!this.activeJobId) return;
    try {
      await api.request(`/api/jobs/${this.activeJobId}/cancel`, { method: "POST" });
      this.showToast("Cancellation requested for job.", "warning");
    } catch (err) {
      this.showToast(`Cancel error: ${err.message}`, "error");
    }
  }

  async togglePauseActiveJob() {
    if (!this.activeJobId) return;
    try {
      if (this.isJobPaused) {
        await api.request(`/api/jobs/${this.activeJobId}/resume`, { method: "POST" });
        this.showToast("Resuming download job...");
      } else {
        await api.request(`/api/jobs/${this.activeJobId}/pause`, { method: "POST" });
        this.showToast("Pausing download job...", "warning");
      }
    } catch (err) {
      this.showToast(`Pause/Resume error: ${err.message}`, "error");
    }
  }

  async retryActiveJob() {
    if (!this.activeJobId) return;
    try {
      await api.request(`/api/jobs/${this.activeJobId}/retry`, { method: "POST" });
      this.showToast("Job re-enqueued for retry connection.");
      const retryBtn = document.getElementById("btn-retry-job");
      if (retryBtn) retryBtn.classList.add("hidden");
    } catch (err) {
      this.showToast(`Retry error: ${err.message}`, "error");
    }
  }

  subscribeToJobProgress(jobId) {
    // 1. Close previous active EventSource if any to eliminate dual-stream visual glitches
    if (this.activeEventSource) {
      try {
        this.activeEventSource.close();
      } catch (_e) {}
      this.activeEventSource = null;
    }

    this.activeJobId = jobId;
    this.isJobRunning = true;
    this.jobTracksSeenCount = 0;
    const logBox = document.getElementById("job-logs");
    const pauseBtn = document.getElementById("btn-pause-job");
    const retryBtn = document.getElementById("btn-retry-job");
    const banner = document.getElementById("live-job-banner");

    if (banner) banner.classList.remove("hidden");
    if (pauseBtn) {
      pauseBtn.classList.remove("hidden");
      pauseBtn.innerHTML = `<svg class="w-3.5 h-3.5 fill-current inline mr-1" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg><span>Pause</span>`;
    }
    if (retryBtn) retryBtn.classList.add("hidden");

    this.activeEventSource = api.subscribeJobEvents(jobId, async (state) => {
      // Discard callbacks from previous jobs
      if (this.activeJobId !== jobId) return;

      this.isJobPaused = Boolean(state.is_paused);
      this.isJobRunning = (state.status === "running" || state.status === "queued");
      if (pauseBtn) {
        pauseBtn.innerHTML = this.isJobPaused
          ? `<svg class="w-3.5 h-3.5 fill-current inline mr-1" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg><span>Resume</span>`
          : `<svg class="w-3.5 h-3.5 fill-current inline mr-1" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg><span>Pause</span>`;
      }

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
        const statusPrefix = this.isJobPaused
          ? "[PAUSED] "
          : (state.status === "queued" ? "[QUEUED in FIFO queue] " : "");
        bannerStep.textContent = `${statusPrefix}${state.current_step} (${state.progress_pct}%)`;
      }
      if (bannerLyric) {
        bannerLyric.textContent = `Lyrics: ${state.lyric_sync_status || "Checking..."}`;
      }

      // Progressive library population: appear one-by-one as downloaded
      const addedList = state.tracks_added || [];
      if (addedList.length > this.jobTracksSeenCount) {
        const newTracks = addedList.slice(this.jobTracksSeenCount);
        this.jobTracksSeenCount = addedList.length;
        const searchVal = document.getElementById("search-input")?.value || "";
        this.loadLibrary(searchVal, true);
        this.loadAlbums();
        // Automatically cache newly downloaded track on device storage (offlineStore)
        for (const tr of newTracks) {
          offlineStore.saveTrackForOffline(tr, api).then(() => {
            this.refreshOfflineState().then(() => this.renderTrackList());
          }).catch(() => {});
        }
      }

      if (state.status === "completed") {
        this.isJobRunning = false;
        this.showToast(`Download job complete (${state.downloaded_count} track(s) added)!`);
        if (pauseBtn) pauseBtn.classList.add("hidden");
        if (retryBtn) retryBtn.classList.add("hidden");
        // Ensure all downloaded tracks are in device storage
        for (const tr of (state.tracks_added || [])) {
          offlineStore.saveTrackForOffline(tr, api).catch(() => {});
        }
        await this.refreshOfflineState();
        this.loadAlbums();
        this.loadLibrary(document.getElementById("search-input")?.value || "", true);
        await this._checkAndTransitionNextFifoJob();
      } else if (state.status === "failed") {
        this.isJobRunning = false;
        this.showToast(`Job interrupted or failed: ${state.current_step}`, "error");
        if (retryBtn) retryBtn.classList.remove("hidden");
      } else if (state.status === "cancelled") {
        this.isJobRunning = false;
        this.showToast("Download job cancelled.", "warning");
        if (pauseBtn) pauseBtn.classList.add("hidden");
        await this._checkAndTransitionNextFifoJob();
      }
    });
  }

  async _checkAndTransitionNextFifoJob() {
    try {
      const data = await api.request("/api/jobs");
      const jobs = data.jobs || [];
      const nextActive = jobs.find((j) => (j.status === "running" || j.status === "queued") && j.job_id !== this.activeJobId);
      if (nextActive) {
        this.showToast(`FIFO Buffer: Moving to next job...`);
        this.subscribeToJobProgress(nextActive.job_id);
      }
    } catch (_e) {}
  }

  // ---- Web Audio API 5-Band Equalizer (DSP) ----

  initEqualizerUI() {
    this.renderEqPresetsUI();
    this.refreshEqualizerModalValues();
  }

  openEqualizerModal() {
    const modal = document.getElementById("eq-modal");
    if (!modal) return;
    this.engine.initAudioContext();
    this.refreshEqualizerModalValues();
    modal.classList.remove("hidden");
  }

  closeEqualizerModal() {
    const modal = document.getElementById("eq-modal");
    if (modal) modal.classList.add("hidden");
  }

  refreshEqualizerModalValues() {
    const state = this.engine.getEqState();
    const toggle = document.getElementById("eq-enable-toggle");
    const label = document.getElementById("eq-enable-label");
    if (toggle) toggle.checked = state.enabled;
    if (label) {
      label.textContent = state.enabled ? "DSP ON" : "DSP OFF";
      label.className = state.enabled ? "text-emerald-400 font-bold" : "text-zinc-500 font-bold";
    }

    const preampSlider = document.getElementById("eq-preamp-slider");
    const preampBadge = document.getElementById("eq-preamp-badge");
    const limiterToggle = document.getElementById("eq-limiter-toggle");
    const pGain = state.preampGainDb || 0;
    if (preampSlider) preampSlider.value = pGain;
    if (preampBadge) {
      const mult = Math.pow(10, pGain / 20).toFixed(1);
      preampBadge.textContent = `${pGain > 0 ? "+" : ""}${pGain} dB (${mult}x)`;
    }
    if (limiterToggle) limiterToggle.checked = Boolean(state.limiterEnabled);

    state.gains.forEach((gain, idx) => {
      const slider = document.getElementById(`eq-slider-${idx}`);
      const valBadge = document.getElementById(`eq-val-${idx}`);
      if (slider) slider.value = gain;
      if (valBadge) valBadge.textContent = `${gain > 0 ? "+" : ""}${gain} dB`;
    });
    this.renderEqPresetsUI();
  }

  renderEqPresetsUI() {
    const container = document.getElementById("eq-presets-list");
    if (!container) return;
    const state = this.engine.getEqState();
    const activePreset = state.preset;

    container.innerHTML = Object.keys(EQ_PRESETS)
      .map((name) => {
        const isActive = activePreset === name;
        return `
          <button
            type="button"
            data-eq-preset="${name}"
            class="ml-btn px-2.5 py-1 text-xs rounded-lg border ${
              isActive
                ? "bg-emerald-500 text-zinc-950 border-emerald-400 font-bold shadow-sm"
                : "bg-zinc-800/80 text-zinc-300 border-zinc-700 hover:border-zinc-500"
            }">
            ${name}
          </button>
        `;
      })
      .join("");

    container.querySelectorAll("[data-eq-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pName = btn.dataset.eqPreset;
        this.engine.setPreset(pName);
        this.refreshEqualizerModalValues();
        this.showToast(`EQ Preset: ${pName}`);
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
      this.showToast("Title, Artist, and Album are required.", "warning");
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
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      this.showToast(`Updated ID3v2.4 metadata for "${title}"`);
    } catch (err) {
      this.showToast(`Failed saving metadata: ${err.message}`, "error");
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
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      this.showToast(`Renamed album "${oldAlbum}" → "${newAlbum}"`);
    } catch (err) {
      this.showToast(`Album rename failed: ${err.message}`, "error");
    }
  }

  async deleteTrack(trackId, deleteFile = true, unmarkHash = false) {
    try {
      await api.request(`/api/tracks/${trackId}?delete_file=${deleteFile}&unmark_hash=${unmarkHash}`, {
        method: "DELETE",
      });
      const currentTrack = this.playQueue[this.currentIndex];
      if (currentTrack && currentTrack.id === trackId) {
        if (this.playQueue.length > 1) {
          this.playQueue = this.playQueue.filter((t) => t.id !== trackId);
          if (this.currentIndex >= this.playQueue.length) {
            this.currentIndex = 0;
          }
          await this.playByIndex(this.currentIndex);
        } else {
          this.engine.audio.pause();
          this.engine.audio.removeAttribute("src");
          this.currentIndex = -1;
          this.playQueue = [];
          this.updateNowPlayingBar(null);
          this.updatePlayButtonUI(false);
        }
      } else {
        this.playQueue = this.playQueue.filter((t) => t.id !== trackId);
      }
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      if (this.viewMode === "layered") {
        this.renderLayeredSelector();
      }
      this.showToast("Track deleted from library.");
    } catch (err) {
      this.showToast(`Failed to delete track: ${err.message}`, "error");
    }
  }

  async deleteSelectedAlbum() {
    if (!this.selectedAlbum) return;
    const albumName = this.selectedAlbum;
    if (!confirm(`Are you sure you want to delete the entire album "${albumName}" and all of its tracks from disk? This cannot be undone.`)) {
      return;
    }
    try {
      await api.request(`/api/albums/${encodeURIComponent(albumName)}?delete_files=true`, {
        method: "DELETE",
      });
      this.selectedAlbum = "";
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      if (this.viewMode === "layered") {
        this.renderLayeredSelector();
      }
      this.showToast(`Deleted album "${albumName}"`);
    } catch (err) {
      this.showToast(`Failed to delete album: ${err.message}`, "error");
    }
  }

  async deleteLayerSelectedTracks() {
    const selected = this.getLayerSelectedTracks();
    if (!selected.length) {
      this.showToast("No tracks currently selected in Layer 3.", "warning");
      return;
    }
    if (!confirm(`Permanently delete ${selected.length} selected track(s) from your library and disk?`)) {
      return;
    }
    try {
      const res = await api.request("/api/tracks/batch-delete", {
        method: "POST",
        body: JSON.stringify({
          track_ids: selected.map((t) => t.id),
          delete_files: true,
          unmark_hashes: false,
        }),
      });
      this.showToast(`Deleted ${res.count || selected.length} track(s).`);
      this.layerExcludedTrackIds.clear();
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
      this.renderLayeredSelector();
    } catch (err) {
      this.showToast(`Batch delete failed: ${err.message}`, "error");
    }
  }

  async deleteAlbumAlias(aliasId) {
    if (!confirm("Remove this album canonicalization alias rule?")) return;
    try {
      await api.request(`/api/albums/aliases/${aliasId}`, {
        method: "DELETE",
      });
      await this.loadAlbumAliases();
      this.showToast("Removed album alias rule.");
    } catch (err) {
      this.showToast(`Failed deleting alias: ${err.message}`, "error");
    }
  }

  async loadAlbumAliases() {
    const box = document.getElementById("settings-aliases-list");
    if (!box) return;
    try {
      const data = await api.request("/api/albums");
      const aliases = data.aliases || [];
      if (!aliases.length) {
        box.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2">No album alias rules defined. Edit an album or rename an album to create rules.</div>`;
        return;
      }
      box.innerHTML = aliases
        .map(
          (a) => `
          <div class="flex items-center justify-between p-2 rounded-xl bg-zinc-900 border border-zinc-800 text-xs">
            <div class="min-w-0 pr-2">
              <div class="truncate font-semibold text-zinc-200">
                ${this.escape(a.raw_album_display)} <span class="text-zinc-500 font-normal">→</span> <span class="text-emerald-400">${this.escape(a.canonical_album)}</span>
              </div>
              <div class="text-[10px] text-zinc-500 truncate">${this.escape(a.artist_norm || "All Artists")}</div>
            </div>
            <button
              data-delete-alias="${a.id}"
              class="ml-btn-icon w-6 h-6 text-xs hover:!bg-red-950/80 hover:!text-red-300"
              title="Delete alias rule">
              ✕
            </button>
          </div>
        `
        )
        .join("");

      box.querySelectorAll("[data-delete-alias]").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.deleteAlbumAlias(Number(btn.dataset.deleteAlias));
        });
      });
    } catch (err) {
      box.innerHTML = `<div class="text-xs text-red-400 p-2">Failed loading aliases: ${err.message}</div>`;
    }
  }

  async saveDownloadPreferences() {
    const method = document.getElementById("set-preferred-download-method")?.value || "auto";
    const bitrate = Number(document.getElementById("set-default-bitrate")?.value || 320);
    try {
      await api.request("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          preferred_download_method: method,
          default_bitrate_kbps: bitrate,
        }),
      });
      const albMethod = document.getElementById("dl-album-method");
      if (albMethod) albMethod.value = method;
      const singleMethod = document.getElementById("dl-single-method");
      if (singleMethod) singleMethod.value = method;
      const singleBitrate = document.getElementById("dl-single-bitrate");
      if (singleBitrate) singleBitrate.value = String(bitrate);
      const extMethod = document.getElementById("ext-method");
      if (extMethod) extMethod.value = method;
      const extBitrate = document.getElementById("ext-bitrate");
      if (extBitrate) extBitrate.value = String(bitrate);
      this.showToast("Download & audio engine preferences saved.");
    } catch (err) {
      this.showToast(`Failed saving download preferences: ${err.message}`, "error");
    }
  }

  // ---- Customizable Playlists ----

  async loadPlaylists() {
    try {
      const res = await api.request("/api/playlists");
      this.playlists = res.playlists || [];
      const badge = document.getElementById("playlist-summary-badge");
      if (badge) badge.textContent = `${this.playlists.length} Playlist(s)`;
      this.renderPlaylistsList();
      if (this.activePlaylist) {
        const stillExists = this.playlists.find((p) => p.id === this.activePlaylist.id);
        if (stillExists) {
          this.selectPlaylist(stillExists.id);
        } else {
          this.activePlaylist = null;
          this.renderPlaylistDetail(null);
        }
      }
    } catch (err) {
      this.showToast(`Failed loading playlists: ${err.message}`, "error");
    }
  }

  renderPlaylistsList() {
    const container = document.getElementById("playlists-list-container");
    if (!container) return;
    if (!this.playlists.length) {
      container.innerHTML = `
        <div class="p-6 text-center text-zinc-500 text-xs">
          No custom playlists yet.<br/>Click <b>+ New Playlist</b> or <b>+ Save Playlist</b> from the 3-Layer Selector to create one!
        </div>`;
      return;
    }

    container.innerHTML = this.playlists
      .map((pl) => {
        const isSelected = this.activePlaylist && this.activePlaylist.id === pl.id;
        return `
          <div
            data-select-playlist="${pl.id}"
            class="flex items-center justify-between p-2.5 rounded-xl border text-xs cursor-pointer transition ${
              isSelected
                ? "bg-emerald-500/15 border-emerald-500/50 text-white font-semibold shadow"
                : "bg-zinc-900/60 border-zinc-800 text-zinc-300 hover:bg-zinc-800 hover:border-zinc-700"
            }">
            <div class="min-w-0 pr-2">
              <div class="truncate text-sm font-semibold">${this.escape(pl.name)}</div>
              <div class="text-[11px] text-zinc-400 truncate mt-0.5">${pl.track_count || 0} track(s)</div>
            </div>
            <span class="text-zinc-500 text-xs">➔</span>
          </div>
        `;
      })
      .join("");

    container.querySelectorAll("[data-select-playlist]").forEach((el) => {
      el.addEventListener("click", () => {
        this.selectPlaylist(Number(el.dataset.selectPlaylist));
      });
    });
  }

  async selectPlaylist(playlistId) {
    try {
      const pl = await api.request(`/api/playlists/${playlistId}`);
      this.activePlaylist = pl;
      this.renderPlaylistsList();
      this.renderPlaylistDetail(pl);
    } catch (err) {
      this.showToast(`Failed loading playlist: ${err.message}`, "error");
    }
  }

  renderPlaylistDetail(pl) {
    const emptyEl = document.getElementById("playlist-detail-empty");
    const contentEl = document.getElementById("playlist-detail-content");
    if (!pl) {
      if (emptyEl) emptyEl.classList.remove("hidden");
      if (contentEl) contentEl.classList.add("hidden");
      return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");
    if (contentEl) contentEl.classList.remove("hidden");

    const titleEl = document.getElementById("pl-detail-title");
    const descEl = document.getElementById("pl-detail-desc");
    const countEl = document.getElementById("pl-detail-count");
    const tracksContainer = document.getElementById("pl-tracks-container");

    if (titleEl) titleEl.textContent = pl.name;
    if (descEl) descEl.textContent = pl.description || "No description";
    if (countEl) countEl.textContent = `${(pl.tracks || []).length} track(s)`;

    if (!tracksContainer) return;
    const tracks = pl.tracks || [];
    if (!tracks.length) {
      tracksContainer.innerHTML = `
        <div class="p-8 text-center text-zinc-500 text-xs">
          This playlist is currently empty.<br/>Add tracks from the 3-Layer Selector using "+ Save Playlist".
        </div>`;
      return;
    }

    tracksContainer.innerHTML = tracks
      .map((t, idx) => {
        const coverSrc = t.cover_url ? api.resolveUrl(t.cover_url) : "";
        const dur = this.formatDuration(t.duration);
        const isOffline = this.offlineIds.has(t.id);
        return `
          <div class="flex items-center justify-between p-2 rounded-xl bg-zinc-900/40 border border-zinc-800/60 hover:bg-zinc-800/60 transition text-xs">
            <div class="flex items-center gap-2.5 min-w-0 flex-1 cursor-pointer" data-pl-track-play="${idx}">
              <span class="text-[11px] font-mono text-zinc-500 w-5 text-right shrink-0">${idx + 1}</span>
              <div class="w-8 h-8 rounded-lg bg-zinc-800 overflow-hidden shrink-0 border border-zinc-700/60 flex items-center justify-center">
                ${coverSrc ? `<img src="${coverSrc}" class="w-full h-full object-cover" alt="cover"/>` : `<span class="text-emerald-400 font-bold text-xs">♪</span>`}
              </div>
              <div class="min-w-0 flex-1">
                <div class="font-semibold text-zinc-200 truncate">${this.escape(t.title)}</div>
                <div class="text-[11px] text-zinc-400 truncate">${this.escape(t.artist_display)} • ${this.escape(t.album)}</div>
              </div>
            </div>
            <div class="flex items-center gap-2 shrink-0 ml-2">
              <span class="text-[10px] font-mono text-zinc-500 hidden sm:inline">${dur}</span>
              <button
                data-pl-track-cache="${t.id}"
                class="ml-btn ml-btn-secondary px-2 py-0.5 text-[10px] flex items-center gap-1 ${isOffline ? "text-emerald-400 font-bold" : "text-zinc-400"}"
                title="Save track on device storage">
                ${isOffline
                  ? `<svg class="w-3 h-3 stroke-current fill-none text-emerald-400" viewBox="0 0 24 24" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span>Saved</span>`
                  : `<svg class="w-3 h-3 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>Device</span>`}
              </button>
              <button
                data-pl-track-remove="${t.id}"
                class="ml-btn-icon w-6 h-6 text-xs flex items-center justify-center hover:!bg-red-950 hover:!text-red-300"
                title="Remove from playlist">
                <svg class="w-3 h-3 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    tracksContainer.querySelectorAll("[data-pl-track-play]").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = Number(el.dataset.plTrackPlay);
        this.playQueue = [...(pl.tracks || [])];
        this.playByIndex(idx);
      });
    });

    tracksContainer.querySelectorAll("[data-pl-track-cache]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const trkId = Number(btn.dataset.plTrackCache);
        const trk = (pl.tracks || []).find((x) => x.id === trkId);
        if (trk) await this.toggleOfflineTrack(trk, btn);
      });
    });

    tracksContainer.querySelectorAll("[data-pl-track-remove]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const trkId = Number(btn.dataset.plTrackRemove);
        await this.removeTrackFromPlaylist(pl.id, trkId);
      });
    });
  }

  openCreatePlaylistModal() {
    const modal = document.getElementById("playlist-edit-modal");
    const heading = document.getElementById("playlist-modal-heading");
    const idEl = document.getElementById("playlist-edit-id");
    const nameEl = document.getElementById("playlist-input-name");
    const descEl = document.getElementById("playlist-input-desc");

    if (heading) heading.textContent = "Create New Playlist";
    if (idEl) idEl.value = "";
    if (nameEl) nameEl.value = "";
    if (descEl) descEl.value = "";
    if (modal) modal.classList.remove("hidden");
  }

  openEditPlaylistModal() {
    if (!this.activePlaylist) return;
    const modal = document.getElementById("playlist-edit-modal");
    const heading = document.getElementById("playlist-modal-heading");
    const idEl = document.getElementById("playlist-edit-id");
    const nameEl = document.getElementById("playlist-input-name");
    const descEl = document.getElementById("playlist-input-desc");

    if (heading) heading.textContent = "Edit Playlist Details";
    if (idEl) idEl.value = String(this.activePlaylist.id);
    if (nameEl) nameEl.value = this.activePlaylist.name;
    if (descEl) descEl.value = this.activePlaylist.description || "";
    if (modal) modal.classList.remove("hidden");
  }

  async savePlaylistSubmit() {
    const idVal = document.getElementById("playlist-edit-id")?.value;
    const nameVal = document.getElementById("playlist-input-name")?.value.trim();
    const descVal = document.getElementById("playlist-input-desc")?.value.trim() || "";

    if (!nameVal) {
      this.showToast("Please enter a playlist name.", "warning");
      return;
    }

    try {
      if (idVal) {
        const updated = await api.request(`/api/playlists/${idVal}`, {
          method: "PUT",
          body: JSON.stringify({ name: nameVal, description: descVal }),
        });
        this.showToast(`Updated playlist "${updated.name}"`);
        document.getElementById("playlist-edit-modal")?.classList.add("hidden");
        await this.loadPlaylists();
        this.selectPlaylist(Number(idVal));
      } else {
        const created = await api.request("/api/playlists", {
          method: "POST",
          body: JSON.stringify({ name: nameVal, description: descVal }),
        });
        this.showToast(`Created playlist "${created.name}"`);
        document.getElementById("playlist-edit-modal")?.classList.add("hidden");
        await this.loadPlaylists();
        this.selectPlaylist(created.id);
      }
    } catch (err) {
      this.showToast(`Failed saving playlist: ${err.message}`, "error");
    }
  }

  async deleteActivePlaylist() {
    if (!this.activePlaylist) return;
    const pl = this.activePlaylist;
    if (!confirm(`Delete playlist "${pl.name}"? (Your music files on disk will not be deleted)`)) {
      return;
    }
    try {
      await api.request(`/api/playlists/${pl.id}`, { method: "DELETE" });
      this.showToast(`Deleted playlist "${pl.name}"`);
      this.activePlaylist = null;
      await this.loadPlaylists();
      this.renderPlaylistDetail(null);
    } catch (err) {
      this.showToast(`Failed deleting playlist: ${err.message}`, "error");
    }
  }

  async removeTrackFromPlaylist(playlistId, trackId) {
    try {
      const updated = await api.request(`/api/playlists/${playlistId}/tracks/${trackId}`, {
        method: "DELETE",
      });
      this.showToast("Track removed from playlist.");
      this.activePlaylist = updated;
      this.renderPlaylistDetail(updated);
      this.loadPlaylists();
    } catch (err) {
      this.showToast(`Failed removing track: ${err.message}`, "error");
    }
  }

  playActivePlaylist(shuffle = false) {
    if (!this.activePlaylist || !(this.activePlaylist.tracks || []).length) {
      this.showToast("Current playlist has no tracks to play.", "warning");
      return;
    }
    let tracks = [...this.activePlaylist.tracks];
    if (shuffle) {
      tracks = [...tracks].sort(() => Math.random() - 0.5);
    }
    this.playQueue = tracks;
    this.playByIndex(0);
    this.showToast(`Playing playlist "${this.activePlaylist.name}" (${tracks.length} tracks)`);
  }

  async saveLayerSelectionAsPlaylist() {
    const selected = this.getLayerSelectedTracks();
    if (!selected.length) {
      this.showToast("No tracks selected in 3-Layer Selector. Select artists/albums first.", "warning");
      return;
    }
    const defaultName = this.layerSelectedArtists.size === 1
      ? `${Array.from(this.layerSelectedArtists)[0]} Mix`
      : `Custom Selection (${selected.length} tracks)`;

    const plName = prompt("Enter a name for this new playlist:", defaultName);
    if (!plName || !plName.trim()) return;

    try {
      const res = await api.request("/api/playlists", {
        method: "POST",
        body: JSON.stringify({
          name: plName.trim(),
          description: `Created from 3-Layer Selector with ${selected.length} tracks`,
          track_ids: selected.map((t) => t.id),
        }),
      });
      this.showToast(`Saved playlist "${res.name}" with ${selected.length} tracks!`);
      this.switchLibraryViewMode("playlists");
      await this.loadPlaylists();
      this.selectPlaylist(res.id);
    } catch (err) {
      this.showToast(`Failed saving playlist: ${err.message}`, "error");
    }
  }

  async cacheActivePlaylistOffline() {
    if (!this.activePlaylist || !(this.activePlaylist.tracks || []).length) {
      this.showToast("Playlist has no tracks to cache.", "warning");
      return;
    }
    const tracks = this.activePlaylist.tracks;
    const btn = document.getElementById("btn-pl-cache-offline");
    if (btn) {
      btn.disabled = true;
      btn.textContent = `Saving 0/${tracks.length}...`;
    }
    try {
      await offlineStore.saveBatchTracksForOffline(tracks, api, (done, total) => {
        if (btn) btn.textContent = `Saving ${done}/${total}...`;
      });
      await this.refreshOfflineState();
      this.showToast(`Saved ${tracks.length} track(s) directly to device storage!`);
      this.renderPlaylistDetail(this.activePlaylist);
    } catch (err) {
      this.showToast(`Device caching failed: ${err.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<svg class="w-3.5 h-3.5 stroke-current fill-none inline mr-1" viewBox="0 0 24 24" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>Save on Device</span>`;
      }
    }
  }

  // ---- Big Player & Synced Karaoke View ----

  toggleBigPlayer(show = true) {
    this.bigPlayerOpen = Boolean(show);
    const modal = document.getElementById("big-player-modal");
    if (modal) {
      modal.classList.toggle("hidden", !this.bigPlayerOpen);
    }
    if (this.bigPlayerOpen) {
      const currentTrack = this.playQueue[this.currentIndex] || this.tracks[0];
      this.updateNowPlayingBar(currentTrack);
      this.updatePlayButtonUI(!this.engine.audio.paused);
      if (currentTrack) {
        this.renderLyrics();
      }
    }
  }

  // ---- Tracklist & Queue Export (TXT, M3U, JSON) ----

  openExportModal(defaultSource = "funnel") {
    this.exportSource = defaultSource;
    const modal = document.getElementById("export-list-modal");
    const sourceSelect = document.getElementById("export-source-select");
    if (sourceSelect) sourceSelect.value = defaultSource;
    this.setExportFormat(this.exportFormat || "m3u");
    if (modal) modal.classList.remove("hidden");
  }

  setExportFormat(fmt) {
    this.exportFormat = fmt;
    const btnM3u = document.getElementById("btn-fmt-m3u");
    const btnTxt = document.getElementById("btn-fmt-txt");
    const btnJson = document.getElementById("btn-fmt-json");
    const activeClass = "export-fmt-btn border border-emerald-500 bg-emerald-500/10 text-emerald-300 rounded-xl p-2.5 text-center text-xs font-bold";
    const inactiveClass = "export-fmt-btn border border-zinc-700 bg-zinc-950 text-zinc-300 rounded-xl p-2.5 text-center text-xs font-bold";

    if (btnM3u) btnM3u.className = fmt === "m3u" ? activeClass : inactiveClass;
    if (btnTxt) btnTxt.className = fmt === "txt" ? activeClass : inactiveClass;
    if (btnJson) btnJson.className = fmt === "json" ? activeClass : inactiveClass;
  }

  downloadExportFile() {
    const source = document.getElementById("export-source-select")?.value || this.exportSource || "funnel";
    let tracks = [];
    if (source === "funnel") {
      tracks = this.getLayerSelectedTracks();
    } else if (source === "playlist") {
      tracks = this.activePlaylist?.tracks || [];
    } else {
      tracks = this.tracks.length ? this.tracks : this.allTracks;
    }

    if (!tracks.length) {
      this.showToast("No tracks to export from the selected source.", "warning");
      return;
    }

    const baseName = document.getElementById("export-filename-input")?.value.trim() || "musiclinker_tracks";
    const fmt = this.exportFormat || "m3u";
    let content = "";
    let mimeType = "text/plain";
    let filename = `${baseName}.${fmt}`;

    if (fmt === "m3u") {
      mimeType = "audio/x-mpegurl";
      content = "#EXTM3U\n" + tracks.map((t) => {
        const dur = t.duration || -1;
        const info = `${t.artist_display || "Unknown"} - ${t.title || "Unknown"}`;
        const path = t.file_path || `${info}.mp3`;
        return `#EXTINF:${dur},${info}\n${path}`;
      }).join("\n");
    } else if (fmt === "txt") {
      mimeType = "text/plain";
      content = tracks.map((t) => `${t.artist_display || "Unknown"} - ${t.title || "Unknown"}`).join("\n");
    } else if (fmt === "json") {
      mimeType = "application/json";
      content = JSON.stringify({
        exported_at: new Date().toISOString(),
        source,
        track_count: tracks.length,
        tracks: tracks.map((t) => ({
          title: t.title,
          artist: t.artist_display,
          album: t.album,
          duration: t.duration,
          year: t.release_year || t.original_year,
          file_path: t.file_path,
        })),
      }, null, 2);
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    document.getElementById("export-list-modal")?.classList.add("hidden");
    this.showToast(`Exported ${tracks.length} track(s) as ${filename}!`);
  }

  // ---- Playback & Isolated Synced/Rough Lyrics ----

  async toggleOfflineTrack(track, btnEl) {
    if (!track) return;
    btnEl.disabled = true;
    btnEl.textContent = "...";
    try {
      if (this.offlineIds.has(track.id)) {
        await offlineStore.removeOfflineTrack(track.id);
        this.showToast(`Removed "${track.title}" from offline cache.`);
      } else {
        await offlineStore.saveTrackForOffline(track, api);
        this.showToast(`Saved "${track.title}" for offline playback.`);
      }
      await this.refreshOfflineState();
      this.renderTrackList();
    } catch (err) {
      this.showToast(`Offline cache error: ${err.message}`, "error");
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
          const targetTrack = res.transition.next_track;
          let foundIdx = this.playQueue.findIndex((t) => t.id === targetTrack.id);
          if (foundIdx === -1) {
            this.playQueue.splice(this.currentIndex + 1, 0, targetTrack);
            foundIdx = this.currentIndex + 1;
          }
          await this.playByIndex(foundIdx);
          return;
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
      const res = await api.request("/api/player/scrobble", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (res && res.status === "scrobbled") {
        this.showScrobbleToast(track.title, track.artist_display);
      }
    } catch (_err) {
      await offlineStore.queueOfflineScrobble(payload);
    }
  }

  showScrobbleToast(title, artist) {
    const existing = document.querySelector(".lastfm-scrobble-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.className = "lastfm-scrobble-toast text-xs";
    toast.innerHTML = `
      <span class="w-4 h-4 rounded-full bg-red-600 flex items-center justify-center text-[10px] font-black text-white shrink-0">l</span>
      <span class="text-zinc-200">Scrobbled to Last.fm: <strong class="text-white">${this.escape(title)}</strong> - <span class="text-zinc-400">${this.escape(artist)}</span></span>
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.remove();
    }, 4000);
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
    const bigBox = document.getElementById("big-lyrics-container");
    const bigStatus = document.getElementById("big-lyrics-status");
    if (!box) return;

    const mode = this.lyricsData.sync_mode || "none";
    if (badge) {
      if (mode === "exact_lrc") {
        badge.className = "px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40";
        badge.textContent = "✓ Exact LRC Sync (LRCLIB)";
      } else if (mode === "youtube_captions") {
        badge.className = "px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40";
        badge.textContent = "≈ Rough Sync (YouTube Stream Captions)";
      } else if (mode === "rough_interpolated") {
        badge.className = "px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40";
        badge.textContent = "≈ Rough Sync (Estimated Position)";
      } else {
        badge.className = "px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-zinc-800 text-zinc-400";
        badge.textContent = "No Synced Lyrics";
      }
    }
    if (bigStatus) {
      bigStatus.textContent = mode === "exact_lrc" ? "Exact LRC Synced (LRCLIB)" : mode === "youtube_captions" || mode === "rough_interpolated" ? "Rough Synced" : "No Synced Lyrics";
    }

    if (!this.lyricsData.lines?.length) {
      box.innerHTML = `<p class="text-zinc-500 text-sm italic py-10">No lyrics found for this track.</p>`;
      if (bigBox) bigBox.innerHTML = `<p class="text-zinc-600 text-base italic py-16">No synchronized lyrics loaded.</p>`;
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

    if (bigBox) {
      bigBox.innerHTML = this.lyricsData.lines
        .map((l, idx) => {
          const tsLabel = l.time !== null && l.time !== undefined ? this.formatTime(l.time) : "";
          return `
            <div
              id="big-lyric-line-${idx}"
              data-big-lyric-time="${l.time ?? 0}"
              class="big-lyric-line cursor-pointer text-zinc-500 hover:text-zinc-300 text-lg sm:text-2xl font-bold py-2 px-4 rounded-2xl transition duration-200">
              <span>${this.escape(l.text)}</span>
              ${tsLabel ? `<span class="block text-[11px] font-mono text-zinc-600 font-normal mt-0.5">${tsLabel}</span>` : ""}
            </div>
          `;
        })
        .join("");

      bigBox.querySelectorAll("[data-big-lyric-time]").forEach((el) => {
        el.addEventListener("click", () => {
          const t = Number(el.dataset.bigLyricTime || 0);
          this.engine.seekToSeconds(Math.max(0, t - this.lyricOffsetSec));
        });
      });
    }

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
    const bigSeekEl = document.getElementById("big-seek-bar");
    const bigCurEl = document.getElementById("big-time-current");
    const bigTotEl = document.getElementById("big-time-total");

    if (seekEl && dur > 0) {
      seekEl.value = String(Math.round((cur / dur) * 1000));
      this.updateSliderFill(seekEl);
    }
    if (timeEl) {
      timeEl.textContent = `${this.formatTime(cur)} / ${this.formatTime(dur)}`;
    }
    if (bigSeekEl && dur > 0) {
      bigSeekEl.value = String(Math.round((cur / dur) * 1000));
      this.updateSliderFill(bigSeekEl);
    }
    if (bigCurEl) bigCurEl.textContent = this.formatTime(cur);
    if (bigTotEl) bigTotEl.textContent = this.formatTime(dur);

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

    // Big lyrics active line
    const prevBigEl = document.getElementById(`big-lyric-line-${this.lastActiveLyricIdx}`);
    if (prevBigEl) {
      prevBigEl.classList.remove("text-emerald-300", "scale-105", "bg-emerald-500/10");
      prevBigEl.classList.add("text-zinc-500");
    }
    const activeBigEl = document.getElementById(`big-lyric-line-${activeIdx}`);
    const bigBox = document.getElementById("big-lyrics-container");
    if (activeBigEl) {
      activeBigEl.classList.remove("text-zinc-500");
      activeBigEl.classList.add("text-emerald-300", "scale-105", "bg-emerald-500/10");
      if (bigBox && Date.now() > this.userScrollLockUntil) {
        const targetScroll = activeBigEl.offsetTop - bigBox.clientHeight / 2 + activeBigEl.clientHeight / 2;
        bigBox.scrollTo({ top: Math.max(0, targetScroll), behavior: "smooth" });
      }
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

  async adjustLyricOffset(deltaSec) {
    const track = this.playQueue[this.currentIndex];
    if (!track) {
      this.lyricOffsetSec = Math.round((this.lyricOffsetSec + deltaSec) * 10) / 10;
      this.updateLyricOffsetLabel();
      this.lastActiveLyricIdx = -1;
      return;
    }

    try {
      const res = await api.request(`/api/tracks/${track.id}/lyrics/offset`, {
        method: "POST",
        body: JSON.stringify({ delta_seconds: deltaSec }),
      });
      if (res && res.lyrics) {
        this.lyricsData = res.lyrics;
        this.lyricOffsetSec = 0.0;
        this.updateLyricOffsetLabel();
        this.lastActiveLyricIdx = -1;
        this.renderLyrics();
        // Update offline cached copy if track is saved in offlineStore
        const offlineRec = await offlineStore.getOfflineTrackRecord(track.id);
        if (offlineRec) {
          await offlineStore.saveTrackForOffline({ ...track, offline_lyrics: res.lyrics }, api).catch(() => {});
        }
        this.flashLyricSavedStatus("Saved permanently ✓");
      }
    } catch (_err) {
      // In offline APK mode or fallback, apply in-memory offset
      this.lyricOffsetSec = Math.round((this.lyricOffsetSec + deltaSec) * 10) / 10;
      this.updateLyricOffsetLabel();
      this.lastActiveLyricIdx = -1;
      this.flashLyricSavedStatus("Offset applied (local)");
    }
  }

  flashLyricSavedStatus(text = "Saved permanently ✓") {
    ["lyric-saved-status", "big-lyric-saved-status"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.textContent = text;
        el.classList.remove("hidden");
        setTimeout(() => el.classList.add("hidden"), 3000);
      }
    });
  }

  async saveCurrentLyricOffsetPermanently() {
    const track = this.playQueue[this.currentIndex];
    if (!track) return;
    if (this.lyricOffsetSec !== 0) {
      await this.adjustLyricOffset(this.lyricOffsetSec);
    } else {
      this.flashLyricSavedStatus("Saved permanently ✓");
    }
  }

  updateLyricOffsetLabel() {
    const sign = this.lyricOffsetSec >= 0 ? "+" : "";
    const text = `${sign}${this.lyricOffsetSec.toFixed(1)}s`;
    const el = document.getElementById("lyric-offset-label");
    const bigEl = document.getElementById("big-lyric-offset-label");
    if (el) el.textContent = text;
    if (bigEl) bigEl.textContent = text;
  }

  updateNowPlayingBar(track) {
    const titleEl = document.getElementById("np-title");
    const artistEl = document.getElementById("np-artist");
    const coverEl = document.getElementById("np-cover");
    const carTitle = document.getElementById("car-title");
    const carArtist = document.getElementById("car-artist");

    const bigTitle = document.getElementById("big-title");
    const bigArtist = document.getElementById("big-artist");
    const bigAlbum = document.getElementById("big-album");
    const bigCover = document.getElementById("big-cover");
    const bigYear = document.getElementById("big-meta-year");
    const bigBitrate = document.getElementById("big-meta-bitrate");
    const bigSync = document.getElementById("big-meta-sync");
    const bigOffline = document.getElementById("big-offline-badge");

    if (!track) {
      if (titleEl) titleEl.textContent = "Not Playing";
      if (artistEl) artistEl.textContent = "Select a track or album";
      if (coverEl) coverEl.src = "/icon.svg";
      if (carTitle) carTitle.textContent = "Not Playing";
      if (carArtist) carArtist.textContent = "—";
      if (bigTitle) bigTitle.textContent = "Not Playing";
      if (bigArtist) bigArtist.textContent = "Select a track or album";
      if (bigAlbum) bigAlbum.textContent = "—";
      if (bigCover) bigCover.src = "/icon.svg";
      if (bigOffline) bigOffline.classList.add("hidden");
      return;
    }

    const numPrefix = track.track_number > 0 ? `#${String(track.track_number).padStart(2, "0")} ` : "";
    if (titleEl) titleEl.textContent = `${numPrefix}${track.title}`;
    if (artistEl) artistEl.textContent = `${track.artist_display} • ${track.album}`;
    if (coverEl) {
      coverEl.src = track.cover_url ? api.resolveUrl(track.cover_url) : "/icon.svg";
    }
    if (carTitle) carTitle.textContent = track.title;
    if (carArtist) carArtist.textContent = track.artist_display;

    if (bigTitle) bigTitle.textContent = `${numPrefix}${track.title}`;
    if (bigArtist) bigArtist.textContent = track.artist_display;
    if (bigAlbum) bigAlbum.textContent = track.album || "Unknown Album";
    if (bigCover) bigCover.src = track.cover_url ? api.resolveUrl(track.cover_url) : "/icon.svg";
    if (bigYear) bigYear.textContent = track.original_year || track.release_year || "Unknown Year";
    if (bigBitrate) bigBitrate.textContent = `${track.bitrate_kbps || 320} kbps`;
    if (bigSync) {
      bigSync.textContent = track.lyric_sync_mode === "exact_lrc" ? "Exact LRC Synced" : track.lyric_sync_mode === "youtube_captions" || track.lyric_sync_mode === "rough_interpolated" ? "Rough Synced" : "Unsynced";
    }
    if (bigOffline) {
      bigOffline.classList.toggle("hidden", !this.offlineIds.has(track.id));
    }
  }

  updatePlayButtonUI(playing) {
    const btn = document.getElementById("btn-play");
    const carBtn = document.getElementById("car-btn-play");
    const bigBtn = document.getElementById("btn-big-play");

    if (btn) {
      btn.innerHTML = playing
        ? `<svg class="w-6 h-6 fill-current" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`
        : `<svg class="w-6 h-6 fill-current ml-0.5" viewBox="0 0 24 24"><polygon points="6 4 20 12 6 20 6 4"/></svg>`;
      btn.title = playing ? "Pause" : "Play";
    }
    if (bigBtn) {
      bigBtn.innerHTML = playing
        ? `<svg class="w-9 h-9 fill-current" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`
        : `<svg class="w-9 h-9 fill-current ml-1" viewBox="0 0 24 24"><polygon points="6 4 20 12 6 20 6 4"/></svg>`;
      bigBtn.title = playing ? "Pause" : "Play";
    }
    if (carBtn) {
      carBtn.innerHTML = playing
        ? `<svg class="w-8 h-8 fill-current" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg><span>PAUSE</span>`
        : `<svg class="w-8 h-8 fill-current ml-1" viewBox="0 0 24 24"><polygon points="6 4 20 12 6 20 6 4"/></svg><span>PLAY</span>`;
    }
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
          ? "ml-btn px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-zinc-950 shadow-sm"
          : "ml-btn px-2.5 py-1.5 rounded-xl text-xs font-medium text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/70";
      }
    });
  }

  // ---- Standalone Mobile UX & Navigation ----

  initMobileNavigation() {
    this.switchMobileView("library");
    const navItems = [
      { id: "nav-btn-library", view: "library" },
      { id: "nav-btn-downloader", view: "downloader" },
      { id: "nav-btn-lyrics", view: "lyrics" },
      { id: "nav-btn-studio", view: "studio" },
      { id: "nav-btn-settings", view: "settings" },
    ];
    navItems.forEach(({ id, view }) => {
      document.getElementById(id)?.addEventListener("click", () => this.switchMobileView(view));
    });

    // Also header connection pill
    document.getElementById("header-conn-status")?.addEventListener("click", () => this.openSyncModal());
  }

  switchMobileView(viewName) {
    this.mobileView = viewName;
    document.body.dataset.mobileView = viewName;

    const navMap = {
      library: "nav-btn-library",
      downloader: "nav-btn-downloader",
      lyrics: "nav-btn-lyrics",
      studio: "nav-btn-studio",
      settings: "nav-btn-settings",
    };

    Object.entries(navMap).forEach(([v, id]) => {
      const btn = document.getElementById(id);
      if (btn) {
        btn.classList.toggle("active", v === viewName);
        btn.classList.toggle("text-emerald-400", v === viewName);
        btn.classList.toggle("text-zinc-400", v !== viewName);
      }
    });

    // Synchronize right-side desktop tab panel with mobile selection
    if (viewName === "downloader") {
      this.switchStudioTab("extractor");
    } else if (viewName === "lyrics") {
      this.switchStudioTab("lyrics");
    } else if (viewName === "studio") {
      this.switchStudioTab("curator");
    } else if (viewName === "settings") {
      this.switchStudioTab("settings");
    }
  }

  async checkServerConnection() {
    const pill = document.getElementById("header-conn-status");
    const label = document.getElementById("header-conn-label");
    const dot = document.getElementById("header-conn-dot");
    try {
      const info = await api.request("/api/sync/info");
      if (pill) {
        pill.className = "ml-btn px-2.5 py-1.5 text-xs rounded-xl border border-emerald-500/40 bg-emerald-500/15 text-emerald-400 flex items-center gap-1.5 transition cursor-pointer";
      }
      if (label) label.textContent = "Online";
      if (dot) dot.className = "w-2 h-2 rounded-full bg-emerald-400";
    } catch (_err) {
      if (pill) {
        pill.className = "ml-btn px-2.5 py-1.5 text-xs rounded-xl border border-amber-500/50 bg-amber-500/15 text-amber-300 flex items-center gap-1.5 transition cursor-pointer";
      }
      if (label) label.textContent = api.isCapacitor() ? "Connect PC" : "Offline";
      if (dot) dot.className = "w-2 h-2 rounded-full bg-amber-400 animate-ping";
    }
  }

  // ---- Multi-Select & Bulk Album Grouping ----

  toggleMultiSelectMode() {
    this.isMultiSelectActive = !this.isMultiSelectActive;
    const btn = document.getElementById("btn-toggle-multi-select");
    const bar = document.getElementById("multi-select-bar");
    if (btn) {
      btn.className = this.isMultiSelectActive
        ? "ml-btn px-2.5 py-1.5 text-xs font-semibold bg-emerald-500 text-zinc-950 flex items-center gap-1.5"
        : "ml-btn ml-btn-secondary px-2.5 py-1.5 text-xs flex items-center gap-1.5";
    }
    if (bar) {
      bar.classList.toggle("hidden", !this.isMultiSelectActive);
    }
    if (!this.isMultiSelectActive) {
      this.selectedTrackIds.clear();
    }
    this.updateMultiSelectBar();
    this.renderTrackList();
  }

  ensureMultiSelectActive() {
    if (this.isMultiSelectActive) return;
    this.isMultiSelectActive = true;
    const btn = document.getElementById("btn-toggle-multi-select");
    const bar = document.getElementById("multi-select-bar");
    if (btn) {
      btn.className = "ml-btn px-2.5 py-1.5 text-xs font-semibold bg-emerald-500 text-zinc-950 flex items-center gap-1.5 shadow-sm";
    }
    if (bar) {
      bar.classList.remove("hidden");
    }
    document.querySelectorAll(".track-select-checkbox").forEach((cb) => {
      cb.classList.remove("hidden");
    });
  }

  handleCtrlClickSelection(idx) {
    const track = this.tracks[idx];
    if (!track) return;
    this.ensureMultiSelectActive();
    const willSelect = !this.selectedTrackIds.has(track.id);
    if (willSelect) {
      this.selectedTrackIds.add(track.id);
    } else {
      this.selectedTrackIds.delete(track.id);
    }
    this.lastSelectedTrackIdx = idx;
    this.updateMultiSelectBar();

    // Immediate DOM feedback
    const rowEl = document.querySelector(`[data-track-row-id="${track.id}"]`);
    if (rowEl) {
      rowEl.classList.toggle("is-selected", willSelect);
      rowEl.classList.toggle("bg-emerald-950/60", willSelect);
      rowEl.classList.toggle("border-emerald-500/70", willSelect);
      rowEl.classList.add("selection-pulse");
      setTimeout(() => rowEl.classList.remove("selection-pulse"), 200);
      const cb = rowEl.querySelector(`[data-select-id="${track.id}"]`);
      if (cb) cb.checked = willSelect;
    }
  }

  handleShiftClickSelection(idx) {
    const track = this.tracks[idx];
    if (!track) return;
    this.ensureMultiSelectActive();

    const startIdx = this.lastSelectedTrackIdx !== null && this.lastSelectedTrackIdx !== undefined
      ? this.lastSelectedTrackIdx
      : 0;
    const endIdx = idx;
    const minIdx = Math.min(startIdx, endIdx);
    const maxIdx = Math.max(startIdx, endIdx);

    for (let i = minIdx; i <= maxIdx; i++) {
      const t = this.tracks[i];
      if (!t) continue;
      this.selectedTrackIds.add(t.id);
      const rowEl = document.querySelector(`[data-track-row-id="${t.id}"]`);
      if (rowEl) {
        rowEl.classList.add("is-selected", "bg-emerald-950/60", "border-emerald-500/70", "selection-pulse");
        setTimeout(() => rowEl.classList.remove("selection-pulse"), 200);
        const cb = rowEl.querySelector(`[data-select-id="${t.id}"]`);
        if (cb) cb.checked = true;
      }
    }

    this.lastSelectedTrackIdx = idx;
    this.updateMultiSelectBar();
  }

  toggleTrackSelection(trackId, isSelected) {
    if (isSelected) {
      this.ensureMultiSelectActive();
      this.selectedTrackIds.add(trackId);
    } else {
      this.selectedTrackIds.delete(trackId);
    }
    this.updateMultiSelectBar();
    const rowEl = document.querySelector(`[data-track-row-id="${trackId}"]`);
    if (rowEl) {
      rowEl.classList.toggle("is-selected", isSelected);
      rowEl.classList.toggle("bg-emerald-950/60", isSelected);
      rowEl.classList.toggle("border-emerald-500/70", isSelected);
      const rowCb = rowEl.querySelector(`[data-select-id="${trackId}"]`);
      if (rowCb) rowCb.checked = isSelected;
    }
  }

  selectAllFilteredTracks() {
    this.tracks.forEach((t) => this.selectedTrackIds.add(t.id));
    this.updateMultiSelectBar();
    this.renderTrackList();
  }

  clearSelectedTracks() {
    this.selectedTrackIds.clear();
    this.updateMultiSelectBar();
    this.renderTrackList();
  }

  updateMultiSelectBar() {
    const countEl = document.getElementById("multi-select-count");
    if (countEl) {
      countEl.textContent = `${this.selectedTrackIds.size} selected`;
    }
  }

  openBulkAlbumModal() {
    if (this.selectedTrackIds.size === 0) {
      this.showToast("Select at least 1 track using checkboxes first.", "warning");
      return;
    }
    const selectedTracks = this.allTracks.filter((t) => this.selectedTrackIds.has(t.id));
    if (!selectedTracks.length) return;

    const modal = document.getElementById("bulk-album-modal");
    const titleInput = document.getElementById("bulk-album-input-title");
    const artistInput = document.getElementById("bulk-album-input-artist");
    const yearInput = document.getElementById("bulk-album-input-year");
    const statusEl = document.getElementById("bulk-album-match-status");

    const sample = selectedTracks[0];
    if (titleInput) titleInput.value = sample.album || "";
    if (artistInput) artistInput.value = sample.album_artist || sample.artist_display || "";
    if (yearInput) yearInput.value = sample.release_year || sample.original_year || "";

    if (statusEl) statusEl.textContent = `${selectedTracks.length} tracks selected`;

    // Render preliminary list of selected tracks
    this.renderBulkAlbumReviewTable(selectedTracks, null);
    if (modal) modal.classList.remove("hidden");
  }

  renderBulkAlbumReviewTable(selectedTracks, matchResult = null) {
    const container = document.getElementById("bulk-album-table-container");
    if (!container) return;

    if (!matchResult) {
      container.innerHTML = `
        <div class="text-xs text-zinc-400 mb-2 font-mono">
          Ready to group ${selectedTracks.length} tracks. Click <b>"Match Official Tracklist"</b> to auto-sort 1..N via Spotify/MusicBrainz, or adjust assignments below.
        </div>
        ${selectedTracks
          .map(
            (t, i) => `
          <div class="flex items-center justify-between p-2 rounded-lg bg-zinc-900/80 border border-zinc-800 text-xs">
            <div class="flex items-center gap-2 min-w-0">
              <span class="font-mono text-zinc-500 w-6 text-center">#${i + 1}</span>
              <span class="text-zinc-200 font-medium truncate">${this.escape(t.title)}</span>
              <span class="text-zinc-500 truncate text-[11px]">— ${this.escape(t.artist_display)}</span>
            </div>
            <input type="number" min="1" max="99" value="${t.track_number || i + 1}" data-assign-track-id="${t.id}" class="w-14 bg-zinc-950 border border-zinc-700 rounded px-1.5 py-0.5 text-center text-xs text-emerald-400 font-mono font-bold" />
          </div>
        `
          )
          .join("")}
      `;
      return;
    }

    const { official_tracks = [], assignments = [] } = matchResult;

    container.innerHTML = `
      <div class="text-xs text-emerald-300 mb-2 font-medium bg-emerald-950/40 border border-emerald-500/30 rounded-lg p-2 flex items-center gap-2">
        <span>✓ Fuzzy match completed! Review the assigned track numbers (1..N) below:</span>
      </div>
      ${assignments
        .map((a) => {
          const conf = Math.round((a.confidence || 0) * 100);
          const confBadgeClass =
            conf >= 80
              ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
              : conf >= 50
              ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
              : "bg-zinc-800 text-zinc-400 border-zinc-700";

          return `
            <div class="flex flex-col sm:flex-row sm:items-center justify-between p-2 rounded-lg bg-zinc-900/90 border border-zinc-800 gap-2 text-xs">
              <div class="min-w-0 flex-1">
                <div class="text-zinc-200 font-semibold truncate">${this.escape(a.current_title)}</div>
                <div class="text-zinc-500 text-[11px] truncate">${this.escape(a.current_artist)}</div>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <span class="text-[10px] font-mono px-1.5 py-0.5 rounded border ${confBadgeClass}">${conf}% match</span>
                <select data-assign-track-id="${a.track_id}" class="bg-zinc-950 border border-zinc-700 rounded px-2 py-1 text-xs text-emerald-400 font-mono font-bold">
                  ${official_tracks
                    .map(
                      (ot) =>
                        `<option value="${ot.track_number}" ${ot.track_number === a.track_number ? "selected" : ""}>#${ot.track_number} ${this.escape(ot.title)}</option>`
                    )
                    .join("")}
                </select>
              </div>
            </div>
          `;
        })
        .join("")}
    `;
  }

  async matchBulkAlbumTracklist() {
    const title = document.getElementById("bulk-album-input-title")?.value.trim();
    const artist = document.getElementById("bulk-album-input-artist")?.value.trim() || "";
    if (!title) {
      this.showToast("Please enter an album title to match.", "warning");
      return;
    }

    const btn = document.getElementById("btn-bulk-compare-official");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>Matching tracklist...</span>`;
    }

    try {
      const trackIds = Array.from(this.selectedTrackIds);
      const res = await api.matchAlbumTracklist(title, artist, trackIds);
      this.bulkMatchData = res;
      this.renderBulkAlbumReviewTable(
        this.allTracks.filter((t) => this.selectedTrackIds.has(t.id)),
        res
      );
      this.showToast(`Found ${res.official_tracks?.length || 0} official track(s) for "${res.album}"`);
    } catch (err) {
      this.showToast(`Failed matching tracklist: ${err.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span>Match Official Tracklist</span>`;
      }
    }
  }

  async submitBulkAssignAlbum() {
    const title = document.getElementById("bulk-album-input-title")?.value.trim();
    const artist = document.getElementById("bulk-album-input-artist")?.value.trim() || "";
    const year = parseInt(document.getElementById("bulk-album-input-year")?.value || "0", 10) || 0;
    if (!title) {
      this.showToast("Album title is required.", "warning");
      return;
    }

    const selects = document.querySelectorAll("[data-assign-track-id]");
    if (!selects.length) {
      this.showToast("No tracks to assign.", "warning");
      return;
    }

    const trackAssignments = [];
    selects.forEach((el) => {
      const trackId = Number(el.dataset.assignTrackId);
      const trackNum = parseInt(el.value, 10) || 1;
      let matchedTitle = "";
      if (this.bulkMatchData?.official_tracks) {
        const match = this.bulkMatchData.official_tracks.find((ot) => ot.track_number === trackNum);
        if (match) matchedTitle = match.title;
      }
      trackAssignments.push({
        track_id: trackId,
        track_number: trackNum,
        title: matchedTitle,
      });
    });

    const submitBtn = document.getElementById("btn-bulk-apply-album-submit");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<span>Updating ID3 tags & moving files...</span>`;
    }

    try {
      const res = await api.bulkAssignAlbum(title, artist, year, null, trackAssignments);
      this.showToast(`Updated ${res.updated_tracks?.length || 0} track(s) into album "${title}"!`);
      document.getElementById("bulk-album-modal")?.classList.add("hidden");
      this.toggleMultiSelectMode();
      await this.loadAlbums();
      await this.loadLibrary("", true);
    } catch (err) {
      this.showToast(`Failed bulk assigning album: ${err.message}`, "error");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><polygon points="20 6 9 17 4 12"/></svg><span>Confirm & Apply to Selected Tracks</span>`;
      }
    }
  }

  async batchDeleteSelectedTracks() {
    if (this.selectedTrackIds.size === 0) return;
    const count = this.selectedTrackIds.size;
    if (!confirm(`Delete ${count} selected track(s) from your library and disk?`)) return;

    try {
      await api.request("/api/tracks/batch-delete", {
        method: "POST",
        body: JSON.stringify({
          track_ids: Array.from(this.selectedTrackIds),
          delete_files: true,
          unmark_hashes: false,
        }),
      });
      this.showToast(`Deleted ${count} track(s)`);
      this.toggleMultiSelectMode();
      await this.loadLibrary("", true);
    } catch (err) {
      this.showToast(`Failed batch deleting: ${err.message}`, "error");
    }
  }

  // ---- Audio Version Swap & 15s Snippet Preview ----

  async openSwapAudioModal(track) {
    this.stopCandidatePreview();
    this.swapCurrentTrack = track;

    const modal = document.getElementById("swap-audio-modal");
    const idInput = document.getElementById("swap-track-id");
    const titleEl = document.getElementById("swap-target-title");
    const artistEl = document.getElementById("swap-target-artist");
    const durEl = document.getElementById("swap-target-duration");
    const searchInput = document.getElementById("swap-search-input");

    if (idInput) idInput.value = track.id;
    if (titleEl) titleEl.textContent = track.title;
    if (artistEl) artistEl.textContent = track.artist_display;
    if (durEl) durEl.textContent = this.formatDuration(track.duration);
    if (searchInput) searchInput.value = `${track.artist_display} ${track.title}`;

    if (modal) modal.classList.remove("hidden");
    await this.queryAudioCandidates(track.id, "");
  }

  async queryAudioCandidates(trackId, customQuery = "") {
    this.stopCandidatePreview();
    const listEl = document.getElementById("swap-candidates-list");
    const statusEl = document.getElementById("swap-status-text");
    const track = this.swapCurrentTrack;

    if (listEl) {
      listEl.innerHTML = `
        <div class="p-8 text-center text-xs text-zinc-400 space-y-2">
          <div class="w-6 h-6 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin mx-auto"></div>
          <div>Searching YouTube for alternative versions${customQuery ? ` matching "${this.escape(customQuery)}"` : ""}...</div>
        </div>
      `;
    }
    if (statusEl) statusEl.textContent = "Querying alternative audio versions...";

    try {
      const res = await api.getTrackCandidates(trackId, customQuery, 8);
      let list = [];
      if (Array.isArray(res)) {
        list = res;
      } else if (Array.isArray(res.candidates)) {
        list = res.candidates;
      } else if (res.candidates && Array.isArray(res.candidates.candidates)) {
        list = res.candidates.candidates;
      }
      this.renderAudioCandidates(track, list);
      if (statusEl) statusEl.textContent = `${list.length} alternative version(s) found`;
    } catch (err) {
      if (listEl) {
        listEl.innerHTML = `<div class="p-6 text-center text-xs text-red-400">Failed searching versions: ${this.escape(err.message)}</div>`;
      }
      if (statusEl) statusEl.textContent = "Search error";
    }
  }

  renderAudioCandidates(currentTrack, candidates) {
    const listEl = document.getElementById("swap-candidates-list");
    if (!listEl) return;

    if (!candidates || !candidates.length) {
      listEl.innerHTML = `
        <div class="p-8 text-center text-xs text-zinc-400 space-y-2">
          <div class="text-zinc-300 font-semibold">No alternative versions found</div>
          <div class="text-zinc-500">Try modifying the search bar above or paste a direct YouTube video URL to swap.</div>
        </div>
      `;
      return;
    }

    listEl.innerHTML = candidates
      .map((c) => {
        const vidId = c.video_id || c.id;
        const channelName = c.channel || c.uploader || "YouTube";
        const albumName = c.album || (currentTrack ? currentTrack.album : "");
        const diffSec = c.duration_diff_sec !== undefined ? c.duration_diff_sec : (c.duration_diff !== undefined ? c.duration_diff : (c.duration - (currentTrack ? currentTrack.duration : 0)));
        const diffText = diffSec === 0 ? "Exact duration" : diffSec > 0 ? `+${diffSec}s` : `${diffSec}s`;
        const diffBadgeClass =
          Math.abs(diffSec) <= 3
            ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
            : Math.abs(diffSec) <= 15
            ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
            : "bg-red-500/20 text-red-300 border-red-500/40";

        const thumbHtml = c.thumbnail
          ? `<img src="${this.escapeAttr(c.thumbnail)}" alt="" class="w-14 h-14 object-cover rounded-xl bg-zinc-800 border border-zinc-700/80 shrink-0" onerror="this.onerror=null; this.src='/icon.svg';" />`
          : `<div class="w-14 h-14 rounded-xl bg-zinc-800 border border-zinc-700/80 shrink-0 flex items-center justify-center text-zinc-500"><svg class="w-6 h-6" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg></div>`;

        return `
          <div class="candidate-card flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs p-3 rounded-2xl bg-zinc-950/60 border border-zinc-800/80 hover:border-zinc-700 transition" data-video-card="${vidId}">
            <div class="flex items-center gap-3 min-w-0 flex-1">
              ${thumbHtml}
              <div class="min-w-0 flex-1">
                <div class="font-bold text-white text-sm truncate leading-snug">${this.escape(c.title)}</div>
                <div class="flex items-center flex-wrap gap-2 text-zinc-400 text-xs mt-1">
                  <span class="text-zinc-300 font-medium">${this.escape(channelName)}</span>
                  ${albumName ? `<span class="px-2 py-0.5 rounded-md bg-zinc-800 border border-zinc-700 text-zinc-300 text-[10px] inline-flex items-center gap-1 font-medium"><svg class="w-3 h-3 text-emerald-400 fill-current" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 14.5c-2.49 0-4.5-2.01-4.5-4.5S9.51 7.5 12 7.5s4.5 2.01 4.5 4.5-2.01 4.5-4.5 4.5zm0-5.5c-.55 0-1 .45-1 1s.45 1 1 1 1-.45 1-1-.45-1-1-1z"/></svg><span class="truncate max-w-[130px]" title="${this.escapeAttr(albumName)}">${this.escape(albumName)}</span></span>` : ""}
                  <span class="text-zinc-600">•</span>
                  <span class="font-mono text-zinc-300">${this.formatDuration(c.duration)}</span>
                  <span class="text-[10px] font-mono px-1.5 py-0.5 rounded border ${diffBadgeClass}">${diffText}</span>
                </div>
              </div>
            </div>
            <div class="flex items-center gap-2 shrink-0 self-end sm:self-center">
              <button
                data-preview-video-id="${vidId}"
                class="ml-btn ml-btn-secondary px-3 py-1.5 text-xs rounded-xl flex items-center gap-1.5 text-emerald-300 hover:text-white"
                title="Play 15-second snippet of this audio start"
              >
                <svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                <span>15s Preview</span>
              </button>
              <button
                data-replace-url="${this.escapeAttr(c.url)}"
                class="ml-btn ml-btn-primary px-3.5 py-1.5 text-xs rounded-xl flex items-center gap-1.5 font-bold"
                title="Swap track audio with this version"
              >
                <span>Select & Swap</span>
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll("[data-preview-video-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const vid = btn.dataset.previewVideoId;
        this.playCandidatePreview(vid, btn);
      });
    });

    listEl.querySelectorAll("[data-replace-url]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const ytUrl = btn.dataset.replaceUrl;
        if (this.swapCurrentTrack) {
          this.confirmReplaceAudio(this.swapCurrentTrack.id, ytUrl, btn);
        }
      });
    });
  }

  playCandidatePreview(videoId, btnEl) {
    if (this.activePreviewVideoId === videoId && this.previewAudio && !this.previewAudio.paused) {
      this.stopCandidatePreview();
      return;
    }

    this.stopCandidatePreview();
    this.activePreviewVideoId = videoId;

    const url = api.getCandidatePreviewUrl(videoId);
    this.previewAudio = new Audio(url);
    this.previewAudio.volume = this.engine.masterGain?.gain.value || 0.85;

    // Temporarily pause main playback so preview is clean
    if (this.engine.isPlaying) {
      this.engine.pause();
    }

    btnEl.innerHTML = `<span class="inline-block w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span><span>Playing...</span>`;
    const card = document.querySelector(`[data-video-card="${videoId}"]`);
    if (card) card.classList.add("is-playing");

    this.previewAudio.play().catch((err) => {
      this.showToast(`Preview failed: ${err.message}`, "error");
      this.stopCandidatePreview();
    });

    this.previewAudio.onended = () => {
      this.stopCandidatePreview();
    };
    this.previewAudio.onerror = () => {
      this.showToast("Could not load 15s snippet preview.", "error");
      this.stopCandidatePreview();
    };
  }

  stopCandidatePreview() {
    if (this.previewAudio) {
      try {
        this.previewAudio.pause();
        this.previewAudio.currentTime = 0;
      } catch (_e) {}
      this.previewAudio = null;
    }
    if (this.activePreviewVideoId) {
      const card = document.querySelector(`[data-video-card="${this.activePreviewVideoId}"]`);
      if (card) card.classList.remove("is-playing");
      const btn = document.querySelector(`[data-preview-video-id="${this.activePreviewVideoId}"]`);
      if (btn) {
        btn.innerHTML = `<svg class="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><polygon points="6 3 20 12 6 21 6 3"/></svg><span>15s Preview</span>`;
      }
      this.activePreviewVideoId = null;
    }
  }

  async confirmReplaceAudio(trackId, youtubeUrl, btnEl) {
    this.stopCandidatePreview();

    const progressContainer = document.getElementById("swap-progress-container");
    const progressFill = document.getElementById("swap-progress-fill");
    const progressPct = document.getElementById("swap-progress-pct");
    const progressDetail = document.getElementById("swap-progress-detail");
    const statusEl = document.getElementById("swap-status-text");
    const floatingPill = document.getElementById("floating-swap-pill");
    const floatingBar = document.getElementById("floating-swap-bar");
    const floatingPct = document.getElementById("floating-swap-pct");
    const floatingTitle = document.getElementById("floating-swap-title");

    const track = this.allTracks.find((t) => t.id === trackId) || this.swapCurrentTrack;
    const trackTitle = track ? track.title : `Track #${trackId}`;

    if (floatingTitle) floatingTitle.textContent = trackTitle;
    if (progressContainer) progressContainer.classList.remove("hidden");
    if (progressFill) progressFill.style.width = "5%";
    if (progressPct) progressPct.textContent = "5%";
    if (progressDetail) progressDetail.textContent = "Connecting to audio stream...";

    const allButtons = document.querySelectorAll("#swap-candidates-list button");
    allButtons.forEach((b) => (b.disabled = true));

    if (btnEl) {
      btnEl.innerHTML = `<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span><span>Replacing...</span>`;
    }
    if (statusEl) {
      statusEl.innerHTML = `<span class="text-emerald-400 font-semibold flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>Downloading audio & replacing file on disk...</span>`;
    }

    this.showToast(`Starting audio swap for "${trackTitle}"...`, "info");

    const wasCurrentPlaying = this.currentIndex >= 0 && this.playQueue[this.currentIndex]?.id === trackId;
    if (wasCurrentPlaying) {
      this.engine.pause();
      if (this.engine.audio) {
        this.engine.audio.src = "";
        this.engine.audio.load();
      }
    }

    this.activeSwapTrackId = trackId;

    if (this.activeSwapInterval) clearInterval(this.activeSwapInterval);
    this.activeSwapInterval = setInterval(async () => {
      if (!this.activeSwapTrackId) {
        clearInterval(this.activeSwapInterval);
        return;
      }
      try {
        const st = await api.getSwapStatus(trackId);
        if (st && st.progress !== undefined) {
          const pct = Math.max(0, Math.min(100, Math.round(st.progress)));
          const msg = st.message || `Processing (${pct}%)...`;
          if (progressFill) progressFill.style.width = `${pct}%`;
          if (progressPct) progressPct.textContent = `${pct}%`;
          if (progressDetail) progressDetail.textContent = msg;

          if (floatingBar) floatingBar.style.width = `${pct}%`;
          if (floatingPct) floatingPct.textContent = `${pct}%`;

          if (st.status === "error") {
            clearInterval(this.activeSwapInterval);
            this.activeSwapTrackId = null;
            if (progressDetail) {
              progressDetail.textContent = st.message || "Audio swap failed";
              progressDetail.classList.add("text-rose-400");
            }
          }
        }
      } catch (_e) {}
    }, 500);

    try {
      const res = await api.replaceTrackAudio(trackId, youtubeUrl, 320);
      const updatedTrack = res.track;

      if (this.activeSwapInterval) clearInterval(this.activeSwapInterval);
      this.activeSwapTrackId = null;

      if (progressFill) progressFill.style.width = "100%";
      if (progressPct) progressPct.textContent = "100%";
      if (progressDetail) progressDetail.textContent = "Replacement complete!";

      this.showToast(`Audio replaced successfully for "${updatedTrack.title}"!`);

      // Update in memory & cache bust stream_url
      if (updatedTrack.stream_url) {
        updatedTrack.stream_url = `${updatedTrack.stream_url.split("?")[0]}?v=${Date.now()}`;
      }
      const allIdx = this.allTracks.findIndex((t) => t.id === trackId);
      if (allIdx !== -1) this.allTracks[allIdx] = updatedTrack;
      const curIdx = this.tracks.findIndex((t) => t.id === trackId);
      if (curIdx !== -1) this.tracks[curIdx] = updatedTrack;

      if (wasCurrentPlaying) {
        this.playQueue[this.currentIndex] = updatedTrack;
        this.updateNowPlayingBar(updatedTrack);
        await this.playByIndex(this.currentIndex);
      }

      this.renderTrackList();
      setTimeout(() => {
        document.getElementById("swap-audio-modal")?.classList.add("hidden");
        if (progressContainer) progressContainer.classList.add("hidden");
        if (floatingPill) floatingPill.classList.add("hidden");
      }, 700);
    } catch (err) {
      if (this.activeSwapInterval) clearInterval(this.activeSwapInterval);
      this.activeSwapTrackId = null;
      if (floatingPill) floatingPill.classList.add("hidden");
      if (progressContainer) progressContainer.classList.add("hidden");

      this.showToast(`Audio replacement failed: ${err.message}`, "error");
      if (wasCurrentPlaying && this.playQueue[this.currentIndex]) {
        await this.playByIndex(this.currentIndex);
      }
    } finally {
      allButtons.forEach((b) => (b.disabled = false));
      if (btnEl) {
        btnEl.innerHTML = `<span>Select & Swap</span>`;
      }
      if (statusEl) {
        statusEl.textContent = "Ready";
      }
    }
  }

  minimizeSwapModal() {
    this.stopCandidatePreview();
    document.getElementById("swap-audio-modal")?.classList.add("hidden");
    const floatingPill = document.getElementById("floating-swap-pill");
    if (this.activeSwapTrackId && floatingPill) {
      floatingPill.classList.remove("hidden");
    }
  }

  restoreSwapModal() {
    const floatingPill = document.getElementById("floating-swap-pill");
    if (floatingPill) floatingPill.classList.add("hidden");
    const modal = document.getElementById("swap-audio-modal");
    if (modal) modal.classList.remove("hidden");
  }

  async runAutoDbFix() {
    const btn = document.getElementById("btn-auto-fix-db");
    const origHtml = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="w-3 h-3 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin"></span><span>Repairing...</span>`;
    }
    this.showToast("Running deep database repair & auto-fix...", "info");
    try {
      const res = await api.autoFixDatabase();
      this.showToast(res.message || "Database auto-repair completed successfully!");
      await this.loadAlbums();
      await this.loadLibrary("", true);
      await this.checkAlbumInconsistencies();
    } catch (err) {
      this.showToast(`Database auto-fix failed: ${err.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  }

  async checkAlbumInconsistencies() {
    try {
      const res = await api.getAlbumInconsistencies();
      this.albumInconsistencies = res.inconsistencies || [];
      const pill = document.getElementById("btn-album-inconsistencies-pill");
      const label = document.getElementById("album-inconsistencies-label");
      if (pill && label) {
        if (this.albumInconsistencies.length > 0) {
          label.textContent = `${this.albumInconsistencies.length} Conflict${this.albumInconsistencies.length > 1 ? "s" : ""}`;
          pill.classList.remove("hidden");
        } else {
          pill.classList.add("hidden");
        }
      }
    } catch (err) {
      console.debug("Could not check album inconsistencies:", err);
    }
  }

  openAlbumInconsistencyModal() {
    if (!this.albumInconsistencies.length) {
      this.showToast("No album conflicts detected in your library!", "info");
      return;
    }
    this.activeInconsistencyIdx = 0;
    const modal = document.getElementById("album-inconsistency-modal");
    if (modal) modal.classList.remove("hidden");
    this.renderInconsistencyView();
  }

  renderInconsistencyView() {
    const item = this.albumInconsistencies[this.activeInconsistencyIdx];
    if (!item) return;

    const navEl = document.getElementById("inconsistency-album-nav");
    if (navEl) {
      navEl.innerHTML = this.albumInconsistencies
        .map((inc, idx) => {
          const active = idx === this.activeInconsistencyIdx;
          const cls = active
            ? "bg-amber-500/25 border-amber-500 text-amber-300 font-bold"
            : "bg-zinc-950/70 border-zinc-800 text-zinc-400 hover:text-zinc-200";
          return `
            <button class="px-3 py-1.5 rounded-xl border text-xs whitespace-nowrap transition cursor-pointer ${cls}" data-inconsistency-idx="${idx}">
              ${this.escape(inc.album)} (${inc.total_tracks} tracks)
            </button>
          `;
        })
        .join("");

      navEl.querySelectorAll("[data-inconsistency-idx]").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.activeInconsistencyIdx = Number(btn.dataset.inconsistencyIdx);
          this.renderInconsistencyView();
        });
      });
    }

    const validCovers = (item.distinct_covers || []).filter((c) => c.has_cover);
    this.selectedCanonicalCoverTrackId = validCovers.length > 0 ? validCovers[0].track_id : null;
    this.selectedCanonicalYear = (item.years || [])[0] || "";

    const coversEl = document.getElementById("inconsistency-covers-list");
    if (coversEl) {
      if (!item.distinct_covers || !item.distinct_covers.length) {
        coversEl.innerHTML = `<div class="text-xs text-zinc-500">No cover art detected across these tracks.</div>`;
      } else {
        coversEl.innerHTML = item.distinct_covers
          .map((c) => {
            const isSelected = this.selectedCanonicalCoverTrackId === c.track_id;
            const borderCls = isSelected
              ? "ring-2 ring-emerald-500 border-emerald-500 bg-emerald-500/10"
              : "border-zinc-800 bg-zinc-950 hover:border-zinc-700";
            return `
              <div class="inconsistency-cover-card p-2.5 rounded-2xl border ${borderCls} cursor-pointer transition flex flex-col items-center text-center" data-cover-track-id="${c.track_id}">
                <img src="${api.resolveUrl(c.cover_url)}" alt="" class="w-20 h-20 sm:w-24 sm:h-24 object-cover rounded-xl shadow-md border border-zinc-700/60 mb-2" onerror="this.onerror=null; this.src='/icon.svg';" />
                <span class="text-[11px] font-semibold text-zinc-200 truncate w-full">${c.has_cover ? `${c.track_count} track(s)` : "Missing Cover"}</span>
                <span class="text-[10px] text-zinc-500 font-mono">${c.has_cover ? "Embedded Cover" : "No art"}</span>
                <span class="mt-1 text-[10px] px-2 py-0.5 rounded-full ${isSelected ? 'bg-emerald-500 text-zinc-950 font-bold' : 'bg-zinc-800 text-zinc-400'}">${isSelected ? 'Canonical Choice' : 'Select'}</span>
              </div>
            `;
          })
          .join("");

        coversEl.querySelectorAll("[data-cover-track-id]").forEach((card) => {
          card.addEventListener("click", () => {
            this.selectedCanonicalCoverTrackId = Number(card.dataset.coverTrackId);
            this.renderInconsistencyView();
          });
        });
      }
    }

    const yearsEl = document.getElementById("inconsistency-years-list");
    if (yearsEl) {
      const yearOpts = item.years && item.years.length ? item.years : ["Unknown"];
      yearsEl.innerHTML = yearOpts
        .map((yr) => {
          const isSelected = this.selectedCanonicalYear === yr;
          const cls = isSelected
            ? "bg-emerald-500 text-zinc-950 font-bold shadow-md"
            : "bg-zinc-900 border border-zinc-800 text-zinc-300 hover:bg-zinc-800";
          return `
            <button class="px-3.5 py-1.5 rounded-xl text-xs font-mono transition cursor-pointer ${cls}" data-year-val="${this.escapeAttr(yr)}">
              ${this.escape(yr)}
            </button>
          `;
        })
        .join("");

      yearsEl.querySelectorAll("[data-year-val]").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.selectedCanonicalYear = btn.dataset.yearVal;
          this.renderInconsistencyView();
        });
      });
    }

    const artistInput = document.getElementById("inconsistency-artist-input");
    if (artistInput) {
      artistInput.value = item.artist || (item.album_artists || [])[0] || "";
    }

    const summaryEl = document.getElementById("inconsistency-summary-note");
    if (summaryEl) {
      const issues = [];
      if (item.cover_conflict) issues.push(`Conflicting or missing cover art across ${item.distinct_covers?.length || 0} track variations`);
      if (item.year_conflict) issues.push(`Conflicting release years: ${(item.years || []).join(", ")}`);
      if (item.artist_conflict) issues.push(`Mismatched artist tags: ${(item.album_artists || []).join(", ")}`);

      summaryEl.innerHTML = `
        <div class="font-bold text-amber-300 flex items-center gap-1.5">
          <span>⚠️ Detected Inconsistencies for "${this.escape(item.album)}" (${item.total_tracks} tracks):</span>
        </div>
        <ul class="list-disc list-inside text-zinc-300 space-y-0.5 pt-1">
          ${issues.map((i) => `<li>${this.escape(i)}</li>`).join("")}
        </ul>
        <div class="text-[11px] text-zinc-400 pt-1 border-t border-zinc-800/80">
          Clicking "Unify All Tracks" will re-tag all ${item.total_tracks} MP3 files with the chosen master cover art, year, and artist.
        </div>
      `;
    }
  }

  async applyResolveInconsistency() {
    const item = this.albumInconsistencies[this.activeInconsistencyIdx];
    if (!item) return;

    const artistInput = document.getElementById("inconsistency-artist-input");
    const canonicalArtist = artistInput ? artistInput.value.trim() : item.artist;
    const submitBtn = document.getElementById("btn-apply-inconsistency-resolve");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<span>Unifying ID3 tags across ${item.total_tracks} tracks...</span>`;
    }

    try {
      const res = await api.resolveAlbumInconsistency({
        album: item.album,
        artist: item.artist,
        canonical_track_id_for_cover: this.selectedCanonicalCoverTrackId,
        canonical_year: this.selectedCanonicalYear,
        canonical_album_artist: canonicalArtist,
      });

      this.showToast(res.message || `Album "${item.album}" unified successfully!`);
      document.getElementById("album-inconsistency-modal")?.classList.add("hidden");

      await this.checkAlbumInconsistencies();
      await this.loadAlbums();
      await this.loadLibrary("", true);
    } catch (err) {
      this.showToast(`Failed unifying album: ${err.message}`, "error");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><polygon points="20 6 9 17 4 12"/></svg><span>Unify All Tracks in Album</span>`;
      }
    }
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
      this.showToast("Please enter a Last.fm username.", "warning");
      return;
    }

    const preferredMethod = document.getElementById("ext-method")?.value || "auto";

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
          preferred_download_method: preferredMethod,
        }),
      });
      if (this.isJobRunning) {
        this.showToast(`Extraction queued in FIFO buffer for ${username}`);
      } else {
        this.activeJobId = job.job_id;
        this.showToast(`Started Last.fm extraction for ${username}`);
        this.subscribeToJobProgress(job.job_id);
      }
    } catch (err) {
      this.showToast(`Failed starting job: ${err.message}`, "error");
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

  async sendAgentInstruction(instructionText = null) {
    const inputEl = document.getElementById("agent-user-prompt");
    const prompt = (instructionText || inputEl?.value || "").trim();
    if (!prompt) return;

    const btn = document.getElementById("btn-send-agent-prompt");
    const statusEl = document.getElementById("curator-status");
    const convoBox = document.getElementById("agent-conversation-box");
    const replyEl = document.getElementById("agent-reply-text");
    const applyBtn = document.getElementById("btn-apply-curation");

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>Thinking...</span>`;
    }
    if (statusEl) statusEl.textContent = "AI Agent is reasoning over library tracks...";

    try {
      const configCard = document.getElementById("agent-config-card");
      if (configCard && !configCard.classList.contains("hidden")) {
        await this.saveSettings(true);
      }
      if (!this.agentChatHistory) this.agentChatHistory = [];

      const res = await api.request("/api/curation/agent", {
        method: "POST",
        body: JSON.stringify({
          instruction: prompt,
          history: this.agentChatHistory,
        }),
      });

      this.agentChatHistory.push({ role: "user", content: prompt });
      this.agentChatHistory.push({ role: "assistant", content: res.reply || "" });

      if (convoBox && replyEl) {
        convoBox.classList.remove("hidden");
        replyEl.textContent = res.reply || "Processed library instruction.";
      }

      const engineBadge = document.getElementById("agent-engine-badge");
      if (engineBadge) {
        if (res.used_llm) {
          engineBadge.className = "px-2 py-0.5 rounded-full text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 font-mono";
          engineBadge.textContent = res.debug?.model ? `⚡ LLM: ${res.debug.model}` : "⚡ LLM Active";
        } else {
          engineBadge.className = "px-2 py-0.5 rounded-full text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/30 font-mono";
          engineBadge.textContent = "⚙ Built-in Heuristics";
        }
      }

      const thinkBox = document.getElementById("agent-thinking-box");
      const thinkText = document.getElementById("agent-thinking-text");
      if (thinkBox && thinkText) {
        if (res.thinking) {
          thinkText.textContent = res.thinking;
          thinkBox.classList.remove("hidden");
        } else {
          thinkBox.classList.add("hidden");
        }
      }

      const debugCard = document.getElementById("agent-debug-card");
      if (debugCard && res.debug) {
        debugCard.classList.remove("hidden");
        const d = res.debug;
        const lat = d.duration_ms ? `${d.duration_ms}ms` : "-";
        const latencyEl = document.getElementById("agent-debug-latency");
        const statusEl2 = document.getElementById("debug-status");
        const durationEl = document.getElementById("debug-duration");
        const modelEl = document.getElementById("debug-model");
        const evalEl = document.getElementById("debug-evaluated");
        const detailEl = document.getElementById("debug-detail");

        if (latencyEl) latencyEl.textContent = lat !== "-" ? `⏱ ${lat}` : "";
        if (statusEl2) {
          statusEl2.textContent = d.status || "ok";
          statusEl2.className = d.status === "success" ? "text-emerald-400 font-bold" : "text-amber-400 font-bold";
        }
        if (durationEl) durationEl.textContent = lat;
        if (modelEl) modelEl.textContent = d.model || (res.used_llm ? "LLM" : "Local Heuristic");
        if (evalEl) evalEl.textContent = `${d.evaluated_tracks_count ?? "-"} / ${d.total_library_tracks ?? "-"} tracks`;
        if (detailEl) {
          const lines = [];
          if (d.endpoint) lines.push(`Endpoint: ${d.endpoint}`);
          if (d.matched_keywords && d.matched_keywords.length) lines.push(`Focused Keywords: ${d.matched_keywords.join(", ")}`);
          if (d.error_detail) lines.push(`LLM Error: ${d.error_detail}`);
          if (d.reason) lines.push(`Note: ${d.reason}`);
          if (d.tokens && (d.tokens.prompt_tokens || d.tokens.total_tokens)) {
            lines.push(`Tokens: prompt=${d.tokens.prompt_tokens || 0}, completion=${d.tokens.completion_tokens || 0}`);
          }
          detailEl.textContent = lines.join(" • ") || "Audit completed without errors.";
        }
      }

      this.proposedCurationActions = res.actions || [];
      const engineLabel = res.engine || (res.used_llm ? "External LLM" : "Built-in Heuristics");
      if (statusEl) {
        statusEl.textContent = `Completed via ${engineLabel}: ${this.proposedCurationActions.length} actionable fix(es).`;
      }
      if (applyBtn) {
        applyBtn.classList.toggle("hidden", !this.proposedCurationActions.length);
      }
      if (inputEl) inputEl.value = "";
      this.renderCurationActions();
    } catch (err) {
      if (statusEl) statusEl.textContent = `Agent error: ${err.message}`;
      this.showToast(`AI Agent error: ${err.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<span>Execute</span> <svg class="w-3.5 h-3.5 stroke-current fill-none inline ml-1" viewBox="0 0 24 24" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
      }
    }
  }

  renderCurationActions() {
    const listEl = document.getElementById("curator-actions-list");
    if (!listEl) return;
    if (!this.proposedCurationActions.length) {
      listEl.innerHTML = `<div class="p-4 text-xs text-zinc-400 text-center flex items-center justify-center gap-1.5"><svg class="w-3.5 h-3.5 stroke-current fill-none text-emerald-400" viewBox="0 0 24 24" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span>No pending actions. Type an instruction above for the AI agent!</span></div>`;
      return;
    }

    listEl.innerHTML = this.proposedCurationActions
      .map((act, idx) => {
        const isDup = act.action_type === "duplicate_remove";
        const isBulk = act.action_type === "bulk_album_rename";
        const badgeColor = isDup ? "text-rose-400" : isBulk ? "text-cyan-400" : "text-emerald-400";
        const badgeIcon = isDup
          ? `<svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`
          : isBulk
          ? `<svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>`
          : `<svg class="w-3.5 h-3.5 stroke-current fill-none" viewBox="0 0 24 24" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`;
        const badgeLabel = isDup
          ? "Remove Duplicate Version"
          : isBulk
          ? "Bulk Album Rename"
          : "Rename Track / Canonicalize Album";

        return `
          <label class="flex items-start gap-2.5 p-2.5 rounded-xl bg-zinc-950/80 border border-zinc-800 text-xs cursor-pointer hover:border-zinc-700">
            <input type="checkbox" data-curate-idx="${idx}" checked class="mt-0.5 accent-emerald-500" />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 font-semibold ${badgeColor}">
                ${badgeIcon}
                <span>${badgeLabel}</span>
              </div>
              ${
                isBulk
                  ? `<div class="text-zinc-300 truncate mt-0.5">
                      <b>Old Album:</b> ${this.escape(act.old_album)} → <b class="text-cyan-300">New Album:</b> ${this.escape(act.new_album)}
                     </div>`
                  : `<div class="text-zinc-300 truncate mt-0.5">
                      <b>Before:</b> ${this.escape(act.current_artist)} — ${this.escape(act.current_title)} <span class="text-zinc-500">[${this.escape(act.current_album)}]</span>
                    </div>
                    ${
                      !isDup
                        ? `<div class="text-emerald-300 truncate">
                            <b>After:</b> ${this.escape(act.proposed_artist)} — ${this.escape(act.proposed_title)} <span class="text-emerald-400">[${this.escape(act.proposed_album)}]</span>
                          </div>`
                        : ""
                    }`
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
      this.showToast(
        `Applied fixes: ${res.renamed} renamed, ${res.removed_duplicates} duplicate(s) removed.`
      );
      this.proposedCurationActions = [];
      this.renderCurationActions();
      document.getElementById("btn-apply-curation")?.classList.add("hidden");
      await this.loadAlbums();
      await this.loadLibrary(document.getElementById("search-input")?.value || "", true);
    } catch (err) {
      this.showToast(`Failed applying curation actions: ${err.message}`, "error");
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
      setVal("proxy-name", cfg.proxy_name || "default");
      setVal("proxy-server-url", cfg.proxy_server_url || "");
      setVal("proxy-password", cfg.proxy_password || "");
      setVal("set-preferred-download-method", cfg.preferred_download_method || "auto");
      setVal("set-default-bitrate", cfg.default_bitrate_kbps || "320");
      setVal("dl-single-method", cfg.preferred_download_method || "auto");
      setVal("dl-single-bitrate", cfg.default_bitrate_kbps || "320");
      setVal("dl-album-method", cfg.preferred_download_method || "auto");
      setVal("ext-method", cfg.preferred_download_method || "auto");
      setVal("ext-bitrate", cfg.default_bitrate_kbps || "320");

      // Last.fm Imbued Scrobbler UI State
      const isConnected = !!cfg.lastfm_session_connected;
      const lfmUsername = cfg.lastfm_username || "";
      const connCard = document.getElementById("lastfm-connected-card");
      const disconnCard = document.getElementById("lastfm-disconnected-card");
      const statusBadge = document.getElementById("lastfm-status-badge");
      const userDisplay = document.getElementById("lastfm-user-display");
      const profileLink = document.getElementById("lastfm-profile-link");
      const playerBadge = document.getElementById("player-lastfm-badge");
      const scrobbleCb = document.getElementById("set-scrobble-enabled");
      const nowPlayingCb = document.getElementById("set-nowplaying-enabled");

      if (connCard) connCard.classList.toggle("hidden", !isConnected);
      if (disconnCard) disconnCard.classList.toggle("hidden", isConnected);
      if (statusBadge) {
        statusBadge.textContent = isConnected ? "Connected" : "Disconnected";
        statusBadge.className = isConnected
          ? "text-[10px] font-mono px-2 py-0.5 rounded-full border border-emerald-500/60 bg-emerald-950/60 text-emerald-400"
          : "text-[10px] font-mono px-2 py-0.5 rounded-full border border-zinc-700 bg-zinc-800 text-zinc-400";
      }
      if (userDisplay) userDisplay.textContent = lfmUsername ? `@${lfmUsername}` : "@user";
      if (profileLink && lfmUsername) profileLink.href = `https://www.last.fm/user/${encodeURIComponent(lfmUsername)}`;
      if (playerBadge) playerBadge.classList.toggle("hidden", !isConnected);

      if (scrobbleCb) scrobbleCb.checked = cfg.lastfm_scrobble_enabled !== false;
      if (nowPlayingCb) nowPlayingCb.checked = cfg.lastfm_nowplaying_enabled !== false;

      // Pending scrobbles
      try {
        const syncInfo = await api.request("/api/sync/info");
        const pendingEl = document.getElementById("lastfm-pending-text");
        if (pendingEl) pendingEl.textContent = `Pending scrobbles: ${syncInfo.pending_scrobbles || 0}`;
      } catch (_e) {}

      await this.loadAlbumAliases();
      try {
        const storageInfo = await api.request("/api/system/storage-info");
        const storageInput = document.getElementById("set-storage-path");
        if (storageInput && storageInfo.downloads_dir) {
          storageInput.value = storageInfo.downloads_dir;
        }
      } catch (_e) {}
    } catch (_err) {}
  }

  async openStorageFolder() {
    try {
      const res = await api.request("/api/system/open-storage", { method: "POST" });
      this.showToast(res.message || "Opened storage folder.");
    } catch (err) {
      this.showToast(`Failed opening storage folder: ${err.message}`, "error");
    }
  }

  toggleMute() {
    const volBar = document.getElementById("volume-bar");
    const volLabel = document.getElementById("volume-label");
    if (!volBar) return;

    const currentVal = Number(volBar.value || 0);
    if (currentVal > 0) {
      this.lastVolumeBeforeMute = currentVal;
      volBar.value = "0";
      this.engine.setBaseVolume(0);
      this.updateSliderFill(volBar);
      if (volLabel) volLabel.textContent = "0%";
      this.updateVolumeIcon(0);
    } else {
      const restoreVal = this.lastVolumeBeforeMute || 85;
      volBar.value = String(restoreVal);
      this.engine.setBaseVolume(restoreVal / 100.0);
      this.updateSliderFill(volBar);
      if (volLabel) volLabel.textContent = `${restoreVal}%`;
      this.updateVolumeIcon(restoreVal);
    }
  }

  updateVolumeIcon(val) {
    const iconBtn = document.getElementById("btn-toggle-mute");
    if (!iconBtn) return;
    if (val === 0) {
      iconBtn.innerHTML = `
        <svg id="volume-icon" class="w-5 h-5 stroke-current fill-none text-red-400" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
          <line x1="23" y1="9" x2="17" y2="15"/>
          <line x1="17" y1="9" x2="23" y2="15"/>
        </svg>`;
    } else if (val < 50) {
      iconBtn.innerHTML = `
        <svg id="volume-icon" class="w-5 h-5 stroke-current fill-none text-zinc-300" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
          <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
        </svg>`;
    } else {
      iconBtn.innerHTML = `
        <svg id="volume-icon" class="w-5 h-5 stroke-current fill-none text-zinc-300" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
          <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
        </svg>`;
    }
  }

  async saveSettings(silent = false) {
    const apiKey = document.getElementById("set-api-key")?.value.trim();
    const apiSecret = document.getElementById("set-api-secret")?.value.trim();
    const sessionKey = document.getElementById("set-session-key")?.value.trim();
    const username = document.getElementById("set-username")?.value.trim();
    const scrobbleEnabled = document.getElementById("set-scrobble-enabled")?.checked;
    const nowPlayingEnabled = document.getElementById("set-nowplaying-enabled")?.checked;
    const llmBaseUrl = document.getElementById("llm-base-url")?.value.trim();
    const llmApiKey = document.getElementById("llm-api-key")?.value.trim();
    const llmModel = document.getElementById("llm-model")?.value.trim();
    const proxyName = document.getElementById("proxy-name")?.value.trim();
    const proxyServerUrl = document.getElementById("proxy-server-url")?.value.trim();
    const proxyPassword = document.getElementById("proxy-password")?.value.trim();

    await api.request("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        lastfm_api_key: apiKey || undefined,
        lastfm_api_secret: apiSecret || undefined,
        lastfm_session_key: sessionKey || undefined,
        lastfm_username: username || undefined,
        lastfm_scrobble_enabled: scrobbleEnabled !== undefined ? scrobbleEnabled : undefined,
        lastfm_nowplaying_enabled: nowPlayingEnabled !== undefined ? nowPlayingEnabled : undefined,
        llm_base_url: llmBaseUrl || undefined,
        llm_api_key: llmApiKey || undefined,
        llm_model: llmModel || undefined,
        proxy_name: proxyName || undefined,
        proxy_server_url: proxyServerUrl !== undefined ? proxyServerUrl : undefined,
        proxy_password: proxyPassword !== undefined ? proxyPassword : undefined,
      }),
    });
    if (!silent) {
      this.showToast("Settings saved to SQLite WAL database.");
    }
    await this.loadSettingsIntoUI();
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
    await this.loadLibrary("", true);
    offlineStore.flushOfflineScrobbles(api).catch(() => {});
    this.showToast("Connected to MusicLinker Server.");
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

    // Equalizer Modal & DSP controls
    document.getElementById("btn-open-eq")?.addEventListener("click", () => this.openEqualizerModal());
    document.getElementById("btn-big-eq")?.addEventListener("click", () => this.openEqualizerModal());
    document.getElementById("btn-close-eq-modal")?.addEventListener("click", () => this.closeEqualizerModal());
    document.getElementById("btn-eq-done")?.addEventListener("click", () => this.closeEqualizerModal());
    document.getElementById("btn-eq-reset")?.addEventListener("click", () => {
      this.engine.resetEq();
      this.refreshEqualizerModalValues();
      this.showToast("Equalizer reset to Flat (0 dB Preamp)");
    });
    document.getElementById("eq-enable-toggle")?.addEventListener("change", (e) => {
      const enabled = this.engine.toggleEq(e.target.checked);
      const label = document.getElementById("eq-enable-label");
      if (label) {
        label.textContent = enabled ? "DSP ON" : "DSP OFF";
        label.className = enabled ? "text-emerald-400 font-bold" : "text-zinc-500 font-bold";
      }
    });

    // Master Preamp Volume Equalizer Slider & Limiter
    const preampSlider = document.getElementById("eq-preamp-slider");
    preampSlider?.addEventListener("input", (e) => {
      const val = Number(e.target.value) || 0;
      this.engine.setPreamp(val);
      const badge = document.getElementById("eq-preamp-badge");
      if (badge) {
        const mult = Math.pow(10, val / 20).toFixed(1);
        badge.textContent = `${val > 0 ? "+" : ""}${val} dB (${mult}x)`;
      }
    });

    document.querySelectorAll(".eq-preamp-quick").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = Number(btn.dataset.preamp) || 0;
        this.engine.setPreamp(p);
        this.refreshEqualizerModalValues();
        this.showToast(`Preamp volume set to ${p > 0 ? "+" : ""}${p} dB`);
      });
    });

    document.getElementById("eq-limiter-toggle")?.addEventListener("change", (e) => {
      const enabled = this.engine.toggleLimiter(e.target.checked);
      this.showToast(enabled ? "Anti-Clip Limiter Enabled" : "Limiter Disabled");
    });

    for (let i = 0; i < 5; i++) {
      const slider = document.getElementById(`eq-slider-${i}`);
      slider?.addEventListener("input", (e) => {
        const val = Number(e.target.value) || 0;
        const valBadge = document.getElementById(`eq-val-${i}`);
        if (valBadge) {
          valBadge.textContent = `${val > 0 ? "+" : ""}${val} dB`;
        }
        this.engine.setBandGain(i, val);
        this.renderEqPresetsUI();
      });
    }
    document.getElementById("btn-rescan-library")?.addEventListener("click", () => this.rescanLibraryOnDisk());
    document.getElementById("car-btn-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("car-btn-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("car-btn-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-car-mode")?.addEventListener("click", () => this.toggleCarMode());
    document.getElementById("btn-exit-car")?.addEventListener("click", () => this.toggleCarMode());

    // Sort order selector
    document.getElementById("sort-select")?.addEventListener("change", (e) => {
      this.sortBy = e.target.value || "track_num";
      this.loadLibrary(document.getElementById("search-input")?.value || "", true);
    });

    // View mode switcher: Standard Library vs 3-Layer Selector vs Playlists
    document.getElementById("btn-view-standard")?.addEventListener("click", () => this.switchLibraryViewMode("standard"));
    document.getElementById("btn-view-layered")?.addEventListener("click", () => this.switchLibraryViewMode("layered"));
    document.getElementById("btn-view-playlists")?.addEventListener("click", () => this.switchLibraryViewMode("playlists"));

    // 3-Layer Selector action buttons
    document.getElementById("btn-layer-play")?.addEventListener("click", () => this.playLayeredSelectionQueue());
    document.getElementById("btn-layer-shuffle")?.addEventListener("click", () => this.randomizeAndPlayCurrentView());
    document.getElementById("btn-layer-save-playlist")?.addEventListener("click", () => this.saveLayerSelectionAsPlaylist());
    document.getElementById("btn-layer-export-modal")?.addEventListener("click", () => this.openExportModal("funnel"));
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
    document.getElementById("btn-delete-selected-album")?.addEventListener("click", () => this.deleteSelectedAlbum());
    document.getElementById("btn-layer-delete-selected")?.addEventListener("click", () => this.deleteLayerSelectedTracks());

    // Styled Seek & Volume Sliders
    document.getElementById("seek-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.seekToFraction(Number(e.target.value) / 1000.0);
    });
    document.getElementById("volume-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      const val = Number(e.target.value);
      this.engine.setBaseVolume(val / 100.0);
      const label = document.getElementById("volume-label");
      if (label) label.textContent = `${Math.round(val)}%`;
      this.updateVolumeIcon(val);
    });
    document.getElementById("btn-toggle-mute")?.addEventListener("click", () => this.toggleMute());
    document.getElementById("btn-bottom-lyrics")?.addEventListener("click", () => this.toggleBigPlayer(true));

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
    document.getElementById("btn-save-lyric-offset")?.addEventListener("click", () => this.saveCurrentLyricOffsetPermanently());
    document.getElementById("btn-big-lyric-earlier")?.addEventListener("click", () => this.adjustLyricOffset(-0.5));
    document.getElementById("btn-big-lyric-later")?.addEventListener("click", () => this.adjustLyricOffset(0.5));
    document.getElementById("btn-big-save-lyric")?.addEventListener("click", () => this.saveCurrentLyricOffsetPermanently());
    document.getElementById("btn-open-storage-folder")?.addEventListener("click", () => this.openStorageFolder());

    // Studio Right-Hand Navigation Tabs
    ["lyrics", "extractor", "curator", "settings"].forEach((tab) => {
      document.getElementById(`tab-btn-${tab}`)?.addEventListener("click", () => this.switchStudioTab(tab));
    });

    // Debounced Library Search
    document.getElementById("search-input")?.addEventListener("input", (e) => {
      const val = e.target.value;
      if (this.searchDebounceTimer) clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = setTimeout(() => {
        this.loadLibrary(val, false);
      }, 180);
    });

    document.getElementById("btn-rename-selected-album")?.addEventListener("click", () => this.openBulkAlbumRenameModal());
    document.getElementById("btn-confirm-album-rename")?.addEventListener("click", () => this.submitBulkAlbumRename());
    document.getElementById("btn-save-track-edit")?.addEventListener("click", () => this.saveEditedTrackMetadata());
    document.getElementById("btn-edit-swap-audio")?.addEventListener("click", () => {
      const trackId = Number(document.getElementById("edit-track-id")?.value || 0);
      const track = this.allTracks.find((t) => t.id === trackId);
      if (track) {
        document.getElementById("edit-track-modal")?.classList.add("hidden");
        this.openSwapAudioModal(track);
      }
    });
    document.getElementById("btn-delete-track-modal")?.addEventListener("click", async () => {
      const trackId = Number(document.getElementById("edit-track-id")?.value || 0);
      const title = document.getElementById("edit-track-title")?.value || "track";
      const deleteFile = document.getElementById("edit-delete-file")?.checked ?? true;
      const unmarkHash = document.getElementById("edit-unmark-hash")?.checked ?? false;
      if (!trackId) return;
      if (confirm(`Delete "${title}" from your library${deleteFile ? " and disk" : ""}?`)) {
        document.getElementById("edit-track-modal")?.classList.add("hidden");
        await this.deleteTrack(trackId, deleteFile, unmarkHash);
      }
    });

    // Single Track & Direct Link Downloader
    document.getElementById("btn-search-single")?.addEventListener("click", () => this.previewSingleTrackOrLink());
    document.getElementById("btn-download-single-direct")?.addEventListener("click", () => this.startDirectTrackDownload(null));
    document.getElementById("dl-single-query")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.previewSingleTrackOrLink();
      }
    });

    // Full Album / Playlist Downloader, Last.fm Extractor & Job Cancellation
    document.getElementById("btn-start-extract")?.addEventListener("click", () => this.startExtractionJob());
    document.getElementById("btn-preview-album")?.addEventListener("click", () => this.previewAlbumForDownload());
    document.getElementById("btn-download-album")?.addEventListener("click", () => this.startAlbumDownloadJob());
    document.getElementById("dl-album-title")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.previewAlbumForDownload();
      }
    });
    document.getElementById("btn-cancel-job")?.addEventListener("click", () => this.cancelActiveJob());
    document.getElementById("btn-pause-job")?.addEventListener("click", () => this.togglePauseActiveJob());
    document.getElementById("btn-retry-job")?.addEventListener("click", () => this.retryActiveJob());

    // LLM Agent Curation & APK Pairing
    document.getElementById("btn-run-curator")?.addEventListener("click", () => this.runLLMLibraryAudit());
    document.getElementById("btn-send-agent-prompt")?.addEventListener("click", () => this.sendAgentInstruction());
    document.getElementById("agent-user-prompt")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.sendAgentInstruction();
      }
    });
    document.querySelectorAll(".agent-chip").forEach((chip) => {
      chip.addEventListener("click", (e) => {
        const prompt = e.currentTarget.getAttribute("data-prompt");
        const inputEl = document.getElementById("agent-user-prompt");
        if (inputEl) inputEl.value = prompt;
        this.sendAgentInstruction(prompt);
      });
    });
    document.getElementById("btn-toggle-agent-config")?.addEventListener("click", () => {
      document.getElementById("agent-config-card")?.classList.toggle("hidden");
    });
    document.getElementById("btn-apply-curation")?.addEventListener("click", () => this.applySelectedCurationActions());
    document.getElementById("btn-show-sync")?.addEventListener("click", () => this.openSyncModal());
    document.getElementById("btn-copy-pairing-url")?.addEventListener("click", () => {
      const text = document.getElementById("sync-pairing-url")?.textContent || "";
      if (text && navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => this.showToast("Pairing URL copied to clipboard!"));
      }
    });
    document.getElementById("btn-save-apk-server")?.addEventListener("click", () => this.saveApkServerConnection());
    document.getElementById("btn-save-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-llm-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-proxy-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-download-preferences")?.addEventListener("click", () => this.saveDownloadPreferences());
    document.getElementById("btn-refresh-aliases")?.addEventListener("click", () => this.loadAlbumAliases());

    // Big Player & Karaoke Synced Lyrics
    document.getElementById("btn-expand-player")?.addEventListener("click", () => this.toggleBigPlayer(true));
    document.getElementById("np-cover")?.addEventListener("click", () => this.toggleBigPlayer(true));
    document.getElementById("np-text-container")?.addEventListener("click", () => this.toggleBigPlayer(true));
    document.getElementById("btn-close-big-player")?.addEventListener("click", () => this.toggleBigPlayer(false));
    document.getElementById("btn-big-play")?.addEventListener("click", () => this.togglePlay());
    document.getElementById("btn-big-prev")?.addEventListener("click", () => this.prevTrack());
    document.getElementById("btn-big-next")?.addEventListener("click", () => this.nextTrack());
    document.getElementById("btn-big-shuffle")?.addEventListener("click", () => this.toggleShuffle());
    document.getElementById("big-seek-bar")?.addEventListener("input", (e) => {
      this.updateSliderFill(e.target);
      this.engine.seekToFraction(Number(e.target.value) / 1000.0);
    });
    document.getElementById("btn-big-player-cache")?.addEventListener("click", async () => {
      const cur = this.playQueue[this.currentIndex];
      if (cur) {
        await offlineStore.saveTrackForOffline(cur, api);
        await this.refreshOfflineState();
        this.updateNowPlayingBar(cur);
        this.showToast(`Saved "${cur.title}" directly to device storage!`);
      }
    });

    // Customizable Playlists
    document.getElementById("btn-create-playlist")?.addEventListener("click", () => this.openCreatePlaylistModal());
    document.getElementById("btn-refresh-playlists")?.addEventListener("click", () => this.loadPlaylists());
    document.getElementById("btn-pl-play")?.addEventListener("click", () => this.playActivePlaylist(false));
    document.getElementById("btn-pl-shuffle")?.addEventListener("click", () => this.playActivePlaylist(true));
    document.getElementById("btn-pl-export")?.addEventListener("click", () => this.openExportModal("playlist"));
    document.getElementById("btn-pl-cache-offline")?.addEventListener("click", () => this.cacheActivePlaylistOffline());
    document.getElementById("btn-pl-delete")?.addEventListener("click", () => this.deleteActivePlaylist());
    document.getElementById("btn-pl-edit-meta")?.addEventListener("click", () => this.openEditPlaylistModal());
    document.getElementById("btn-save-playlist-submit")?.addEventListener("click", () => this.savePlaylistSubmit());

    // Export Modal
    document.getElementById("btn-fmt-m3u")?.addEventListener("click", () => this.setExportFormat("m3u"));
    document.getElementById("btn-fmt-txt")?.addEventListener("click", () => this.setExportFormat("txt"));
    document.getElementById("btn-fmt-json")?.addEventListener("click", () => this.setExportFormat("json"));
    document.getElementById("btn-do-export-download")?.addEventListener("click", () => this.downloadExportFile());

    // Multi-Select & Bulk Album Grouping
    document.getElementById("btn-toggle-multi-select")?.addEventListener("click", () => this.toggleMultiSelectMode());
    document.getElementById("btn-multi-select-all")?.addEventListener("click", () => this.selectAllFilteredTracks());
    document.getElementById("btn-multi-clear")?.addEventListener("click", () => this.clearSelectedTracks());
    document.getElementById("btn-multi-group-album")?.addEventListener("click", () => this.openBulkAlbumModal());
    document.getElementById("btn-multi-delete")?.addEventListener("click", () => this.batchDeleteSelectedTracks());
    document.getElementById("btn-multi-add-playlist")?.addEventListener("click", () => this.addMultiSelectToPlaylist());
    document.getElementById("btn-bulk-compare-official")?.addEventListener("click", () => this.matchBulkAlbumTracklist());
    document.getElementById("btn-bulk-apply-album-submit")?.addEventListener("click", () => this.submitBulkAssignAlbum());

    // Audio Version Swap Modal & Search Form
    document.getElementById("swap-search-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = document.getElementById("swap-search-input")?.value.trim() || "";
      if (this.swapCurrentTrack) {
        this.queryAudioCandidates(this.swapCurrentTrack.id, q);
      }
    });

    document.getElementById("btn-close-swap-modal")?.addEventListener("click", () => {
      this.stopCandidatePreview();
      this.minimizeSwapModal();
    });
    document.getElementById("btn-cancel-swap-modal")?.addEventListener("click", () => {
      this.stopCandidatePreview();
      this.minimizeSwapModal();
    });
    document.getElementById("btn-minimize-swap-modal")?.addEventListener("click", () => this.minimizeSwapModal());
    document.getElementById("btn-swap-run-bg")?.addEventListener("click", () => this.minimizeSwapModal());
    document.getElementById("floating-swap-pill")?.addEventListener("click", () => this.restoreSwapModal());
    document.getElementById("btn-floating-swap-expand")?.addEventListener("click", (e) => {
      e.stopPropagation();
      this.restoreSwapModal();
    });

    // Auto DB Fix button
    document.getElementById("btn-auto-fix-db")?.addEventListener("click", () => this.runAutoDbFix());

    // Album Inconsistencies button & modal
    document.getElementById("btn-album-inconsistencies-pill")?.addEventListener("click", () => this.openAlbumInconsistencyModal());
    document.getElementById("btn-close-inconsistency-modal")?.addEventListener("click", () => {
      document.getElementById("album-inconsistency-modal")?.classList.add("hidden");
    });
    document.getElementById("btn-cancel-inconsistency-modal")?.addEventListener("click", () => {
      document.getElementById("album-inconsistency-modal")?.classList.add("hidden");
    });
    document.getElementById("btn-apply-inconsistency-resolve")?.addEventListener("click", () => this.applyResolveInconsistency());

    // Imbued Last.fm Scrobbler Listeners
    document.getElementById("btn-lastfm-connect")?.addEventListener("click", async () => {
      try {
        const authData = await api.startLastFmAuth();
        this.pendingLastFmToken = authData.token;
        if (authData.auth_url) {
          window.open(authData.auth_url, "_blank");
        }
        document.getElementById("lastfm-auth-modal")?.classList.remove("hidden");
        this.startLastFmPolling();
      } catch (err) {
        this.showToast(`Failed starting Last.fm authorization: ${err.message}`, "error");
      }
    });

    document.getElementById("btn-complete-lastfm-auth")?.addEventListener("click", async () => {
      await this.checkLastFmAuthSession(true);
    });

    document.getElementById("btn-close-lastfm-modal")?.addEventListener("click", () => {
      this.stopLastFmPolling();
      document.getElementById("lastfm-auth-modal")?.classList.add("hidden");
    });
    document.getElementById("btn-cancel-lastfm-modal")?.addEventListener("click", () => {
      this.stopLastFmPolling();
      document.getElementById("lastfm-auth-modal")?.classList.add("hidden");
    });

    window.addEventListener("focus", () => {
      if (this.lastFmPollingActive && this.pendingLastFmToken) {
        this.checkLastFmAuthSession();
      }
    });

    document.getElementById("btn-lastfm-disconnect")?.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to disconnect your Last.fm account?")) return;
      try {
        await api.disconnectLastFm();
        this.showToast("Disconnected from Last.fm.");
        await this.loadSettingsIntoUI();
      } catch (err) {
        this.showToast(`Disconnect failed: ${err.message}`, "error");
      }
    });

    document.getElementById("btn-flush-scrobbles")?.addEventListener("click", async () => {
      try {
        const res = await api.flushPendingScrobbles();
        this.showToast(`Synced ${res.flushed} scrobble(s) to Last.fm.`);
        const pendingEl = document.getElementById("lastfm-pending-text");
        if (pendingEl) pendingEl.textContent = `Pending scrobbles: ${res.pending_count || 0}`;
      } catch (err) {
        this.showToast(`Sync failed: ${err.message}`, "error");
      }
    });

    document.getElementById("set-scrobble-enabled")?.addEventListener("change", async (e) => {
      await api.request("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ lastfm_scrobble_enabled: e.target.checked }),
      });
    });

    document.getElementById("set-nowplaying-enabled")?.addEventListener("change", async (e) => {
      await api.request("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ lastfm_nowplaying_enabled: e.target.checked }),
      });
    });

    // Global keyboard shortcuts
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (this.bigPlayerOpen) {
          this.toggleBigPlayer(false);
        }
        this.stopCandidatePreview();
        document.getElementById("export-list-modal")?.classList.add("hidden");
        document.getElementById("playlist-edit-modal")?.classList.add("hidden");
        document.getElementById("swap-audio-modal")?.classList.add("hidden");
        document.getElementById("bulk-album-modal")?.classList.add("hidden");
        document.getElementById("lastfm-auth-modal")?.classList.add("hidden");
        document.getElementById("album-inconsistency-modal")?.classList.add("hidden");
      }
    });
  }

  startLastFmPolling() {
    this.stopLastFmPolling();
    this.lastFmPollingActive = true;
    const statusText = document.getElementById("lastfm-auth-status-text");
    if (statusText) statusText.textContent = "Waiting for approval on Last.fm... (polling every 2s)";

    this.lastFmPollInterval = setInterval(async () => {
      if (!this.lastFmPollingActive || !this.pendingLastFmToken) {
        this.stopLastFmPolling();
        return;
      }
      await this.checkLastFmAuthSession();
    }, 2000);
  }

  stopLastFmPolling() {
    this.lastFmPollingActive = false;
    if (this.lastFmPollInterval) {
      clearInterval(this.lastFmPollInterval);
      this.lastFmPollInterval = null;
    }
  }

  async checkLastFmAuthSession(isManual = false) {
    if (!this.pendingLastFmToken) return;
    try {
      const res = await api.completeLastFmAuth(this.pendingLastFmToken);
      if (res && res.authenticated) {
        this.stopLastFmPolling();
        document.getElementById("lastfm-auth-modal")?.classList.add("hidden");
        this.showToast(`Connected to Last.fm as @${res.username}!`);
        await this.loadSettingsIntoUI();
      } else if (isManual) {
        this.showToast("Still waiting for authorization on Last.fm. Please approve in your browser.", "info");
      }
    } catch (err) {
      if (isManual) {
        this.showToast(`Last.fm connection error: ${err.message}`, "error");
      }
    }
  }

  async addMultiSelectToPlaylist() {
    if (this.selectedTrackIds.size === 0) return;
    await this.loadPlaylists();
    if (!this.playlists.length) {
      this.openCreatePlaylistModal();
      return;
    }
    const plNames = this.playlists.map((p, i) => `${i + 1}. ${p.name}`).join("\n");
    const choice = prompt(`Select playlist number to add ${this.selectedTrackIds.size} track(s):\n\n${plNames}`);
    const num = parseInt(choice, 10);
    if (isNaN(num) || num < 1 || num > this.playlists.length) return;
    const targetPl = this.playlists[num - 1];
    try {
      await api.request(`/api/playlists/${targetPl.id}/tracks`, {
        method: "POST",
        body: JSON.stringify({ track_ids: Array.from(this.selectedTrackIds) }),
      });
      this.showToast(`Added ${this.selectedTrackIds.size} tracks to "${targetPl.name}"!`);
      this.toggleMultiSelectMode();
    } catch (err) {
      this.showToast(`Failed adding to playlist: ${err.message}`, "error");
    }
  }

  formatTime(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const rem = String(s % 60).padStart(2, "0");
    return `${m}:${rem}`;
  }

  formatDuration(sec) {
    return this.formatTime(sec);
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