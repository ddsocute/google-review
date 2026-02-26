// NOTE (2026-02-28):
// This project is currently NOT using PWA.
// However, some browsers may still have an old Service Worker registered (possibly with scope "/"),
// which can keep serving stale cached HTML/CSS/JS and block you from seeing latest progress.
//
// This "cleanup" Service Worker:
// - does NOT cache anything
// - deletes ALL Cache Storage entries
// - unregisters itself on activation
// - asks open tabs to reload once

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch (_) {
      // ignore
    }

    try {
      await self.registration.unregister();
    } catch (_) {
      // ignore
    }

    try {
      const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      clients.forEach((c) => {
        try { c.navigate(c.url); } catch (_) { /* ignore */ }
      });
    } catch (_) {
      // ignore
    }
  })());
});

// Never intercept requests. Let the network handle everything.
self.addEventListener("fetch", () => {});

