# Vendored frontend assets

This directory holds pinned third-party assets loaded directly by the templates.
Do not fetch them at runtime from a CDN in production; the CDN is a Phase 1
development placeholder only.

## Files

- `vendor/htmx/htmx-2.0.4.min.js`
  - Version: 2.0.4
  - Source: https://unpkg.com/htmx@2.0.4/dist/htmx-2.0.4.min.js
  - SHA256: `<HASH_HTMX>`

- `vendor/alpine/alpine-3.14.8.min.js`
  - Version: 3.14.8
  - Source: https://unpkg.com/alpinejs@3.14.8/dist/alpine-3.14.8.min.js
  - SHA256: `<HASH_ALPINE>`

Replace the placeholder hashes with the actual SHA256 of the vendored files
before launch.
