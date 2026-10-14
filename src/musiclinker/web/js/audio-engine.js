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