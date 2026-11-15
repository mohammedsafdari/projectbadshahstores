/*
 * Badshah Store service worker (D91).
 *
 * This file is a Django template, not a static file: every address, cache name
 * and list below is filled in by apps/storefront/pwa_views.py.
 *
 * SAFETY RULE: the worker stores the offline page and files under the static
 * URL, and nothing else. Never store an HTML page, a product photo, JSON or
 * anything that depends on who is asking or changes with a price or a stock
 * level (D90, D91).
 */
"use strict";

// Each value below arrives from the view already written as a JavaScript
// literal (quotes and escapes included), which is why it needs no quoting here.
const OFFLINE_URL = {{ offline_url|safe }};
const STATIC_PREFIX = {{ static_prefix|safe }};
const OFFLINE_CACHE = {{ offline_cache|safe }};
const STATIC_CACHE = {{ static_cache|safe }};
const CACHE_PREFIX = {{ cache_prefix|safe }};
const CURRENT_CACHES = [OFFLINE_CACHE, STATIC_CACHE];
const NEVER_INTERCEPT_PREFIXES = {{ never_intercept_prefixes|safe }};
const STATIC_DESTINATIONS = ["style", "script", "image", "font"];

// One refresh per worker start is enough: a changed shop phone number reaches
// the stored offline page as soon as the visitor opens any page.
let offlinePageRefreshed = false;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(OFFLINE_CACHE)
      // "reload" skips the browser's own HTTP cache, so this copy is the
      // shop's current page and not one a proxy kept.
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: "reload" })))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            // Only this worker's own old caches go; a cache that does not carry
            // the prefix belongs to something else and is left alone.
            .filter((name) => name.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.includes(name))
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  if (NEVER_INTERCEPT_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(navigationFirst(event));
    return;
  }
  if (url.pathname.startsWith(STATIC_PREFIX) && STATIC_DESTINATIONS.includes(request.destination)) {
    event.respondWith(staleWhileRevalidate(event));
  }
});

// A page always comes from the network: it may show a price or a stock level,
// so a stored copy would be wrong. The stored offline page is the answer only
// when the network call itself fails; a redirect or an error reply is passed
// straight through untouched.
async function navigationFirst(event) {
  try {
    const response = await fetch(event.request);
    event.waitUntil(refreshOfflinePage());
    return response;
  } catch (error) {
    const offlineCache = await caches.open(OFFLINE_CACHE);
    const stored = await offlineCache.match(OFFLINE_URL);
    return stored || Response.error();
  }
}

// Cache write 1 of 2: the offline page, refreshed once per worker start.
function refreshOfflinePage() {
  if (offlinePageRefreshed) {
    return Promise.resolve();
  }
  offlinePageRefreshed = true;
  return fetch(new Request(OFFLINE_URL, { cache: "reload" }))
    .then((response) => {
      if (!response.ok) {
        return undefined;
      }
      return caches.open(OFFLINE_CACHE).then((cache) => cache.put(OFFLINE_URL, response));
    })
    .catch(() => undefined);
}

// A stylesheet, a script, an image or a font never shows a price or a stock
// level, so the stored copy may be served at once and refreshed behind it.
async function staleWhileRevalidate(event) {
  const cache = await caches.open(STATIC_CACHE);
  const stored = await cache.match(event.request);
  const refreshed = fetch(event.request)
    .then((response) => {
      // Cache write 2 of 2: only a good, same-origin reply is stored, never a
      // redirect, an error page or an opaque cross-origin reply.
      if (!response.ok || response.type !== "basic") {
        return response;
      }
      return cache.put(event.request, response.clone()).then(() => response);
    })
    .catch(() => undefined);
  if (stored) {
    event.waitUntil(refreshed);
    return stored;
  }
  const fresh = await refreshed;
  return fresh || Response.error();
}
