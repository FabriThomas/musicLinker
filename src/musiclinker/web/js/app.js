/**
 * Main UI controller for MusicLinker Studio & Android APK v3.0.8:
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
    this.initEqualizerUI();
    await this.refreshOfflineState();
    await this.loadSettingsIntoUI();
    await this.loadAlbums();
    await this.loadLibrary("", true);
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

    const icon = type === "error" ? "⚠" : type === "warning" ? "⚡" : "✓";

    const toast = document.createElement("div");
    toast.className = `ml-toast pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border shadow-xl backdrop-blur-md text-xs font-medium ${colorClasses}`;
    toast.innerHTML = `<span class="font-bold text-sm">${icon}</span><span class="leading-snug">${this.escape(message)}</span>`;
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
  }

  renderTrackList() {
    const container = document.getElementById("track-list");
    if (!container) return;
    if (!this.tracks.length) {
      container.innerHTML = `
        <div class="p-10 text-center text-zinc-400 space-y-2.5">
          <div class="text-3xl">🎵</div>
          <div class="text-sm font-medium text-zinc-300">No tracks match the current filter.</div>
          <div class="text-xs text-zinc-500">Open the <b>⬇ Downloader</b> tab to grab any single song, Spotify/YouTube link, playlist, or full studio album.</div>
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

        return `
          <div class="ml-track-row group flex items-center justify-between p-2.5 rounded-xl border ${
            isCurrent
              ? "bg-emerald-950/50 border-emerald-500/45 shadow-sm"
              : "bg-zinc-900/30 border-zinc-800/40 hover:bg-zinc-800/65 hover:border-zinc-700/80"
          }">
            <div class="flex items-center gap-3 min-w-0 flex-1 cursor-pointer" data-play-idx="${idx}">
              <div class="w-11 h-11 rounded-lg bg-zinc-800 flex items-center justify-center overflow-hidden shrink-0 border border-zinc-700/60 relative">
                ${
                  coverSrc
                    ? `<img src="${coverSrc}" class="w-full h-full object-cover" alt="cover"/>`
                    : `<span class="text-emerald-400 font-bold">♪</span>`
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
                data-edit-idx="${idx}"
                title="Edit Track, Track #, or Album Metadata"
                class="ml-btn-icon w-7 h-7 text-xs">
                ✎
              </button>
              <button
                data-delete-idx="${idx}"
                title="Delete Track from Library & Disk"
                class="ml-btn-icon w-7 h-7 text-xs hover:!text-red-300 hover:!border-red-500/50 hover:!bg-red-950/70">
                🗑
              </button>
              <button
                data-offline-idx="${idx}"
                title="${isOffline ? "Saved for Offline (tap to remove)" : "Save for Offline (APK)"}"
                class="ml-btn px-2.5 py-1 rounded-lg text-xs font-medium border ${
                  isOffline
                    ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                    : "bg-zinc-800/80 border-zinc-700/80 text-zinc-300 hover:border-emerald-500/50 hover:text-emerald-300"
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
          this.loadLibrary(document.getElementById("search-input")?.value || "", false);
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
      if (btn) btn.textContent = "✓ Album Cached Offline";
      this.showToast(`Cached ${albumTracks.length} track(s) for offline playback.`);
    } catch (err) {
      this.showToast(`Failed caching album offline: ${err.message}`, "error");
      if (btn) btn.textContent = "↓ Cache Offline (APK)";
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
      if (btn) btn.textContent = `✓ Cached ${selected.length}`;
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
                    class="ml-btn-icon w-7 h-7 text-xs"
                    title="Copy metadata into override fields">
                    ✎
                  </button>
                  <button
                    data-dl-single-idx="${idx}"
                    class="ml-btn ml-btn-primary px-2.5 py-1.5 text-xs">
                    ⬇ Download
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
                  : `<div class="w-9 h-9 rounded-lg bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-300 font-bold shrink-0">💿</div>`
              }
              <div class="min-w-0">
                <div class="font-semibold text-zinc-100 truncate">${this.escape(res.album || "Collection")}</div>
                <div class="text-indigo-400 text-[10px] font-mono truncate">${this.escape(sourceLabel)} • ${this.escape(entityType)} • ${res.tracks.length} tracks</div>
              </div>
            </div>
            <button id="btn-toggle-all-album-tracks" type="button" class="ml-btn ml-btn-secondary px-2 py-1 text-[10px] shrink-0">
              ☑ Toggle All
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
      pauseBtn.textContent = "⏸ Pause";
    }
    if (retryBtn) retryBtn.classList.add("hidden");

    this.activeEventSource = api.subscribeJobEvents(jobId, async (state) => {
      // Discard callbacks from previous jobs
      if (this.activeJobId !== jobId) return;

      this.isJobPaused = Boolean(state.is_paused);
      this.isJobRunning = (state.status === "running" || state.status === "queued");
      if (pauseBtn) {
        pauseBtn.textContent = this.isJobPaused ? "▶ Resume" : "⏸ Pause";
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
      const currentTracksAdded = (state.tracks_added || []).length;
      if (currentTracksAdded > this.jobTracksSeenCount) {
        this.jobTracksSeenCount = currentTracksAdded;
        const searchVal = document.getElementById("search-input")?.value || "";
        this.loadLibrary(searchVal, true);
        this.loadAlbums();
      }

      if (state.status === "completed") {
        this.isJobRunning = false;
        this.showToast(`Download job complete (${state.downloaded_count} track(s) added)!`);
        if (pauseBtn) pauseBtn.classList.add("hidden");
        if (retryBtn) retryBtn.classList.add("hidden");
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
                class="ml-btn ml-btn-secondary px-2 py-0.5 text-[10px] ${isOffline ? "text-emerald-400 font-bold" : "text-zinc-400"}"
                title="Save track on device storage">
                ${isOffline ? "💾 Saved" : "↓ Device"}
              </button>
              <button
                data-pl-track-remove="${t.id}"
                class="ml-btn-icon w-6 h-6 text-xs hover:!bg-red-950 hover:!text-red-300"
                title="Remove from playlist">
                ✕
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
        btn.textContent = "↓ Save on Device";
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
    const label = playing ? "⏸" : "▶";
    if (btn) btn.textContent = label;
    if (carBtn) carBtn.textContent = playing ? "⏸ PAUSE" : "▶ PLAY";
    if (bigBtn) bigBtn.textContent = label;
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
        btn.innerHTML = `<span>Execute</span> <span>⚡</span>`;
      }
    }
  }

  renderCurationActions() {
    const listEl = document.getElementById("curator-actions-list");
    if (!listEl) return;
    if (!this.proposedCurationActions.length) {
      listEl.innerHTML = `<div class="p-4 text-xs text-zinc-400 text-center">✓ No pending actions. Type an instruction above for the AI agent!</div>`;
      return;
    }

    listEl.innerHTML = this.proposedCurationActions
      .map((act, idx) => {
        const isDup = act.action_type === "duplicate_remove";
        const isBulk = act.action_type === "bulk_album_rename";
        const badgeColor = isDup ? "text-rose-400" : isBulk ? "text-cyan-400" : "text-emerald-400";
        const badgeLabel = isDup
          ? "🗑 Remove Duplicate Version"
          : isBulk
          ? "💿 Bulk Album Rename"
          : "✨ Rename Track / Canonicalize Album";

        return `
          <label class="flex items-start gap-2.5 p-2.5 rounded-xl bg-zinc-950/80 border border-zinc-800 text-xs cursor-pointer hover:border-zinc-700">
            <input type="checkbox" data-curate-idx="${idx}" checked class="mt-0.5 accent-emerald-500" />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 font-semibold ${badgeColor}">
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
      await this.loadAlbumAliases();
    } catch (_err) {}
  }

  async saveSettings(silent = false) {
    const apiKey = document.getElementById("set-api-key")?.value.trim();
    const apiSecret = document.getElementById("set-api-secret")?.value.trim();
    const username = document.getElementById("set-username")?.value.trim();
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
        lastfm_username: username || undefined,
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
      this.engine.setPreset("Flat");
      this.refreshEqualizerModalValues();
      this.showToast("Equalizer reset to Flat");
    });
    document.getElementById("eq-enable-toggle")?.addEventListener("change", (e) => {
      const enabled = this.engine.toggleEq(e.target.checked);
      const label = document.getElementById("eq-enable-label");
      if (label) {
        label.textContent = enabled ? "DSP ON" : "DSP OFF";
        label.className = enabled ? "text-emerald-400 font-bold" : "text-zinc-500 font-bold";
      }
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

    // Global keyboard shortcuts
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (this.bigPlayerOpen) {
          this.toggleBigPlayer(false);
        }
        document.getElementById("export-list-modal")?.classList.add("hidden");
        document.getElementById("playlist-edit-modal")?.classList.add("hidden");
      }
    });
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