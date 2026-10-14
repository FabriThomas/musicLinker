const STATIC_CACHE = "musiclinker-static-v2.1";
const AUDIO_CACHE = "musiclinker-audio-v2.1";

const APP_SHELL = [
  "/",
  "/manifest.json",
  "/icon.svg",
  "/css/styles.css",
  "/js/api.js",
  "/js/offline-store.js",
  "/js/audio-engine.js",
  "/js/app.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== AUDIO_CACHE)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (url.pathname.endsWith("/file") || url.pathname.endsWith("/stream")) {
    const normalizedFilePath = url.pathname.replace(/\/stream$/, "/file");
    event.respondWith(
      caches.open(AUDIO_CACHE).then(async (audioCache) => {
        const cachedResponse = await audioCache.match(normalizedFilePath);
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request);
      })
    );
    return;
  }

  if (url.pathname.endsWith("/cover") || url.pathname.endsWith("/lyrics")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        try {
          const networkResp = await fetch(event.request);
          if (networkResp && networkResp.ok) {
            cache.put(event.request, networkResp.clone());
          }
          return networkResp;
        } catch (_err) {
          const fallback = await cache.match(event.request);
          return fallback || new Response("{}", { status: 404 });
        }
      })
    );
    return;
  }

  if (event.request.method === "GET" && !url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.match(event.request).then((r) => r || caches.match("/")))
    );
  }
});