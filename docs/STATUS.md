# Badshah Store: Status

Start here in every new chat. Update this file at the end of every slice (see "Slice closeout checklist" below).

## Where we are

Phase 1 (scaffold) and Phase 2 (catalogue and inventory) are complete. Last finished slice: 28 (search fix and Phase 2 wrap-up, 28a and 28b). Next: Phase 3 (cart and checkout, COD first), per `PROJECT_INSTRUCTIONS.md` section 9.

Before every slice, Claude lists the open questions in `docs/open_questions.md` that are due (their "ask again before" point is that slice or earlier) and asks them first, with the default in use. This rule lives here and in `PROJECT_INSTRUCTIONS.md` so every new chat inherits it.

Questions due at the start of Slice 28: the nine items in `docs/open_questions.md` whose point was Slice 28 (pack weights for KitKat, Munch and Milkybar; the veg mark on the real products; the naming rule for price points; the filter-panel counts question; who fills `search_keywords`; the 15 unloaded billing rows; the rest-of-shop export; the rest of the category tree; and the two 1 kg Everyday milk powder rows). The owner chose all the defaults, and those items now ask again in Phase 3. The next "ask again before" points are Phase 3, Phase 5, Phase 6 and Phase 7. The defaults in use stay until then.

Since Slice 22 the coding is done by an OpenCode Desktop agent on the `main` branch only; see "How we work". Beam Free did Slices 22 to 25d; since Slice 25e the model is DeepSeek V4.1 Flash (free, high effort) because Beam Free had problems. The full suite runs in Docker on PostgreSQL.

Terminology: phase numbers come from the build plan in `PROJECT_INSTRUCTIONS.md` section 9. Where section 1 of that brief says "Phase 2 adds pan-India shipping", it means a later business phase, which is Phase 8 in the build plan.

## Starting a new chat

- Claude chat: attach `PROJECT_INSTRUCTIONS.md` and this file and write "Continue at Slice N (name)". Claude reads the repo itself (github.com/mohammedsafdari/projectbadshahstores), asks the due open questions first, then sends one prompt file per run.
- OpenCode chat: a new chat needs no history. Every prompt names its slice and tells the agent to read `AGENTS.md`, `docs/AGENT_BRIEF.md` and this file first. Model: DeepSeek V4.1 Flash (free, high).
- Python recap for a .NET developer: `docs/python_recap.md`. It shows each idiom with a real example from this repo.
- Before opening the new chats: the working tree is clean (`git status`), everything is pushed, the suite passes twice in Docker, and `docker compose exec web python manage.py migrate` has been run.

## What is built

- Accounts: custom user model, login by phone (Phase 1).
- Core: abstract `TimeStampedModel` and `ArchivableModel` (Slice 12).
- Catalog: `Brand`, `Category` (a tree through `parent`), `Product` and `ProductVariant`, with admin (variants editable on the product page), factories and tests (Slices 12 and 13). Pricing helpers in `apps/catalog/services.py` (Slice 13c).
- Catalog images: `ProductImage` (several photos per product, validated uploads, local media storage in dev) and an admin inline with a thumbnail (Slice 14, D41).
- Loose-by-weight and low stock on variants: `is_sold_loose`, `quantity_step` and an optional `low_stock_threshold`, with database constraints (Slice 15b and 15c, D42).
- Inventory: `Supplier` (name, phone, notes; archivable) and the append-only `InventoryMovement` ledger: decimal quantities, database sign rules per movement type, optional batch, expiry and supplier on purchases, and `AppendOnlyError` blocking updates and deletes through `save`, `delete`, queryset `update` and `delete`, and `bulk_update` (Slice 15d to 15f, D42). The admin screens are described in the inventory admin bullet below.
- Inventory services (`apps/inventory/services.py`, Slice 16, D43): `get_on_hand`, `receive_stock`, `adjust_stock`, `record_damage` and `count_stock`. Every write runs in `transaction.atomic()`, locks the variant row with `select_for_update()` and goes through one private `_record_movement`. Quantities are `Decimal` or `int` with at most 3 decimals, whole numbers for packet variants. Errors: `InventoryError`, `InvalidQuantityError`, `InsufficientStockError`. Concurrency tests prove the lock: removing it makes them fail.
- Integrity check: `find_integrity_problems()` in `apps/inventory/integrity.py` and the read-only command `check_inventory_integrity` (exit code 1 only on an ERROR). It is run by hand; nothing schedules it yet.
- Audit log (Slice 17, D44): append-only `core.AuditLog` (actor, action, target_type, target_id, changes as JSON `{field: [old, new]}`, note). `AppendOnlyError` now lives in `apps/core/models.py` (inventory imports it). One write path: `record_change` and `snapshot` in `apps/core/audit.py` (Decimal values are stored as strings, floats are refused, nothing is written when no value changed; call it inside the same transaction as the change). Read-only admin `AuditLogAdmin` (no add, change or delete; reading needs the `core.view_auditlog` permission, superusers have it). Variant helpers in `apps/catalog/audit.py` (`AUDITED_VARIANT_FIELDS`, `snapshot_variant_from_db`, `record_variant_change`, the formset helpers, `audit_actor`): editing an existing variant on its own admin page (`save_model`) or inside the product page (`save_formset`) writes a `VARIANT_UPDATE` row. Stock is not audited here; the ledger covers it.
- Inventory admin (Slice 18, D45), in `apps/inventory/admin.py`, `forms.py` and `templates/admin/inventory/stockitem/stock_form.html`:
  - Read-only `InventoryMovementAdmin` (the history: reasons, who, when; filters by type, supplier and date; search by SKU, product, reason and batch) and an ordinary `SupplierAdmin`.
  - The Stock screen: proxy model `StockItem` of `ProductVariant` (read-only) with on-hand from one annotated query, a low-stock marker and filter, and an Actions column: Receive, Adjust, Count, Damage and History. The four write pages are plain forms that call `receive_stock`, `adjust_stock`, `count_stock` and `record_damage`; they share one view, `_stock_form_view`. They need the Stock view permission and `inventory.add_inventorymovement` (superusers pass). History needs `inventory.view_inventorymovement` and opens the movement history for one variant (`?variant__id__exact=<id>`).
  - The Expiring lots screen: proxy model `ExpiringLot` of `InventoryMovement` (read-only), listing received lots with an expiry date whose variant still has stock, soonest first, with filters for already expired and 7, 30 and 60 days.
  - Migration `0003_stock_proxy_models` creates the two proxy models (no tables). `ProductVariantAdmin` is unchanged.
- CSV export (Slice 19, D46): `apps/catalog/csv_export.py` (`CSV_COLUMNS`, `variants_for_export`, `variant_to_row`, `write_variants_csv`) writes one row per variant with its product, brand and category into any file-like object. UTF-8 with a BOM (read it with `utf-8-sig`), plain decimals, `TRUE`/`FALSE` flags, and a formula guard that puts a `'` in front of a text cell starting with `=`, `+`, `-`, `@`, tab or carriage return. Admin actions: `export_csv` on the variant list and `export_variants_csv` on the product list, offered only to users with `catalog.view_productvariant` (`VariantCsvExportPermissionMixin`). No stock column.
- CSV import (Slice 20, D47): `apps/catalog/csv_import.py` reads the export format back and checks every cell without touching the database; `csv_import_plan.py` compares the clean rows with the database (CREATE / UPDATE / UNCHANGED per row, a CREATE for a missing product, and issues for an unknown brand or category slug, a SKU letter-case clash, a SKU moving to another product and a barcode another SKU already holds) and writes nothing; `csv_import_apply.py` locks products then variants in ascending `pk` order, plans again under the lock, and writes products then variants in one transaction with `full_clean()` and one audit row per created or changed variant (products are not audited). The admin page behind the "Import CSV" button on the variant list (`templates/admin/catalog/productvariant/`) uploads, shows the report, keeps the file in the session and writes only on Confirm, refusing when the fingerprint changed. A round-trip test proves an exported file comes back unchanged.
- Seed command (Slice 22, D49): `apps/catalog/seed.py` and `apps/catalog/management/commands/seed_catalog.py` load `nestle.csv` (and `demo.csv` with `--demo`) through the importer; brands and categories are created from the CSV slug/name pairs; `--demo-stock` receives demo stock once. Tests in `apps/catalog/tests/test_seed_catalog.py`.
- Storefront listing (Slice 23, D50): `apps/storefront/services.py` (`CategoryTree`, `visible_products`, `build_cards`, `ProductCard`) and three views in `apps/storefront/views.py`: `/products/`, `/category/<slug>/` and `/brand/<slug>/` (templates in `templates/storefront/`). A product is visible when it, its brand, its category and all that category's ancestors are active and it has an active variant. Sorting (name, price up or down, newest) and 24 products per page happen in the database; a card shows the cheapest active variant's price ("from" when there are several), the struck MRP and percent off when the price is lower, and an Out of stock badge (on-hand is the ledger sum). `ProductImage.thumbnail` (migration 0007) holds a 400 px WebP made on save (`apps/catalog/images.py`); cards fall back to the original photo, then to `static/img/product-placeholder.svg`. `Category.clean()` blocks parent cycles. `base.html` loads htmx with a script tag and links Home and Shop.
- Listing filters (Slice 24, D51): `apps/storefront/filters.py` (`ListingFilters`, `parse_filters`, `apply_filters`) and `apps/storefront/filter_options.py` (`brand_options`); the filter panel and results block in `templates/storefront/_filter_panel.html` and `_listing_results.html`; `views.py` reads the filters, returns only the results block for htmx requests and the whole page otherwise, with `Vary: HX-Request`. Filters: brand (repeatable), min/max price, own brand, in stock, vegetarian.
- Product detail page (Slice 25, D52): `/product/<slug>/`; `apps/storefront/product_detail.py` (`get_product_detail`, `VariantDetail`, `ProductDetail`) and `templates/storefront/product_detail.html`.
  - A hidden product (or a hidden brand, category or ancestor) gives 404 through the same `visible_products` rule (D52).
  - The page shows photos (main `medium`, the rest thumbnails), variants cheapest first with pack label, price, struck MRP, "Save ₹X (Y%)", unit price for kg/g/ml/l, stock from the ledger, "Product details" labels, four related products by name, and JSON-LD with absolute image URLs.
  - `ProductImage.medium` (900 px WebP, migration 0008) is made on save by `apps/catalog/images.py`; `backfill_image_sizes` fills it for existing photos (run once after deploying).
  - `SHOP_FSSAI_LICENCE` shows "FSSAI licence no. ..." only when it is non-empty and the product is our own brand; never invent a number.
- Search (Slice 26, D53): `apps/catalog/search.py` (`SearchHit`, `SearchBackend`, `normalise_query`, `build_prefix_tsquery`, `PostgresSearchBackend`, `get_search_backend`) behind the `SEARCH_BACKEND` setting; full-text on the `simple` configuration (every query word, last word a prefix), trigram word similarity and `icontains`; the storefront intersects the ranked ids with `visible_products` and sorts them with a database `Case/When`. `/search/` (`product_search`) has the listing filters, relevance sort, pagination, `noindex`, a no-results block and a header search box on every page; migration `0009_enable_pg_trgm` adds `pg_trgm` (no indexes yet).
- Search-as-you-type (Slice 27, D54): `/search/suggest/` (`search_suggest`) returns an HTML fragment with at most 6 products for a query of 2 or more characters, using the same backend, visibility rule and relevance order as `/search/`, with `noindex` and `no-store`; the header search box asks for it with htmx (250 ms debounce, `hx-sync` replace) and Alpine opens and closes the panel and moves focus with the arrow keys. Without JavaScript the plain form still submits `/search/`. No logging and no rate limit.
- Everything else in `docs/architecture.md` is target design only; the other app folders are empty shells.

## Slice log

| Slice | What changed |
| --- | --- |
| 1 to 10 | Phase 1 scaffold: settings split, Docker Compose, CI, pre-commit, custom user model, health endpoint, README, ADRs up to D35. |
| 11 | Removed the no-op `USE_X_FORWARDED_PROTO` setting (D36); README note about shared compose containers. |
| 12 | `core` base models; `Brand` and `Category`; admin, factories, tests; ASCII-only slugs (D37, D38). |
| 13 | `Product` and `ProductVariant` with database check constraints; admin with variant inline; pricing helpers; tests (D39, D40). |
| 13d | `docs/open_questions.md` updated with Phase 2 items. |
| 13e to 13i | Documentation sync: this file, ADRs D37 to D40, architecture, README, new open questions. |
| 14a to 14d | Pillow dependency and dev media URL; upload validators; `ProductImage` model, factory and tests; admin inline with thumbnail; Dockerfile makes media and staticfiles writable (D41). |
| 14e | Documentation sync: ADR D41, open questions, architecture, this file, agent brief. |
| 15a | ADR D42 (inventory decisions and loose-by-weight quantities); D23 narrowed. |
| 15b to 15c | `ProductVariant`: `is_sold_loose`, `quantity_step`, `low_stock_threshold`, with constraints. |
| 15d | `Supplier` model. |
| 15e | `InventoryMovement` ledger model with check constraints. |
| 15f | Append-only guard; tidy-up; test-writing rules and read-only checks added to `docs/AGENT_BRIEF.md`. |
| 15g | Documentation sync: this file, architecture, open questions. |
| 16a | ADR D43 (inventory services) and three open questions. |
| 16b | `services.py` core (validation, lock, single write path), `get_on_hand`, `receive_stock`, with tests; types tightened. |
| 16c | `adjust_stock`, `record_damage`, `count_stock`, with tests. |
| 16d | Concurrency tests (threads, `transaction=True`); thread-test rule in `docs/AGENT_BRIEF.md`. |
| 16e | `apps/inventory/integrity.py`: `find_integrity_problems`, with tests. |
| 16f | `check_inventory_integrity` management command, with tests. |
| 16g | Documentation sync: this file, ADR D43 corrections, README. |
| 17a | ADR D44 (AuditLog) and open questions. |
| 17b | `core.AuditLog` model, append-only guard, `AppendOnlyError` moved to `core`, factory and tests. |
| 17c | `apps/core/audit.py`: `record_change` and `snapshot`, with tests. |
| 17d | Read-only `AuditLogAdmin`, with tests (an unwanted `has_view_permission` override was removed). |
| 17e | `apps/catalog/audit.py` and `ProductVariantAdmin.save_model` write variant audit rows. |
| 17f | `ProductAdmin.save_formset` audits variants edited on the product page; tests post the real admin page. |
| 17g | Documentation sync: this file, ADR D44 additions, open questions. |
| 18a | ADR D45 (inventory admin) and open questions, applied as a patch. |
| 18b | Read-only `InventoryMovementAdmin`, with tests. |
| 18c | `SupplierAdmin`, with tests. |
| 18d | Proxy models `StockItem` and `ExpiringLot`, and their migration. |
| 18e | The Stock screen: on-hand column and low-stock filter, with tests. |
| 18f | `forms.py`, the shared stock page and the Receive page, with tests. |
| 18g | Adjust stock and Record damage pages, with tests. |
| 18h | Count stock page and the History links, with tests. |
| 18i | The Expiring lots screen, with tests. |
| 18j | Documentation sync: this file, ADR D45 additions, open questions. |
| 19a | `apps/catalog/csv_export.py`, the two admin export actions, with tests. |
| 19b | Documentation sync: this file, ADR D46, open questions. |
| 20a | ADR D47 (CSV import) and open questions with "ask again before" points. |
| 20b | `apps/catalog/csv_import.py`: parser and cell checks, no database (99 tests). Slice 20 is now split 20c plan (database checks), 20d apply, 20e admin page, 20f round trip and docs. |
| 20c | `apps/catalog/csv_import_plan.py`: plan step (database checks, five queries, fingerprint), with tests. |
| 20d | `apps/catalog/csv_import_apply.py`: lock, re-plan, write inside one transaction; `record_variant_created`; with tests including a lock test. |
| 20e | The "Import CSV" admin page (upload, report, Confirm) and the changelist button, with tests. |
| 20f | Round-trip tests (export then import unchanged, one price edit) and documentation. |
| 20g | Follow-ups: updated_at tests, docs rules. |
| 21a | `apps/catalog/price_update.py`: spec and plan, half-up rounding, and the all-or-nothing apply with audit rows. |
| 21b | Tests for the maths, row rules, fingerprint, audit rows and the lock. |
| 21c | The "Change prices of selected variants" action: price form, preview/Confirm flow and permission check. |
| 21d | `change_prices.html`: spec form, preview report and the Confirm form. |
| 21e | End-to-end admin tests; two fixed for Django's empty-changelist action bar and `DATA_UPLOAD_MAX_NUMBER_FIELDS`. |
| 21f | Documentation sync: this file, ADR D48. |
| 21 review | Claude pulled the repo, ran 754 tests on PostgreSQL, ruff and mypy; five deliberate breakages (locks, `updated_at`, fingerprint check, half-up rounding, audit call) were each caught. |
| 22 | `seed_catalog` management command loads seed data through the CSV importer; `apps/catalog/seed.py` (brands/categories from CSV, demo stock); tests; docs. |
| 22 follow-up | First slice run in OpenCode Desktop: Beam Free did the whole-slice prompt and a cleanup prompt, DeepSeek V4.1 Flash fixed one test. Seed run twice in the dev database: the first run created 40 and 48 variants, the second changed nothing. Review: a new test failed for the wrong reason (a 32-cell row against 31 columns) and was fixed. |
| 23 | Listing pages (`/products/`, category, brand), sorting, 24 per page, card thumbnails (migration 0007), category cycle check, htmx tag fix, ADR D50. The first run (Beam Free) added no tests. |
| 23 follow-up | Review found: no tests (767 before and after); two paragraphs of ADR D48 overwritten by the D50 edit (restored by Claude, D50 moved to the end); sorting and paging done in Python over the whole catalogue (moved into the database). Tests were added in three steps (795, 810, 828 passing). A last pass rewrote five weak view tests and gave the storefront tests their own temporary media folder. |
| 24a | `filters.py` parser and database filtering, `filter_options.py`, `visible_products(filters=...)`, 38 tests (866). |
| 24a follow-up | Review: two `brand_options` tests were too weak, one brand sat in both categories and the sort order matched by accident; rewritten, count unchanged. |
| 24b | Filter panel, htmx results block, `Vary` header, pagination `hx-` attributes, brand page rule, 24 tests (890); review: 19 of 22 deliberate breakages caught. |
| 24c | Three tests for the gaps review found (893); D51 and these docs. |
| 25 | Product detail page: `product_detail.py` and `product_detail.html`, `ProductImage.medium` (migration 0008) and the `backfill_image_sizes` command, `SHOP_FSSAI_LICENCE`, JSON-LD; 11 catalog tests + 46 storefront tests (950). D52. |
| 25d | Savings text "Save ₹X (Y%)" computed in the service, absolute JSON-LD image URLs via `build_url`, related products excluded and limited in the database, meta description cut at a word, 14 new tests (964). |
| 25e | Finished what 25d skipped (first run with DeepSeek V4.1 Flash): the 14 new tests, three rewritten weak tests and the STATUS text (964). Review: 44 of 45 deliberate breakages caught; the view's `request.build_absolute_uri` is untested (see "Where we are"). A 24b follow-up made the filter view tests compare product names instead of page substrings (a random CSRF token containing "P2" had made one fail). |
| 26 | Search behind an interface: `apps/catalog/search.py`, pg_trgm migration 0009, `/search/` with filters, relevance sort, pagination and `noindex`, header search form; 24 catalog + 20 storefront + 1 JSON-LD view + 2 `cut_meta` tests (1011). D53. |
| 26 review | Claude ran the 1011 tests on PostgreSQL 16 (ruff, format, mypy and makemigrations clean) and made 21 deliberate breakages: 14 caught. Not caught: exact vs prefix vs weak score order (the order could match by accident, because an exact name always sorts before a longer name that starts with it), the cap on a candidate limit above 200, the hidden `q` input in the filter form. Three more survived because another rule covers them (name_hi and search_keywords `icontains`, brand name in the full-text vector); the Devanagari test therefore does not prove which rule matched. |
| 26b | Test follow-up: score assertions for the ranking bands, a prefix-before-full-text test with an adversarial name order, the limit cap, the hidden `q` input; no product code changed (1014). |
| 27a | `/search/suggest/` (`search_suggest`, `SUGGESTION_CANDIDATES = 50`, `SUGGESTION_LIMIT = 6`): an HTML fragment of at most 6 cards from the same backend and visibility rule as `/search/`, empty under 2 characters without touching the backend or the tables, `noindex` and `no-store`; new template `_search_suggestions.html`; 17 tests (1031). |
| 27b | Header search-as-you-type: htmx request with a 250 ms debounce and `hx-sync`, Alpine open/close and ArrowUp/ArrowDown focus, the `x-cloak` style; 8 tests. D54 and docs. |
| 28a | Search fix: the three trigram rules run only when the longest query word has at least 5 characters (`TRIGRAM_MIN_WORD_LENGTH`, `allows_typo_matching`), so "tea" no longer returns oils or Test Atta; 8 catalog + 2 storefront tests (1049). D53 and D54 amended. |
| 28b | Phase 2 wrap-up docs: STATUS, open questions, README, `docs/python_recap.md`; D53 and D54 amendments. |

## Remaining Phase 2 slices

14. (done) ProductImage and media storage.
15. (done) Inventory models: `InventoryMovement` ledger, `Supplier`, loose-by-weight and low-stock fields (D42).
16. (done) Inventory services and integrity check (D43).
17. (done) AuditLog in core (D44): stock is not audited (the ledger covers it); variant edits in the admin are.
18. (done) Inventory admin (D45): movement history, suppliers, Stock screen with receive, adjust, count and damage pages, low-stock filter and Expiring lots screen.
19. (done) CSV export (D46): service plus an action on the variant list and on the product list.
20. (done) CSV import with a validation report (dry run, then commit) (D47).
21. (done) Bulk price update admin action (must write audit rows: `snapshot_variant_from_db` before, `record_variant_change` after; queryset `update()` skips any automatic hook, D44).
22. (done) Seed command (about 40 real rows plus demo rows loaded through the importer; idempotent).
23. (done) Storefront listing, category and brand pages.
24. (done) Filters (HTMX) (D51).
25. (done) Product detail page (variants, savings, FSSAI info, JSON-LD) (D52).
26. (done) Search service (PostgreSQL full-text and trigram behind an interface) (D53).
27. (done) Search-as-you-type UI (D54).
28. (done) Phase 2 wrap-up (docs, ADRs, README, smoke test, Python recap).

StockReservation stays in Phase 3.

## Decided in Slice 15 (kept for reference)

All four were decided and recorded as ADR D42 in `docs/decisions.md`, together with the loose-by-weight rule. The list below is the original questions.

1. Current stock: derive it as the sum of movements (what `docs/architecture.md` says) instead of a cached StockLevel table. Add a cache later only if it is slow.
2. Batch and expiry: optional batch number and expiry date on received stock only; no first-expiry-first-out logic (the brief asks for expiry and batch tracking).
3. Supplier: a minimal optional record (name, phone, notes) on purchases.
4. Low-stock threshold: an optional number per variant; no alert when blank.

## Known gaps

- `Invoice` and `Address` models are in the brief but not yet in the data model of `docs/architecture.md`; design them before Phase 3 (addresses) and Phase 5 (invoices).
- Rounding rules and HSN format are defaults awaiting the CA (see `docs/open_questions.md`); they must be confirmed before invoices are issued (Phase 5).
- Photo files are not deleted from disk when an image row or product is deleted; add a clean-up job before production (see `docs/open_questions.md`).
- Only original photos, 400 px WebP card thumbnails and 900 px WebP detail images are stored; the backfill command must be run once after deploying and photo files are still not deleted from disk.
- `Category.clean()` blocks parent cycles only in forms and the admin; a direct `.save()` or `update()` can still create one. The storefront helpers are cycle-safe (D50).
- Brand options have no counts and do not narrow with the other filters, so a customer can pick a combination with no results; the diet filter offers Vegetarian only; the filter form puts empty fields (`min_price=&max_price=`) into the address; the htmx behaviour (swap, push-url, Back button) has no browser test and was checked by hand. `CategoryTree` reads the whole category table once per request, which is fine for hundreds of categories.
- Search (D53): there is no GIN index on the search fields yet (the catalogue is small; add one when it grows); `/search/` has no rate limit; there is no "did you mean" suggestion when a typo finds nothing; search words are not logged, so a missing product is invisible to the owner; the brand filter lists every brand with visible products instead of narrowing to the results; relevance ranking is judged only on the tiny seed catalogue. A query whose longest word is under 5 characters skips the trigram rules (D53, Slice 28), so a short typo is not tolerated.
- Search-as-you-type (D54): `/search/suggest/` has no rate limit; the dropdown has no browser test (the htmx swap, the focus movement and the Back button were checked by hand); matched letters are not highlighted; an IME composition fires one request per input event, debounced only by the 250 ms delay.
- The ledger is append-only in Django only (`save`, `delete` and queryset methods); raw SQL can still change rows. A Postgres trigger or a restricted database role is a production hardening step (see `docs/open_questions.md`).
- Loose-by-weight quantities exist in the ledger and on variants; the cart, orders and reservations (Phase 3) must use decimal quantities too.
- Operations that touch several rows (the Slice 20 import) lock products first, then variants, each in ascending `pk` order (D43, D47). Slice 21 must keep that order; there is no shared helper for it yet.
- `receive_stock` checks the supplier's `is_active` on the object passed in and does not lock the supplier, so a supplier archived at the same moment could be used once more.
- `check_inventory_integrity` is run by hand; nothing schedules it (cron or a job) yet.
- The variant audit covers edits of existing variants on the two admin pages, and variants the CSV import creates or changes (Slice 20). Creating or deleting a variant on the admin pages, other models (product, brand, category), and changes made in the shell or with queryset `update()` write no audit row. Slice 21 must call the helpers in `apps/catalog/audit.py` (D44).
- The CSV export contains `cost_price` and is open to anyone with `catalog.view_productvariant`; narrow it when the Phase 5 roles exist. Exports are not logged (D46).
- The CSV import reads the export format back exactly (a round-trip test proves it): files are opened as UTF-8 with an optional BOM, one leading `'` is removed from a text cell when the next character is `=`, `+`, `-`, `@`, tab or carriage return, and barcodes (digits) and HSN codes (4 to 8 digits) are validated, because Excel turns `8901234567890` into `8.9E+12` and `0902` into `902` when a CSV is opened by double-click (D46, D47).
- `cost_price` is not shown in the product-page variant inline, so it is audited only when edited on the variant's own admin page.
- Adjust can take stock below zero (the service allows it and logs a warning, D43); Damage cannot. The Count page can set stock to any number from zero up.
- The Expiring lots screen cannot say how much of each lot is left, only the variant's total (D42, D45). Real batch-level stock would need first-expiry-first-out accounting.
- Stock write permissions are the plain Django ones (`inventory.add_inventorymovement` plus the Stock view permission) until the Phase 5 staff roles exist (D45).
- Nothing alerts anyone about low stock or near expiry; the screens must be opened (D45, notifications come with Phase 6).
- `AuditLog` is append-only in Django only; raw SQL can still change rows (same production hardening step as the ledger, D42 and D44). Retention of audit rows is an open question.
- The detail page has no zoom or swap-the-main-photo gallery (the small photos are links to the thumbnail); there is no "Add to cart" until Phase 3; related products are the first four of the same category by name, not a relevance ranking.
- `backfill_image_sizes` must be run once after deploying Slice 25 to create `medium` files for photos that already exist.

## How we work

- Slice 18 changed the routine: 18b to 18d were prompts run in Freebuff; when Freebuff stalled in a loop on 18e, Claude wrote 18e to 18i directly and tested them in a sandbox with PostgreSQL (ruff, format, mypy, the whole pytest suite, `makemigrations --check`). Each slice was delivered as a patch: copy it into the repo folder, run `git apply --check <file>`, then `git apply <file>`, delete the patch file, run the gate yourself, and commit. The sandbox could not run the pre-commit hooks (no network), so your own `pre-commit run --all-files` is the real check. The rules below still apply to any agent run.
- The agent (OpenCode Desktop; model DeepSeek V4.1 Flash, free, high effort, since Slice 25e; Beam Free before) never touches `.env`, never commits or pushes, and never runs `git reset` or `git checkout`. It runs only the four checks in `docs/AGENT_BRIEF.md` (ruff, ruff format --check, mypy, pytest), once, at the end, plus any command the prompt names (for example one `makemigrations`). The rules every agent reads first are in `AGENTS.md` in the project root. OpenCode models have no memory of the project, so every prompt is self-contained and names the files to read.
- Each prompt states a goal, the fixed decisions, the files to read first, the exact names of the tests to write (with a minimum count), the allowed commands and the rules. Existing files change by small exact-match edits (each OLD text must appear exactly once, or nothing changes); new files may be written in full. Size: since Slice 24c the owner prefers bigger prompts, ideally one per slice, in which the agent designs and writes the code from a goal, fixed decisions and exact test names (Claude no longer pastes finished code into prompts). To keep a big prompt from stopping early: start it with a PLAN ONLY line, number the work items, and ask for a final report with one line per item saying done or "not written". Big prompts start with a PLAN ONLY line (the desktop app has no plan-mode key): the agent replies with a plan, the owner reads it, then says "go ahead".
- Every prompt starts with the slice number and letter (for example "Slice 24b"), tells the agent to read `AGENTS.md`, `docs/AGENT_BRIEF.md` and `docs/STATUS.md` first, covers one part of a slice, and ends with "Show the diff and STOP". At the start of each slice Claude asks the due questions first, then gives the prompts. After each run the owner compares the test count with what the prompt promised.
- What works with the agent (learned in Slices 24 and 25): one prompt that mixes code, a migration, many tests and docs made it stop early and report the rest as "not written (time)", or report "done" for edits that never reached the disk. Prompts that give a goal, fixed decisions, exact test names and a numbered list of work items work. Split the tests by whole files and ask for a single-file pytest run after each file; for docs, ask the agent to read the file again to confirm and to report one line per item. When a run stops early, the owner commits the green part and the next prompt names only the missing items. Claude never trusts a report: the owner checks `git status` and the test count, and Claude re-runs the suite twice and the deliberate breakages after every push.
- After every prompt the owner runs this block from the repo folder: `uv run ruff format .`, `uv run ruff check .`, `uv run mypy apps`, `docker compose exec web python -m pytest -q`. For a slice that changes models, also `docker compose exec web python manage.py check` and `docker compose exec web python manage.py makemigrations --check --dry-run`. The Docker pytest run is the official one; `uv run pytest` on Windows uses whatever database `.env` points at and can pass or fail for reasons unrelated to the code.
- Roles: the OpenCode agent does the context-heavy coding. Claude designs, writes the prompts, builds data files and doc fixes that must be exact, and reviews every pushed slice by cloning the public repo (github.com/mohammedsafdari/projectbadshahstores); files do not need to be pasted. Work happens on `main` only. After an agent run the owner runs the Docker pytest, `git status`, `git add` with named folders, `git status` again (no `.env`, `opencode.json`, `.cloud-attachments` or `media`), commits with the slice id, pushes, and sends Claude the pytest summary line and the agent's final message.
- Claude's review of a pushed slice: pull the repo, run the full suite against PostgreSQL in the sandbox (install PostgreSQL 16 with contrib from the Ubuntu archive (`apt-get install postgresql postgresql-contrib`), run it on a socket folder with `initdb` and `pg_ctl`, and set `PGHOST` to that folder and `DATABASE_URL=postgres://postgres@/postgres?host=<folder>`), run ruff and mypy, then break each guarded line on purpose (locks, `updated_at`, fingerprint check, audit call, rounding) and confirm a test fails. Slice 20 and Slice 21 passed this check (719 and 754 tests).
- The agent edits settings or `pyproject.toml` only if the prompt names them. After any `pyproject.toml` edit, run the TOML check.
- Check the real `git diff` and `--stat`, not the agent's pasted diff. Commit in small steps after `git status` and `git diff --stat`.
- Every commit message starts with the slice id, for example `Slice 14a: Add ProductImage model`.
- Windows 11, Command Prompt. Python and Django idioms are explained as we go, because the owner is new to Python.
- Business questions nobody can answer yet go into `docs/open_questions.md` with the default in use and an "ask again before <slice or phase>" point; work continues unless the answer cannot be changed later. At the start of every slice, Claude lists the open questions that have become due and asks them before any code is written.

Everyday commands (they run inside the container):

- `docker compose exec web python manage.py check`
- `docker compose exec web python manage.py makemigrations <app> --name <short_name>`
- `docker compose exec web python manage.py migrate`
- `docker compose exec web python -m pytest -q`
- `pre-commit run --all-files` (run all hooks once before the first commit attempt; it may reformat files, then run `git add -u`)

## Lessons

- Agent reports can be wrong: invented text, inconsistent line numbers, even checking the wrong file name. Verify key facts with `findstr`, `type`, `git ls-files` and `git diff`.
- The questions file is `docs/open_questions.md` (underscore).
- Django's Unicode slug check rejects Devanagari vowel signs (D37).
- Rewriting a long file in one prompt corrupted it once; use exact-match edits.
- On this Windows PC `git push` needed `git config --global http.sslBackend schannel`.
- Pre-commit may reformat files; run `git add -u` and commit again.
- The agent sometimes reports edits that never reached disk. Check `git status` and `git diff` before anything else.
- Django string model references are `"app_label.ModelName"`. Validators and `upload_to` take the function itself, not a quoted dotted path.
- After adding a dependency: refresh the lock file (`docker compose run --rm --no-deps web uv lock`), rebuild, then recreate containers with `--renew-anon-volumes`, otherwise the old `/opt/venv` volume hides the new package.
- Uploaded media lives in the `/app/media` Docker volume, not in the project's `media\` folder, and `--renew-anon-volumes` deletes it (dev data only). The Dockerfile creates `/app/media` and `/app/staticfiles` owned by the `app` user; without that, uploads failed with PermissionError.
- Verify docs work as well: ADRs D37 to D40 were once listed here without being written to `docs/decisions.md`.
- Before committing code, run the whole gate once: pytest, `makemigrations --check --dry-run`, `pre-commit run --all-files`; then `git add -u` and `git add apps/<app>`, and check that new migration files appear in `git status`.
- A change that alters how an existing model behaves (a `save` override, a constraint, a manager) can break old tests. Name them in the prompt and let the agent fix them.
- Agent edits can merge or mangle neighbouring lines, even in docs (two bullets of `AGENT_BRIEF.md` were once joined). Review the real diff of every commit, including docs.
- A race test must be able to fail: remove `select_for_update()` (keep `.objects`; a typo that crashes the code proves nothing), run the test a few times, expect assertion errors, then `git restore` the file.
- Factories can hide rows: `InventoryMovementFactory` also creates a +10 purchase for its variant. In ledger tests build the variant with `ProductVariantFactory` and add movements by hand.
- Two test functions with the same name silently replace each other (ruff F811). After the agent writes tests, list them with `findstr /n "def test_"`.
- With checks allowed, the agent once wrote files nobody asked for (a note and an empty report file) and printed a wrong path in its report. Run `git status` for untracked files and say "create exactly these files and no others".
- Exact-match edits at the end of a file or inside a long line failed silently or split lines. New files with verbatim content (`integrity.py`, the concurrency tests) worked.
- mypy does not know reverse accessors such as `variant.movements`; query from the model that owns the foreign key (`InventoryMovement.objects.filter(variant_id=...)`).
- Pasting text with a stray `>` into Command Prompt creates junk files; check `git status` before every commit.
- When the agent loops on a rejected edit, stop it, throw its files away (`git restore`, delete the untracked files) and write the change another way. Its half-applied files (duplicated lines, an IndentationError, two tests with one name) were worse than none (17e).
- "Change nothing else" was ignored once (unrelated inventory tests rewritten) and an unrequested `has_view_permission` returning True let every staff user read the audit log; only a test that a staff user without permissions gets 403 caught it. Name every permission method in the prompt and review test-file diffs too.
- Run the whole suite yourself before committing; an agent report with a failing test inside it is not a pass (17c).
- Django's admin escapes HTML, so `->` appears as `-&gt;` in page text; assert on the escaped form.
- In an admin `save_model` or `save_formset` the form has already put the new values on the object; read the old values from the database before saving.
- django-stubs generics such as `BaseFormSet[Any]` fail at run time unless the module starts with `from __future__ import annotations`.
- A proxy model still needs a migration (`makemigrations` writes it; it has no tables). In 18d the migration was missing from the first push even though checks passed on the owner's PC: always look for the new file in `git status` before committing and confirm with `git log origin/main`.
- A running server does not notice a brand-new `admin.py`; run `docker compose restart web` after adding admin files.
- `git push` failing with "Could not resolve host" is a network or DNS problem on the PC, not a git problem; the commit is safe locally. Fix the connection, push again, and check `git log origin/main --oneline -3`.
- Proxy models get their own permission codenames (`view_stockitem`, `view_expiringlot`); tests grant them by codename. Custom admin pages must be listed before Django's own `<id>/` URL in `get_urls`, wrapped in `admin_site.admin_view`, and must check permissions themselves.
- An annotation added in `get_queryset` is unknown to mypy; use `cast(Any, obj).on_hand` (ruff B009 forbids `getattr` with a constant name). Always test that the annotated number equals the service (`get_on_hand`) and that the query count does not grow with the number of rows.
- A custom admin action that the user may not run does not return an error code: Django shows the list page again (status 200, HTML), so test for "no CSV came back". The admin also adds its own `Cache-Control` words, so assert `"no-store" in header`, not equality (19a).
- Test helpers that create a user must not reuse the same phone number inside one test (unique constraint), and a purchase of quantity zero violates a ledger constraint; both made 18e's first test run fail.
- `save(update_fields=[...])` writes only the named columns, so `updated_at` must be listed too; without it the row changes while its timestamp does not, and the import fingerprint would not notice (20d).
- Catch `ValidationError` and `IntegrityError` OUTSIDE the `transaction.atomic()` block: caught inside, the transaction is already marked broken and every later query fails. The `try` must wrap the `with`, not sit inside it (20d).
- Lock in a fixed order: products first, then variants, each with `order_by("pk")`, so two imports of overlapping files queue instead of deadlocking each other (D43, D47).
- A test must fail when the line it guards is removed. Slice 20 had no test for `updated_at` in `save(update_fields=...)`; found by deleting the line and running the suite (20g-1).
- Check results come from the Docker pytest run, not from `uv run pytest` on the PC (see "How we work"). I once gave `uv run pytest` in a command block; the project rule is Docker for pytest and `uv run` for ruff and mypy.
- A sample sales invoice from the billing software (28/10/2026) showed: loose items billed per kg (Gur, Makhana), GST-inclusive line amounts at 0%, 5% and 18%, no item codes, barcodes, HSN or categories, free-text pack sizes in item names, and the words "Credit Tax Invoice". It cannot decide the SKU scheme or product grouping; an item master export can.
- A slice is done when the test count rose by the number of tests promised, not when the suite is green. Slice 23's first run left 767 tests before and after.
- A prompt must list the exact test names and a minimum count per file. "Write tests for X" produced 4 view tests instead of about 15 (Slice 23).
- Agents can destroy documents: the D50 edit replaced two paragraphs of D48 and put D50 before D49. In `git diff docs/` every `-` line must be intentional; new ADRs go at the end of the file.
- Weak test patterns to reject: asserting only `is not None`, `a or b` assertions that are always true, an early `return` inside a test, `html.index("A") < html.index("B")` on a whole page, and invalid image bytes that make the code under test skip silently (Slice 23).
- Only `apps/catalog/tests` gets the temporary `MEDIA_ROOT`; tests elsewhere that upload files need their own fixture or they write into the real `media/` folder.
- Sort and paginate in the database. The first Slice 23 listing loaded every product into Python and sorted there.
- A free-model agent can stop early and still end with STOP. Read its report for "not written" and compare it with the prompt's list.
- Review's deliberate breakages found weak tests twice in Slice 24: data that makes a scope filter irrelevant (the same brand in both categories), an order that matches by accident (the reverse order was also correct; use three items whose id order and plain sort are both wrong), and an assertion that only works on page 1 (check the total on page 2 as well).
- A checkbox state test must look at that one `<input>` tag, not at 'checked' anywhere in the form.
- htmx details that tests cannot see (swap, history, Back button) are checked by hand in the browser before a slice is closed.
- A test that loops over a list must first assert the list is not empty (the absolute-URL test passed on an empty list).
- Read the rendered text of a new template, not only its structure (the savings line printed the price).
- A report that says "done" for docs is checked with `git status` and `git diff docs/`.
- Never assert that a short string such as "P2" is in or not in the whole page: the page holds random text (the CSRF token) that can contain it. Compare the list of product names read from the `<h2>` headings.
- After a slice with a migration, run `docker compose exec web python manage.py migrate` in the dev database. pytest builds its own database, so it passes while the site shows "column does not exist".
- Commit with named files (`git add <paths>`), not `git add -A`, and not while an agent may still be running.
- A test of a view that hands a helper to a service (`request.build_absolute_uri`) must go through the view; calling the service with its own helper leaves the view untested.
- Split search words on whitespace, never with a `\w` regex: Python's `\w` does not match Devanagari vowel signs such as the one in "आटा", so the query words would be broken (D53).
- Ranking tests must compare scores, not only the order of names: a name order that is also alphabetical passes by accident (Slice 26). Choose test data whose alphabetical order is the OPPOSITE of the expected rank.
- Claude's review sandbox needs a real PostgreSQL 16 with the contrib package (the pgserver pip package has no pg_trgm, so every database test errors); install it from the Ubuntu archive.
- Trigram similarity is unreliable on words under 5 characters: a 3-letter word has only four trigrams and two of them are the padded start, so "tea" scored 0.5 against "tel" and "test". Gate typo matching by word length (D53, Slice 28).

## Slice closeout checklist

1. Tests pass (`docker compose exec web python -m pytest -q`), `manage.py check` is clean and `makemigrations --check --dry-run` says no changes. The test count rose by at least the number of tests the slice promised, and `git diff docs/` shows no deleted line you did not intend.
2. Commit with the slice id, push, and confirm CI is green.
3. Update this file: last finished slice, what is built, the slice log, remaining slices, known gaps.
4. Add an ADR to `docs/decisions.md` for every new decision (next free number), noting where it narrows the brief.
5. Update `docs/architecture.md` only if the target design changed.
6. Update `docs/open_questions.md`: new doubts (each with an "ask again before" point), and answers received.
7. Update `README.md` only if setup or commands changed.
8. Commit the docs as `Slice <n>: Update docs`.
9. Start a new chat: attach `PROJECT_INSTRUCTIONS.md` and `docs/STATUS.md`, add other docs only when the next slice touches them, and say "Continue at Slice <next>".
