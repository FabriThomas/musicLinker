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