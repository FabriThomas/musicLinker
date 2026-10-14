/**
 * IndexedDB + Cache API offline storage manager for Android APK / PWA:
 *  - Caches single tracks or entire albums for 100% offline playback
 *  - Serves direct Blob URLs when offline inside the native Android APK
 *  - Queues offline scrobbles in IndexedDB and flushes them in batch when reconnected
 */
const DB_NAME = "musiclinker_pwa_db";
const DB_VERSION = 1;
const AUDIO_CACHE_NAME = "musiclinker-audio-v3.0";

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

  canonicalCacheKey(trackId) {
    return `/api/tracks/${trackId}/file`;
  }

  async saveTrackForOffline(track, apiClient) {
    const canonicalKey = this.canonicalCacheKey(track.id);
    const fetchUrl = apiClient.resolveUrl(canonicalKey);
    const token = apiClient.getPairToken();
    const headers = token ? { "X-Pair-Token": token } : {};

    const response = await fetch(fetchUrl, { headers });
    if (!response.ok) {
      throw new Error(`Failed to fetch audio blob (${response.status})`);
    }
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.put(canonicalKey, response.clone());

    // Cache lyrics JSON inside track record so lyrics work 100% offline in the APK
    let offlineLyrics = null;
    if (track.lyrics_url) {
      try {
        offlineLyrics = await apiClient.request(track.lyrics_url);
      } catch (_err) {}
    }

    const db = await this.dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("offline_tracks", "readwrite");
      tx.objectStore("offline_tracks").put({
        ...track,
        offline_lyrics: offlineLyrics,
        saved_offline_at: Date.now(),
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async saveBatchTracksForOffline(tracks, apiClient, onProgress) {
    let saved = 0;
    for (let i = 0; i < tracks.length; i++) {
      await this.saveTrackForOffline(tracks[i], apiClient);
      saved++;
      onProgress?.(saved, tracks.length, tracks[i]);
    }
    return saved;
  }

  async getOfflineAudioBlobUrl(trackId) {
    try {
      const cache = await caches.open(AUDIO_CACHE_NAME);
      const match = await cache.match(this.canonicalCacheKey(trackId));
      if (!match) return null;
      const blob = await match.blob();
      return URL.createObjectURL(blob);
    } catch (_err) {
      return null;
    }
  }

  async getOfflineTrackRecord(trackId) {
    const db = await this.dbPromise;
    return new Promise((resolve) => {
      const tx = db.transaction("offline_tracks", "readonly");
      const req = tx.objectStore("offline_tracks").get(Number(trackId));
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
  }

  async removeOfflineTrack(trackId) {
    const cache = await caches.open(AUDIO_CACHE_NAME);
    await cache.delete(this.canonicalCacheKey(trackId));

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