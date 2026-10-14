/**
 * IndexedDB + Cache API offline storage manager for Android PWA caching and offline scrobble outbox.
 */
const DB_NAME = "musiclinker_pwa_db";
const DB_VERSION = 1;
const AUDIO_CACHE_NAME = "musiclinker-audio-v2.1";

class OfflineStore {
  constructor() {
    this.dbPromise = this._initDB();
  }

  _initDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (evt) => {
        const db = evt.target.result;
        if (!db.objectStoreNames.contains("offline_tracks")) {
          db.createObjectStore("offline_tracks", { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains("offline_scrobbles")) {
          db.createObjectStore("offline_scrobbles", { keyPath: "id", autoIncrement: true });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async saveTrackForOffline(track, pairToken = "") {
    const fileUrl = `/api/tracks/${track.id}/file`;
    const headers = pairToken ? { "X-Pair-Token": pairToken } : {};
    const response = await fetch(fileUrl, { headers });
    if (!response.ok) {
      throw new Error(`Failed to fetch audio blob (${response.status})`);
    }
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.put(fileUrl, response.clone());

    if (track.cover_url) {
      fetch(track.cover_url).catch(() => {});
    }
    if (track.lyrics_url) {
      fetch(track.lyrics_url, { headers }).catch(() => {});
    }

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").put({ ...track, saved_offline_at: Date.now() });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async removeOfflineTrack(trackId) {
    const fileUrl = `/api/tracks/${trackId}/file`;
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.delete(fileUrl);

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").delete(trackId);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async getOfflineTracks() {
    const db = await this.dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readonly");
      const req = tx.objectStore("offline_tracks").getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async queueOfflineScrobble(scrobble) {
    const db = await this.dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readwrite");
      tx.objectStore("offline_scrobbles").add(scrobble);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async flushOfflineScrobbles(apiClient) {
    const db = await this.dbPromise;
    const pending = await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readonly");
      const req = tx.objectStore("offline_scrobbles").getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
    if (!pending.length) {
      return 0;
    }
    const cleanPayload = pending.map(({ artist, title, album, duration, timestamp, track_id }) => ({
      track_id,
      artist,
      title,
      album,
      duration,
      timestamp,
    }));
    await apiClient.request("/api/player/scrobble/batch", {
      method: "POST",
      body: JSON.stringify({ scrobbles: cleanPayload }),
    });
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_scrobbles", "readwrite");
      tx.objectStore("offline_scrobbles").clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    return pending.length;
  }
}

export const offlineStore = new OfflineStore();