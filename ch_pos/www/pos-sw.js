/**
 * CH POS — Service Worker v2
 *
 * Strategy:
 *   App shell (HTML, JS bundles, CSS) → Cache-first, background revalidate
 *   Frappe API calls (/api/method/*)  → Network-first, cache fallback
 *   Everything else                  → Network-only
 *
 * Cache names are versioned so old caches are purged on activate.
 */

const SHELL_CACHE = "ch-pos-shell-v3";
const API_CACHE   = "ch-pos-api-v1";

// Assets that must be available offline for the POS to boot.
// These are fetched and cached during SW install.
const SHELL_ASSETS = [
  "/app/ch-pos-app",
  "/assets/frappe/css/frappe-web.bundle.css",
  "/assets/ch_pos/css/pos_variables.css",
  "/assets/ch_pos/css/pos_layout.css",
  "/assets/ch_pos/css/pos_components.css",
];

// API paths that are safe to serve from cache when offline.
const CACHEABLE_API_PATHS = [
  "/api/method/ch_pos.api.pos_api.get_pos_profile_data",
  "/api/method/ch_pos.api.pos_api.get_sale_types",
  "/api/method/ch_pos.api.search.pos_item_search",
  "/api/method/ch_pos.api.offline_sync.get_full_item_catalog",
];

// ── Install: pre-cache app shell ─────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => {
      // Best-effort: don't block install if an asset fails
      return Promise.allSettled(
        SHELL_ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn("[CH POS SW] Could not pre-cache:", url, err);
          })
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// ── Activate: purge old caches ────────────────────────────────────────────────
self.addEventListener("activate", (event) => {
  const keep = [SHELL_CACHE, API_CACHE];
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !keep.includes(k))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: routing logic ───────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET from same origin
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  const path = url.pathname;

  // App shell → cache-first, update in background
  if (path === "/app/ch-pos-app" || _is_shell_asset(path)) {
    event.respondWith(_cache_first_with_revalidate(request, SHELL_CACHE));
    return;
  }

  // JS/CSS bundles → cache-first (hash-named, so cache never stales)
  if (_is_versioned_bundle(path)) {
    event.respondWith(_cache_first_permanent(request, SHELL_CACHE));
    return;
  }

  // Frappe API → network-first, fallback to cache
  if (_is_cacheable_api(path)) {
    event.respondWith(_network_first(request, API_CACHE));
    return;
  }

  // All other API calls → network-only (mutations must not be cached)
  if (path.startsWith("/api/")) return;

  // Frappe desk pages → network-only (auth-sensitive)
});

// ── Background Sync: flush queued invoices ────────────────────────────────────
self.addEventListener("sync", (event) => {
  if (event.tag === "pos-invoice-sync") {
    event.waitUntil(_notify_clients("sync:bg_sync_triggered"));
  }
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function _is_shell_asset(path) {
  return SHELL_ASSETS.some((a) => a !== "/app/ch-pos-app" && path === a);
}

function _is_versioned_bundle(path) {
  // Frappe bundles include a hash: ch_pos.bundle.6PMQW57I.js
  return (
    (path.startsWith("/assets/") || path.startsWith("/files/")) &&
    (path.endsWith(".js") || path.endsWith(".css")) &&
    /\.[A-Z0-9]{6,}\./.test(path)
  );
}

function _is_cacheable_api(path) {
  return CACHEABLE_API_PATHS.some((p) => path.startsWith(p));
}

/** Cache-first, stale-while-revalidate. */
async function _cache_first_with_revalidate(request, cache_name) {
  const cache = await caches.open(cache_name);
  const cached = await cache.match(request);
  const network_fetch = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => null);
  return cached || await network_fetch || new Response("Offline", { status: 503 });
}

/** Cache-first, never revalidate (content-addressed bundles). */
async function _cache_first_permanent(request, cache_name) {
  const cache = await caches.open(cache_name);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

/** Network-first with cache fallback (API data). */
async function _network_first(request, cache_name) {
  const cache = await caches.open(cache_name);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    return cached || new Response(
      JSON.stringify({ message: null, exc: "Offline" }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }
}

async function _notify_clients(event_name) {
  const clients = await self.clients.matchAll({ type: "window" });
  clients.forEach((client) => client.postMessage({ type: event_name }));
}
