"use strict";
// Offline copy of the browser build. tools/build_web.py fills in VERSION and FILES.
// Every file is cached on install, so the app opens with no connection after the first visit.
// Same-site GET requests are served from the cache first; anything else goes to the network.
const VERSION = "8a7619d13c84";
const CACHE = "triody-" + VERSION;
const FILES = ["./", "index.html", "manifest.webmanifest", "triody_bundle.zip", "app.js", "boot.js", "style.css", "icon.svg", "icons/icon-192.png", "icons/icon-512.png", "icons/maskable-512.png", "icons/apple-touch-icon.png", "pyodide/pyodide.js", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "pyodide/pyodide-lock.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("triody-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, { ignoreSearch: req.mode === "navigate" });
    if (hit) return hit;
    try {
      const res = await fetch(req);
      if (res.ok && res.type === "basic") cache.put(req, res.clone());
      return res;
    } catch (e) {
      if (req.mode === "navigate") {
        const page = await cache.match("./");
        if (page) return page;
      }
      return new Response("Offline and not cached yet.", { status: 503, headers: { "Content-Type": "text/plain" } });
    }
  })());
});
