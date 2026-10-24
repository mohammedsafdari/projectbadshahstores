# Badshah Store — Decisions (ADRs)

Short records of why each key decision was made. Where a decision overrides PROJECT_INSTRUCTIONS.md, that is noted. This file records reasons; `docs/architecture.md` is the source of truth for the current design.

## D1. Modular monolith, one Django deployable app

- **Decision:** Single Django project with apps under `apps/`; no microservices in MVP.
- **Why:** One delivery origin, one store, simpler ops and deployments. Integrations remain behind interfaces so a later FastAPI service or shipping provider can be swapped without touching core logic.

## D2. Business logic in `services.py`, thin views

- **Decision:** Put core logic (cart, checkout, stock, orders, payments, coupons) in per-app `services.py`; keep views as organizers of requests/responses.
- **Why:** Makes logic testable without the HTTP layer, keeps reusable by a future API, and localises the risky money/stock paths for review.

## D3. Money is Decimal INR, never float

- **Decision:** All monetary fields are `DecimalField`; INR with 2 decimals; no float in pricing, totals, taxes, or gateway amounts except at the gateway boundary with exact conversion and errors on fractional paise.
- **Why:** Avoids classic floating-point money bugs; GST-inclusive display and invoice math must be exact.

## D4. Timezone and locale are Django settings

- **Decision:** `TIME_ZONE = Asia/Kolkata`, `USE_TZ = True`, `LANGUAGE_CODE = en-IN` (with i18n scaffolding), and Indian digit-grouping formatting in templates/utils. These are not stored in `ShopSettings`.
- **Why:** Shops operate on IST; mixing store-level timezone into business data causes subtle bugs. Locale formatting is a presentation concern.

## D5. Inventory as an immutable ledger

- **Decision:** `InventoryMovement` is the source of truth for stock; `on_hand` is derived from movements; stock reservation separate from on-hand.
- **Why:** Allows full audit trail (receive, sale, return, damage, adjustment), reconciliation against physical counts, and correct async reservation expiry without corrupting stock.

## D6. Reservation model split by kind and expiry

- **Decision:** `StockReservation` with `ACTIVE/CONSUMED/RELEASED` and kinds `CHECKOUT_PENDING_PAYMENT` and `HELD_FOR_FULFILMENT`. Expiry (15 min, configurable) applies only to unpaid online checkouts; COD/paid orders hold until packing/cancellation/delivery.
- **Why:** Prevents holding stock for abandoned carts forever while keeping COD/paid orders stable until fulfilment. Separates "customer may still abandon" from "we are fulfilling".

## D7. Sale movement written once, at fulfilment, with reservation consumption

- **Decision:** Only one service function writes `SALE` movements, in the same transaction that consumes the matching reservation at packing.
- **Why:** Eliminates double-deduction between reservation and sale; centralises the one place stock leaves inventory for sales.

## D8. No negative stock from sales; explicit adjustment only

- **Decision:** Sales check availability and never create negative `on_hand`; only an explicit adjustment with reason may create negative `on_hand`, logged with a warning.
- **Why:** Negative stock from races or bugs is dangerous for an inventory-ledger model; make it an explicit, auditable exception, not a side effect.

## D9. Payment captured after reservation expiry

- **Decision:** If payment is captured after the reservation expired, try to re-reserve; if stock is gone, auto-refund and notify the customer.
- **Why:** Razorpay async/payment-completion timing can exceed checkout reservation; we must not silently sell unavailable stock or leave the order in an inconsistent state.

## D10. Order state machine centralized

- **Decision:** Explicit statuses with a single enforced transition path; `OrderStatusLog` for every transition with timestamp and actor. COD skips payment states.
- **Why:** Many people touch orders in the admin; a single source of truth for allowed transitions prevents illegal states and makes audit/reversal reasoning clear.

## D11. Substitutions preserve the original line

- **Decision:** Keep the original line with status `SUBSTITUTED` and add a new line linked by `replaces`; recompute price/GST at substitute's current selling price; customer picks `substitute / refund / call me` at checkout.
- **Why:** Keeps an accurate record of what the customer ordered vs what was fulfilled, and what price/GST actually applied; supports refunds and disputes.

## D12. Order finalization at packing

- **Decision:** At end of packing, finalise: cancel unfulfilled quantities, release reservations, make refund/adjustment as needed.
- **Why:** Prevents dangling reservations and unfulfilled lines from lingering; makes the stock and order state consistent when a packer finishes.

## D13. One order = one invoice

- **Decision:** Each order has exactly one invoice generated at packing/dispatch from fulfilled quantities.
- **Why:** GST invoicing for a provision shop should map cleanly to one sale per customer order; avoids splitting tax invoices in confusing ways.

## D14. Numbering: locked counter, financial year label, gapless sequential

- **Decision:** `NumberSeriesCounter` keyed by `(prefix, financial_year)`, incremented under `select_for_update` in a locked transaction; no reset job. Financial-year label from April start. Order numbers `BS/26-27/000001`; invoice numbers `INV/26-27/000001`, max 16 chars (GST rule, to be confirmed by CA), sequential and gapless.
- **Why:** Locking prevents duplicate numbers under concurrent order creation; financial-year key matches Indian accounting practice; gapless sequential invoices are expected for GST records. CA to confirm the 16-char rule and exact prefix style.

## D15. Webhook idempotency via unique event_id

- **Decision:** Verify Razorpay signature first, then insert `WebhookEvent` (unique `event_id`) in one transaction; on conflict return 200; process and mark processed. Out-of-order events handled only through allowed transitions.
- **Why:** Duplicate webhooks must never double-charge, double-ship, or double-decrement stock; idempotent insert is the gate. Webhook is the source of truth, not the browser redirect.

## D16. Coupon concurrency safety

- **Decision:** Unique constraint on `(coupon, order)` plus `select_for_update` on the coupon row while checking limits and redeeming.
- **Why:** Prevents overselling a coupon's usage cap under concurrent checkouts.

## D17. OTP stored as HMAC, not plaintext

- **Decision:** Store an HMAC over phone + purpose + code using a server secret; constant-time comparison; short expiry; limited verify attempts; per-phone rate limits.
- **Why:** Reduces blast radius if DB is compromised; still allows verification. Note: this protects stored representation, but OTP delivery itself remains a provider/transport concern. Never log OTP codes or message bodies, including in `NotificationLog`.

## D18. Authorization on every object access

- **Decision:** Customer can only see their own orders/addresses; staff access governed by role/permissions; no client-supplied IDs assumed owned.
- **Why:** Basic multi-tenant correctness for a customer-facing app.

## D19. Delivery fee and free-delivery threshold live on DeliveryZone

- **Decision:** `DeliveryZone` carries the fee and free-delivery threshold. `PinCodeZone` only links pin codes to a zone.
- **Why:** Different pin codes can have different economics; keeping fee rules on the zone avoids one global fee that doesn't match reality, and `PinCodeZone` stays a simple membership link.

## D20. Slot capacity computed live under lock

- **Decision:** Capacity computed on the fly inside the order-placement transaction with a lock; no cached `orders_count`.
- **Why:** Cached counts drift under concurrency; computing live with a lock keeps slot limits correct.

## D21. COD cap applied at checkout total, not re-enforced after substitutions

- **Decision:** COD capped at ₹2,000 per order, checked on the order total at checkout only.
- **Why:** Matches the business rule as stated; substitutions recompute the total but the cap decision is made at checkout. This is a deliberate scope choice; review if policy changes.

## D22. Reservation transition on payment capture or COD placement

- **Decision:** When an online payment is captured, or a COD order is placed, the reservation changes from `CHECKOUT_PENDING_PAYMENT` to `HELD_FOR_FULFILMENT` inside the same transaction as the order status change.
- **Why:** Keeps the reservation state and order state in lockstep; the checkout reservation is short-lived and may expire, while the fulfilment reservation is held until packing/cancellation/delivery. Doing the transition in the same transaction avoids a split-state window.

## D23. Quantities are whole-number integers for now

- **Decision:** Order/cart quantities are integers. No loose or weight-based items yet; revisit if you sell items by weight.
- **Why:** Keeps the MVP data model and stock math simple and correct. Weight-based or loose quantities would need a different unit/pricing model.
- **Update:** Narrowed by D42. Packet variants stay whole-number; variants sold loose by weight use decimal quantities.

## D24. No guest checkout; OTP login required at checkout

- **Decision:** The MVP has no guest checkout. OTP login is required before an order can be placed.
- **Why:** Matches the locked scope; simplifies authorization, order ownership, notifications, and stock reservation ownership.

## D25. Provider defaults

- **Decision:** Razorpay in test mode only until told otherwise. OTP/SMS/WhatsApp and email use console backends in development; real MSG91 and Brevo later, with MSG91 DLT registration first.
- **Why:** Lets development proceed without live credentials or regulated registrations; keeps the integration points testable with a console backend.

## D26. Search starts with PostgreSQL full-text + trigram

- **Decision:** Begin with PostgreSQL full-text and trigram similarity behind an interface; add semantic/embedding search later.
- **Why:** Adequate for typos and Hinglish spellings at MVP scale, no separate search infrastructure yet.

## D27. Media: S3-compatible in prod, local in dev

- **Decision:** S3-compatible object storage in production, local storage in development, with image resizing.
- **Why:** Keeps dev simple; prod gets durable, CDN-friendly storage.

## D28. Tailwind via pinned CDN in Phase 1; build step before launch

- **Decision:** Phase 1 loads Tailwind from a pinned CDN; HTMX and Alpine.js vendored into `static/`. Note: a Tailwind build step is required before launch.
- **Why:** Fastest path to a styled, mobile-first UI in Phase 1 without a JS toolchain yet. CDN is a placeholder for a proper build, recorded as a known debt.

## D29. Deferred items (not MVP)

- **Decision:** PWA, full Hindi i18n content, pan-India shipping, subscriptions, loyalty, and the FastAPI AI service are deferred. i18n scaffolding starts now; everything else is not built.
- **Why:** MVP scope is hyperlocal ordering + admin operations; defer the rest to avoid over-building.

## D30. Tests run against PostgreSQL 16

- **Decision:** Tests and CI target PostgreSQL 16 (Compose locally, service container in CI) because locking and constraint tests need it.
- **Why:** SQLite cannot exercise `select_for_update` and several constraint/idempotency behaviors we rely on.

## D31. Custom user model with phone as USERNAME_FIELD, set before first migration

- **Decision:** `AUTH_USER_MODEL` set to the accounts custom user before the first migration; phone normalised to E.164; empty email stored as NULL; conditional unique constraints among active users; role separate from `is_staff`; customers cannot reach admin through role.
- **Why:** Phone OTP auth requires phone as the identity field; early custom user avoids painful migrations later. Role/eligibility separation keeps customer accounts out of the admin surface by design.

## D32. Staff 2FA before launch (recommended)

- **Decision:** Recommended, not built in Phase 1: add 2FA for staff/admin accounts before launch.
- **Why:** Admin access controls stock, orders, refunds, and payments; stronger staff authentication reduces risk before go-live. Deferred to keep Phase 1 scoped, but flagged here so it is not forgotten.

## D33. Ruff rule exceptions

- **Decision:** Ruff is configured to ignore a small number of rules where the warning conflicts with Django conventions, settings layout, or intentional test data.
- **Why:** We want ruff/mypy coverage without forcing fake annotations or changes that fight Django's normal idioms. The exceptions are narrow and documented so they do not quietly widen.

### Global ignores

- `RUF012` is ignored globally.
- **Reason:** It flags mutable class attributes without `ClassVar`. That is normal Django usage for things like `Meta`, admin options, and migration class attributes, and annotating each one with `ClassVar` adds noise without real safety.

### Per-file ignores

- `S105` and `S106` are ignored in test code only (`tests/**/*.py` and `**/tests/*.py`).
- **Reason:** Test code intentionally contains hard-coded passwords, tokens, and fake secret-like strings. Treating those as real secret detections would produce constant noise without security value.
- `F405` is ignored in settings modules only (`badshahstore/settings/*.py`).
- **Reason:** Settings modules use `from .base import *` by design, so names are brought in through the star import rather than being referenced as attributed imports.

### Targeted suppression

- `DJ001` is suppressed with `# noqa: DJ001` on `User.email` only.
- **Reason:** The field is `models.EmailField("email address", null=True, blank=True)` with no `unique=True` and no `UniqueConstraint` that includes `email` in `User.Meta`. Email is optional because login uses phone (`USERNAME_FIELD = "phone"`), not email.

## D34. Pre-commit runs ruff and mypy through `uv run`

- **Decision:** `.pre-commit-config.yaml` uses `repo: local` hooks with `entry: uv run ruff check ...`, `uv run ruff format ...`, and `uv run mypy .` rather than installing duplicates of those tools into the pre-commit environment.
- **Why:** This keeps pre-commit using the exact versions and dependencies locked in `uv.lock`, so local hook runs match the versions used by developers and CI. It also avoids maintaining a second pinned copy of ruff/mypy inside `.pre-commit-config.yaml`.

## D35. Pre-commit passes `--force-exclude` to ruff

- **Decision:** The ruff pre-commit hooks use `ruff check --fix --force-exclude` and `ruff format --force-exclude`.
- **Why:** Pre-commit typically passes changed filenames explicitly, which can override ruff's normal exclude handling. `--force-exclude` makes ruff respect the excludes in `pyproject.toml` even when filenames are supplied directly, so generated code matching `apps/*/migrations` stays excluded in both pre-commit and direct `uv run` runs.

## D36. Proxy HTTPS trust is controlled only by SECURE_PROXY_SSL_HEADER

- **Decision:** `SECURE_PROXY_SSL_HEADER` is the only setting that makes Django trust a proxy's HTTPS header, and the settings read it from the environment only when it is explicitly set. The `USE_X_FORWARDED_PROTO` variable was removed from `badshahstore/settings/base.py`, `badshahstore/settings/prod.py` and `.env.example`. `USE_X_FORWARDED_HOST` is unchanged.
- **Why:** `USE_X_FORWARDED_PROTO` is not a Django setting, so it had no effect, but its name suggested protection that did not exist. A setting that looks like a security control and does nothing gives false assurance. `SECURE_PROXY_SSL_HEADER` should be set only behind a reverse proxy that terminates HTTPS and overwrites that header, because otherwise a client could fake it.

## D37. Django's Unicode slug check rejects Devanagari vowel signs

- **Decision:** Hindi text is not used in slugs. Hindi names go in the name fields (`name`, `name_hi`). Slugs are romanised ASCII (see D38). The rule is explained in a code comment in `apps/catalog/models.py` and checked by `test_slug_rejects_devanagari` in `apps/catalog/tests/test_models.py`.
- **Why:** Brand and Category slugs were first created with `SlugField(allow_unicode=True)` (migration `0001_initial`) so that Hindi could be used. Django's Unicode slug check accepts only letters, digits, hyphens and underscores, and Devanagari vowel signs (combining marks, such as the final "ा" in "आटा") do not count as letters, so ordinary Hindi words failed validation. Loosening the validation would have meant writing our own slug rules, so slugs were switched to ASCII instead.

## D38. Slugs are plain ASCII only; Hindi goes in name fields

- **Decision:** Brand, Category and Product slugs use Django's standard `SlugField`, which allows only ASCII letters, digits, hyphens and underscores. Migration `0002_slug_ascii_only` changed Brand and Category slugs from `allow_unicode=True` to this default. Hindi text goes in the name fields (`name`, `name_hi`). The test `test_slug_rejects_devanagari` in `apps/catalog/tests/test_models.py` enforces the rule.
- **Why:** This follows from D37: Django's Unicode slug check rejects Devanagari vowel signs, so Hindi slugs would fail validation for ordinary Hindi words. Romanised slugs (for example `chana-dal`) also stay readable and safe in URLs on every device. The admin fills the slug from the English name automatically.

## D39. Database check constraints on ProductVariant

- **Decision:** The catalog stores money and tax invariants in the database with `CheckConstraint`s on `ProductVariant`, not only in Django model validators. The current constraints are `catalog_variant_mrp_positive` (MRP > 0), `catalog_variant_selling_price_positive` (selling_price > 0), `catalog_variant_selling_price_lte_mrp` (selling_price <= MRP), `catalog_variant_cost_price_non_negative` (cost_price is null or >= 0), and `catalog_variant_gst_rate_0_to_100` (GST rate between 0 and 100 inclusive). These exist in both the model and the migration that created the variant table.
- **Why:** Some rules are cheap and dangerous if they can be bypassed, so they belong at the database level. The existing code shows that model validators already help at the form/validation layer (`hsn_code` digits, `gst_rate_percent` bounds, price checks in `full_clean`), but the database constraints are what actually stop invalid rows from being written. The test file for products checks these by expecting `IntegrityError` from the DB when a constraint is violated, and also checks `ValidationError` from `full_clean` where the model validator catches the same class of problem earlier. The migration `0003_product_and_variant.py` is the concrete evidence for the constraint names and conditions.

## D40. Pricing helpers live in `apps/catalog/services.py`

- **Decision:** Catalog pricing is in pure, database-free helper functions in `apps/catalog/services.py`, and all money uses `Decimal` INR. The main helpers are `round_money` (round to whole paise with halves going up, `ROUND_HALF_UP`), `gst_breakdown` (split a GST-inclusive price into taxable value, GST, CGST and SGST), `savings_amount`, `savings_percent` (whole-number percent, rounded **down** so it is never overstated), and `unit_price` (price per standard quantity such as per kg or per 100 g). `GstBreakdown` and `UnitPrice` are frozen dataclasses.
- **Why:** Keeping pricing logic out of models and views makes it easy to reuse for the storefront, admin, and invoices, and to test without a database. Money rounding and GST math is exactly the kind of code that is easy to get subtly wrong, so it is centralised and labelled as a review point before invoices are issued. The current code does the GST split from a GST-inclusive price, rounds the GST amount, rounds CGST, and sets SGST as the remainder so CGST + SGST always equal the GST amount exactly. Savings percent is rounded down on purpose so the shop does not overstate a discount. Some details are still CA-dependent and are flagged in code comments: the rounding rule, the exact HSN format, and the GST split for intra-state vs inter-state sales (the current code assumes intra-state CGST/SGST for Madhya Pradesh and leaves IGST for a later phase). Because the exact rounding and split rules were not independently confirmed from anything outside the code, this entry is reconstructed from `services.py` and the related tests; the owner should confirm the CA-facing details before invoices go live.

## D41. Product images: model, upload rules and storage

- **Decision:** `ProductImage` belongs to `Product`, not to `ProductVariant`. Fields: `image`, `alt_text`, `sort_order` (the lowest value is the main image; there is no `is_primary` flag) and `width`/`height`, which Django fills in automatically. Rows can be deleted outright (no `ArchivableModel`). Files are stored at `products/<product_id>/<random uuid>.<ext>`. Uploads must be JPG, PNG or WebP, at most 2 MB and 8000 pixels on the longest side, and Pillow must confirm the real file format, so a renamed file is refused (the limits are constants in `apps/catalog/validators.py`). In development files go to the local `MEDIA_ROOT`, served by Django only when `DEBUG` is on. No resizing or WebP conversion yet. Deleting an image row or a product does not delete the file from disk.
- **Why:** All variants of a product share the same photos. Using the lowest `sort_order` as the main image avoids a flag that would need its own constraint. Stored width and height let the storefront reserve space and avoid layout shift on slow phones. Random file names avoid clashes, odd characters and path tricks. Checking the real file content stops a harmless-looking file name hiding another file type. Resizing and WebP are deferred to the storefront slices (23 and 25) or Phase 6, when the needed sizes are known; this narrows the brief's "responsive images in WebP" for now. Production storage stays S3-compatible (D27) and can be switched later through Django's `STORAGES` setting.

## D42. Inventory: ledger-derived stock, loose-by-weight quantities, batch and expiry, supplier, low-stock threshold

- **Decision:**
  1. Current stock is the sum of `quantity_change` over a variant's `InventoryMovement` rows. There is no cached `StockLevel` table. The inventory services will lock the `ProductVariant` row with `select_for_update()`, then read the sum, check it and write the movement in the same transaction.
  2. Quantities are decimals with 3 places (`Decimal(12, 3)`), counted in the variant's own unit. Packet variants stay whole-number: the services reject fractions for them. A variant sold loose by weight has `is_sold_loose=True`, unit `KG` or `L`, `pack_size` 1 (so `selling_price` is the price per kg or per litre) and a `quantity_step` (for example 0.100) that limits what a customer may order. Packet variants have step 1. The step applies to cart quantities (Phase 3), not to the ledger: staff may adjust loose stock by any gram amount. This narrows D23.
  3. A product sold both loose and in packs is two variants with two separate stock pools. Repacking is two `ADJUSTMENT` movements with a reason. A `REPACK` movement type can be added later if it is frequent.
  4. `InventoryMovement` has an optional `batch_number` and `expiry_date`, allowed only on `PURCHASE_RECEIVED` rows (a database check constraint enforces this). There is no first-expiry-first-out logic.
  5. `Supplier` is a minimal optional record (name, phone, notes; archivable). A movement has an optional `supplier` foreign key with `on_delete=PROTECT`. It is not enforced in the database that only purchases carry a supplier.
  6. `ProductVariant.low_stock_threshold` is an optional non-negative decimal (same units as stock). A blank value means no alert. A variant counts as low stock when on-hand is less than or equal to the threshold.
  7. `quantity_change` is never 0. Database constraints: `PURCHASE_RECEIVED` and `RETURN` are positive; `IN_STORE_SALE`, `SALE` and `DAMAGE` are negative; `ADJUSTMENT` may be either sign but needs a reason. Movement rows are never updated or deleted. `created_by` is nullable (imports and system actions).
- **Why:** At about 100 SKUs a sum over indexed rows is fast, and one source of truth cannot drift, so a cache is added only if it proves slow. Locking the variant row gives the services a lock target even though there is no stock row. Some products are sold loose by weight, and changing the quantity type after carts and orders exist would touch much more code, so decimals are decided now, before any cart or order exists. Keeping packets and loose items as separate variants keeps stock math and pricing simple. Expiry tracking is per received lot only: the expiry view can say that a lot is near expiry while the variant still has stock, but it cannot say how many units of that lot remain, because sales are not tied to lots. A supplier record and a low-stock threshold are the simplest forms that meet the brief.
Signed quantities with per-type sign rules in the database stop a wrongly signed movement from being written.

## D43. Inventory services: single write path, locking, quantity rules, negative stock, integrity check

- **Decision:**
  1. The functions in `apps/inventory/services.py` are the only code that writes `InventoryMovement` rows, through one private function `_record_movement`. Admin actions, imports and other apps call the services and never insert movements directly.
  2. Every write runs inside `transaction.atomic()`, locks the `ProductVariant` row with `select_for_update()` (reloading it, so its flags are current), then reads the sum, checks the rules and inserts the movement. One variant per call. Work that touches several variants must lock them in ascending `pk` order to avoid deadlocks. This relies on PostgreSQL's default READ COMMITTED isolation; changing the isolation level means revisiting the services.
  3. Quantities must be `Decimal` or `int` (not float, bool, str, NaN or Infinity), have at most 3 decimal places, and have an absolute value below 1,000,000,000. They are stored with 3 decimals. A packet variant (`is_sold_loose` false) takes whole numbers only. A loose variant takes any 3-decimal amount; its `quantity_step` applies to carts, not to the ledger. `reason` is stripped of surrounding whitespace and limited to 200 characters; `batch_number` is limited to 60.
  4. Functions: `get_on_hand(variant)`; `receive_stock` (positive quantity; optional supplier, batch number, expiry date and reason; an archived supplier is rejected; the expiry date must be a `date`, and a past date is allowed); `adjust_stock` (signed quantity, reason required); `record_damage` (positive input stored as a negative movement, reason required, may not take on-hand below 0); `count_stock` (compares a counted quantity with on-hand under the lock and writes one `ADJUSTMENT` for the difference, nothing if equal, reason required).
  5. Negative stock: only `adjust_stock` and `count_stock` may leave on-hand below 0, because they carry an explicit reason, and they log a warning. `record_damage` and later sales may not.
  6. Archived variants can still be received and adjusted, so discontinued stock can be written off.
  7. Errors: `InventoryError` (subclass of `ValueError`), with `InvalidQuantityError` and `InsufficientStockError` below it.
  8. `find_integrity_problems()` and the management command `check_inventory_integrity` are read-only and never fix anything. ERROR: a fractional movement on a non-loose variant; an `ADJUSTMENT` whose reason is only whitespace; a supplier on a movement that is not a purchase. WARNING: a variant with negative on-hand. The command exits with code 1 only when there is an ERROR.
  9. Not built in Slice 16: writers for `SALE` (Phase 3/4, exactly one function), `IN_STORE_SALE` and `RETURN`. They will reuse `_record_movement`.
  10. The ledger already records who, when and why for every stock change (`created_by`, `created_at`, `reason`), so the AuditLog planned for Slice 17 should not duplicate stock movements.
- **Why:** A single write path under one lock is the only way the "check, then write" step stays safe when two people act on the same variant at once; a lock only protects code that takes it, so nothing may bypass the services. Rejecting floats and extra decimals keeps stored quantities exact, and the packet-versus-loose rule keeps whole packets whole. Negative stock is allowed only through an explicit, reasoned correction, as the architecture requires, so a sale or damage entry can never hide a miscount. The integrity check finds rows that got in around the services (raw SQL, or a variant switched from loose to packet later), since the database constraints cannot express those rules. Deferred writers wait for the phases that need them.
