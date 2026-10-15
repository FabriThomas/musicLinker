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
    const artistInput = document.getElementById("dl-album-artist");
    const albumInput = document.getElementById("dl-album-title");
    const artist = artistInput?.value.trim() || "";
    const album = albumInput?.value.trim() || "";
    const box = document.getElementById("dl-album-preview-list");
    if (!album && !artist) {
      alert("Enter an Album name, Spotify URL, or YouTube Album/Playlist URL.");
      return;
    }
    if (box) box.innerHTML = `<div class="text-xs text-zinc-400 p-2">Looking up tracklist from Spotify / MusicBrainz / YouTube Music...</div>`;

    try {
      const qs = new URLSearchParams({ artist, album });
      const res = await api.request(`/api/albums/lookup?${qs.toString()}`);
      this.previewedAlbumTracks = res.tracks || [];
      if (!this.previewedAlbumTracks.length) {
        if (box) box.innerHTML = `<div class="text-xs text-amber-400 p-2">No tracks found. Try entering Artist and Album name or a direct Spotify/YouTube link.</div>`;
        return;
      }

      // Auto-fill artist if empty
      if (res.artist && artistInput && !artistInput.value.trim()) {
        artistInput.value = res.artist;
      }

      if (box) {
        const coverBadge = res.cover_url
          ? `<div class="flex items-center gap-2 pb-1.5 border-b border-zinc-800 text-[11px] text-zinc-400">
               <img src="${this.escape(res.cover_url)}" class="w-8 h-8 rounded object-cover border border-zinc-700" alt="Cover" />
               <div class="truncate">
                 <div class="font-semibold text-zinc-200 truncate">${this.escape(res.album || "Album")}</div>
                 <div class="text-emerald-400 text-[10px] uppercase font-mono">${this.escape(res.source || "online")} • ${res.tracks.length} tracks</div>
               </div>
             </div>`
          : "";

        box.innerHTML = coverBadge + this.previewedAlbumTracks
          .map(
            (tr, idx) => `
            <label class="flex items-center justify-between p-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs hover:border-zinc-700 cursor-pointer transition">
              <div class="flex items-center gap-2 truncate">
                <input type="checkbox" data-dl-album-track="${idx}" checked class="accent-emerald-500" />
                <span class="font-mono text-emerald-400 text-[11px]">#${String(tr.track_number || idx + 1).padStart(2, "0")}</span>
                <span class="truncate font-medium">${this.escape(tr.title)}</span>
                ${tr.artist && tr.artist !== res.artist ? `<span class="text-zinc-500 text-[10px] truncate">— ${this.escape(tr.artist)}</span>` : ""}
              </div>
              ${tr.duration ? `<span class="font-mono text-[10px] text-zinc-500 ml-2">${this.formatDuration(tr.duration)}</span>` : ""}
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
      await this.saveSettings(true);
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
      alert(`AI Agent error: ${err.message}`);
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
      setVal("proxy-name", cfg.proxy_name || "default");
      setVal("proxy-server-url", cfg.proxy_server_url || "");
      setVal("proxy-password", cfg.proxy_password || "");
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

    // Jobs, Full Album Downloader, LLM Agent Curation & APK Pairing
    document.getElementById("btn-start-extract")?.addEventListener("click", () => this.startExtractionJob());
    document.getElementById("btn-preview-album")?.addEventListener("click", () => this.previewAlbumForDownload());
    document.getElementById("btn-download-album")?.addEventListener("click", () => this.startAlbumDownloadJob());
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
    document.getElementById("btn-save-apk-server")?.addEventListener("click", () => this.saveApkServerConnection());
    document.getElementById("btn-save-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-llm-settings")?.addEventListener("click", () => this.saveSettings(false));
    document.getElementById("btn-save-proxy-settings")?.addEventListener("click", () => this.saveSettings(false));
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