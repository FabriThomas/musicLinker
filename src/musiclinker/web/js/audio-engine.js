/**
 * HTML5 Audio + APK Offline Blob Playback + ReplayGain Volume Normalization + Scrobble Tracker + MediaSession Bridge.
 */
import { offlineStore } from "./offline-store.js";

export const EQ_PRESETS = {
  Flat: [0, 0, 0, 0, 0],
  "Bass Boost": [6, 4, 1, 0, 0],
  "Treble Boost": [0, 0, 1, 4, 6],
  Rock: [4, 2, -1, 2, 4],
  Electronic: [5, 3, 0, 2, 4],
  Vocal: [-2, 1, 4, 3, 1],
  "Hip-Hop": [6, 5, 0, 1, 2],
  Acoustic: [2, 1, 2, 3, 2],
};

export const EQ_BANDS = [
  { label: "60 Hz", name: "Sub-Bass", freq: 60, type: "lowshelf" },
  { label: "230 Hz", name: "Bass", freq: 230, type: "peaking" },
  { label: "910 Hz", name: "Mids", freq: 910, type: "peaking" },
  { label: "3.6 kHz", name: "Presence", freq: 3600, type: "peaking" },
  { label: "14 kHz", name: "Air / Treble", freq: 14000, type: "highshelf" },
];

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

    // Web Audio API DSP Equalizer State
    this.audioCtx = null;
    this.sourceNode = null;
    this.eqFilters = [];
    this.eqEnabled = true;
    this.currentEqPreset = "Flat";
    this.eqGains = [0, 0, 0, 0, 0];

    this._loadSavedEq();
    this._bindEvents();
  }

  _loadSavedEq() {
    try {
      const saved = localStorage.getItem("ml_eq_state");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (typeof parsed.enabled === "boolean") this.eqEnabled = parsed.enabled;
        if (parsed.preset && EQ_PRESETS[parsed.preset]) this.currentEqPreset = parsed.preset;
        if (Array.isArray(parsed.gains) && parsed.gains.length === 5) {
          this.eqGains = parsed.gains.map(g => Number(g) || 0);
        }
      }
    } catch (_e) {}
  }

  _saveEq() {
    try {
      localStorage.setItem("ml_eq_state", JSON.stringify({
        enabled: this.eqEnabled,
        preset: this.currentEqPreset,
        gains: this.eqGains,
      }));
    } catch (_e) {}
  }

  initAudioContext() {
    if (this.audioCtx) {
      if (this.audioCtx.state === "suspended") {
        this.audioCtx.resume();
      }
      return;
    }
    try {
      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtxClass) return;
      this.audioCtx = new AudioCtxClass();
      this.sourceNode = this.audioCtx.createMediaElementSource(this.audio);

      this.eqFilters = EQ_BANDS.map((band, idx) => {
        const filter = this.audioCtx.createBiquadFilter();
        filter.type = band.type;
        filter.frequency.value = band.freq;
        if (band.type === "peaking") {
          filter.Q.value = 1.0;
        }
        filter.gain.value = this.eqEnabled ? this.eqGains[idx] : 0;
        return filter;
      });

      let prevNode = this.sourceNode;
      for (const filter of this.eqFilters) {
        prevNode.connect(filter);
        prevNode = filter;
      }
      prevNode.connect(this.audioCtx.destination);
    } catch (err) {
      console.warn("AudioContext equalizer initialization failed:", err);
    }
  }

  setBandGain(bandIdx, gainDb) {
    if (bandIdx < 0 || bandIdx >= 5) return;
    const clamped = Math.max(-12, Math.min(12, Number(gainDb) || 0));
    this.eqGains[bandIdx] = clamped;
    this.currentEqPreset = "Custom";

    if (this.audioCtx && this.eqFilters[bandIdx]) {
      this.eqFilters[bandIdx].gain.value = this.eqEnabled ? clamped : 0;
    }
    this._saveEq();
  }

  setPreset(presetName) {
    const preset = EQ_PRESETS[presetName];
    if (!preset) return;
    this.currentEqPreset = presetName;
    this.eqGains = [...preset];

    if (this.audioCtx && this.eqFilters.length === 5) {
      this.eqFilters.forEach((f, idx) => {
        f.gain.value = this.eqEnabled ? this.eqGains[idx] : 0;
      });
    }
    this._saveEq();
  }

  toggleEq(enabled) {
    this.eqEnabled = typeof enabled === "boolean" ? enabled : !this.eqEnabled;
    if (this.audioCtx && this.eqFilters.length === 5) {
      this.eqFilters.forEach((f, idx) => {
        f.gain.value = this.eqEnabled ? this.eqGains[idx] : 0;
      });
    }
    this._saveEq();
    return this.eqEnabled;
  }

  getEqState() {
    return {
      enabled: this.eqEnabled,
      preset: this.currentEqPreset,
      gains: [...this.eqGains],
    };
  }

  _bindEvents() {
    this.audio.addEventListener("play", () => {
      this.initAudioContext();
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