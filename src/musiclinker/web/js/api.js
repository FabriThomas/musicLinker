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

  isCapacitor() {
    return Boolean(
      window.Capacitor?.isNativePlatform?.() ||
      window.Capacitor !== undefined ||
      window.location.protocol === "capacitor:" ||
      window.location.protocol === "file:" ||
      (window.location.hostname === "localhost" && !window.location.port)
    );
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

    const origin = this.getServerOrigin();
    if (this.isCapacitor() && !origin && !path.startsWith("http")) {
      window.dispatchEvent(new CustomEvent("ml:show-connect-modal", { detail: { reason: "APK requires PC server connection." } }));
      throw new Error("APK is not connected to your PC server. Tap 'Connect PC' to enter your PC IP (e.g. http://192.168.1.X:8101).");
    }

    const fullUrl = this.resolveUrl(path);
    let resp;
    try {
      resp = await fetch(fullUrl, {
        ...options,
        headers,
      });
    } catch (networkErr) {
      if (this.isCapacitor()) {
        window.dispatchEvent(new CustomEvent("ml:show-connect-modal", { detail: { reason: "Cannot reach PC server." } }));
        throw new Error(`Cannot reach PC server at ${origin || "localhost"}. Verify your PC is running and on the same Wi-Fi.`);
      }
      throw networkErr;
    }

    const contentType = resp.headers.get("content-type") || "";
    const rawText = await resp.text();

    if (rawText.trim().startsWith("<!DOCTYPE") || rawText.trim().startsWith("<html") || contentType.includes("text/html")) {
      if (this.isCapacitor() || !origin) {
        window.dispatchEvent(new CustomEvent("ml:show-connect-modal", { detail: { reason: "PC Server returned HTML / Not connected" } }));
        throw new Error("APK is not connected to your PC server. Tap 'Connect PC' to enter your PC IP (e.g. http://192.168.1.X:8101).");
      }
      throw new Error(`Server returned HTML instead of JSON for ${path}. (Check if server is running on correct port)`);
    }

    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const errJson = JSON.parse(rawText);
        detail = errJson.detail || errJson.message || detail;
      } catch (_e) {
        if (rawText) detail = rawText.slice(0, 150);
      }
      throw new Error(detail);
    }

    try {
      return JSON.parse(rawText);
    } catch (err) {
      throw new Error(`Invalid JSON response: ${err.message}`);
    }
  }

  // ---- Audio Candidates & Audio Replacement ----

  async getTrackCandidates(trackId, query = "", maxResults = 6) {
    const qParam = query ? `&q=${encodeURIComponent(query)}` : "";
    return this.request(`/api/tracks/${trackId}/candidates?max_results=${maxResults}${qParam}`);
  }

  getCandidatePreviewUrl(videoId) {
    return this.resolveUrl(`/api/tracks/candidate-preview/${encodeURIComponent(videoId)}`);
  }

  async replaceTrackAudio(trackId, youtubeUrl, bitrateKbps = 320) {
    return this.request(`/api/tracks/${trackId}/replace-audio`, {
      method: "POST",
      body: JSON.stringify({ youtube_url: youtubeUrl, bitrate_kbps: bitrateKbps }),
    });
  }

  async getSwapStatus(trackId) {
    return this.request(`/api/tracks/${trackId}/swap-status`);
  }

  async autoFixDatabase() {
    return this.request("/api/library/auto-fix", { method: "POST" });
  }

  async getAlbumInconsistencies() {
    return this.request("/api/albums/inconsistencies");
  }

  async resolveAlbumInconsistency(payload) {
    return this.request("/api/albums/resolve-inconsistency", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }


  // ---- Last.fm Imbued Scrobbler Methods ----

  async startLastFmAuth() {
    return this.request("/api/lastfm/auth/start", { method: "POST" });
  }

  async completeLastFmAuth(token) {
    return this.request(`/api/lastfm/auth/complete?token=${encodeURIComponent(token)}`, { method: "POST" });
  }

  async disconnectLastFm() {
    return this.request("/api/lastfm/disconnect", { method: "POST" });
  }

  async flushPendingScrobbles() {
    return this.request("/api/lastfm/scrobble-pending", { method: "POST" });
  }

  // ---- Bulk Album Matching & Assignment ----

  async matchAlbumTracklist(album, artist, trackIds) {
    return this.request("/api/albums/match-tracklist", {
      method: "POST",
      body: JSON.stringify({ album, artist: artist || "", track_ids: trackIds }),
    });
  }

  async bulkAssignAlbum(album, albumArtist, releaseYear, coverUrl, trackAssignments) {
    return this.request("/api/tracks/bulk-assign-album", {
      method: "POST",
      body: JSON.stringify({
        album,
        album_artist: albumArtist || "",
        release_year: parseInt(releaseYear, 10) || 0,
        cover_url: coverUrl || null,
        track_assignments: trackAssignments,
      }),
    });
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

  getCandidatePreviewUrl(videoId, duration = 60) {
    const clean = encodeURIComponent(videoId);
    return this.resolveUrl(`/api/tracks/candidate-preview/${clean}?duration=${duration}`);
  }

  async getTestModeStatus() {
    return this.request("/api/system/test-mode");
  }

  async setTestMode(enabled) {
    return this.request("/api/system/test-mode", {
      method: "POST",
      body: JSON.stringify({ enabled: Boolean(enabled) }),
    });
  }

  async clearTestModeLogs() {
    return this.request("/api/system/test-mode/clear", { method: "POST" });
  }

  async getTestModeEvents(sinceSeq = 0, category = "") {
    let url = `/api/system/test-mode/events?since=${sinceSeq}`;
    if (category) url += `&category=${encodeURIComponent(category)}`;
    return this.request(url);
  }
}

export const api = new MusicLinkerAPI();