/**
 * Authenticated API client and SSE job stream subscriber.
 */
class MusicLinkerAPI {
  constructor() {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("pair_token");
    if (urlToken) {
      localStorage.setItem("ml_pair_token", urlToken);
      window.history.replaceState({}, document.title, "/");
    }
  }

  getPairToken() {
    return localStorage.getItem("ml_pair_token") || "";
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

    const resp = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  subscribeJobEvents(jobId, onUpdate) {
    const token = encodeURIComponent(this.getPairToken());
    const url = `/api/jobs/${jobId}/events${token ? `?pair_token=${token}` : ""}`;
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