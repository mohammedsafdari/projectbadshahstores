# Badshah Store — Architecture

Single modular monolith: one Django deployable app, Django apps under `apps/`, business logic in `services.py` per app, thin views. Money is `Decimal` INR with 2 decimals, never `float`. Timezone `Asia/Kolkata`, `USE_TZ = True`, locale `en-IN` are Django settings, not shop settings.

**Status:** Phase 1 (scaffold) is done and Phase 2 (catalogue and inventory) is in progress. The data-model, integrity and India-specific sections below describe the target design. What is actually built right now, and what comes next, is tracked in `docs/STATUS.md`.

## 0. Initial configuration values

Seed values stored in the database, not hardcoded in logic.

- Delivery fee: ₹30
- Free delivery above: ₹500
- Minimum order: ₹200 on the product subtotal (excludes delivery fee)
- COD cap: ₹2,000, checked on the order total at checkout only
- Delivery slot (MVP): single "Same Day" slot with a 16:00 cutoff
- Catalogue size at launch: about 100 SKUs

These live in `DeliveryZone`, `DeliverySlotDefinition`, and `ShopSettings`. Logic reads them from the database; it does not bake them in.

## 1. Django app boundaries

| App | Responsibility |
| --- | --- |
| `core` | Shared base classes, number-series/financial-year helpers, settings singleton accessors, generic utilities. |
| `accounts` | Custom user model, phone OTP auth, staff password auth, addresses, customer notes. |
| `catalog` | Brand, Category, Product, ProductVariant, ProductImage, search. |
| `inventory` | InventoryMovement ledger, StockReservation, availability calculation, stock operations. |
| `cart` | Cart, CartItem, guest-cart merge on login, stock-aware limits. |
| `orders` | Order, OrderItem, OrderStatusLog, state machine, substitutions, finalization. |
| `payments` | Payment, Refund, WebhookEvent, Razorpay gateway boundary, idempotency, reconciliation. |
| `delivery` | DeliveryZone, PinCodeZone, DeliverySlotDefinition, DeliverySlotDate, DeliveryAssignment, CashSettlement. |
| `notifications` | Notification abstractions (SMS, WhatsApp, email, console), NotificationLog. |
| `promotions` | Coupon, CouponRedemption, Banner, FeaturedProduct. |
| `storefront` | Customer-facing templates/views, home, catalogue, product, cart, checkout, order tracking, account. |
| `adminpanel` | Django admin customizations, actions (CSV import/export, pick list, invoice print, bulk price/stock). |

## 2. Data model

### Shop settings
- `ShopSettings` singleton (one row): shop name, address, contact, GSTIN, FSSAI number + expiry, grievance officer details, minimum order value, COD cap, reservation minutes, online stock buffer, order/invoice number prefixes, `is_published`.
- Short-TTL cache only; no timezone/locale/fiscal-year fields here.

### Catalog
- `Brand`
- `Category`: self-referential parent FK for hierarchy.
- `Product`: `is_own_brand` flag only on Product; `name_en`, `name_hi`; veg/non-veg; search keywords; no per-product FSSAI fields.
- `ProductVariant`: `sku_code` (internal, unique) separate from `barcode` (nullable); unit choices `KG / G / ML / L / PIECE / PACKET`; HSN code; editable `gst_rate_percent`; `mrp`; `selling_price`; `cost_price`; own-brand label fields on the variant: net quantity, best-before label, ingredients, allergens, shelf life, manufacturer details.
- `ProductImage` on Product (not on variant): `image`, `alt_text`, `sort_order` (lowest is the main image), automatic `width` and `height`. Files are stored at `products/<product_id>/<random>.<ext>`; see D41.

### Inventory

- Decided in D42: stock is derived from the ledger, with no cached stock table; batch number and expiry date are optional and only on received stock; `Supplier` is a minimal optional record; the low-stock threshold is an optional number per variant.
- Quantities are decimals with 3 places, counted in the variant's own unit. Packet variants stay whole-number (the services reject fractions). A variant sold loose by weight has `is_sold_loose`, unit `KG` or `L`, pack size 1 and a `quantity_step`, so its selling price is per kg or per litre. A product sold both loose and in packs is two variants with separate stock; repacking is two `ADJUSTMENT` movements with a reason (D42, narrows D23).
- No guest checkout in the MVP: OTP login is required at checkout.
- `InventoryMovement` (immutable ledger): types `PURCHASE_RECEIVED, IN_STORE_SALE, SALE, RETURN, DAMAGE, ADJUSTMENT`. No `TRANSFER`.
- `InventoryMovement` fields: variant, type, signed quantity, reason, optional supplier, batch number and expiry date (purchases only), `created_by` (null for imports and system actions) and `created_at`. Database rules: the quantity is never 0; purchases and returns are positive; in-store sales, sales and damage are negative; adjustments may be either sign and need a reason. Rows are append-only: `AppendOnlyError` blocks updates and deletes.
- `Supplier`: unique name, phone, notes; archivable. A supplier or variant with movements cannot be deleted.
- `on_hand = sum(movements)`.
- `StockReservation`: status `ACTIVE / CONSUMED / RELEASED`; `reservation_kind` `CHECKOUT_PENDING_PAYMENT` or `HELD_FOR_FULFILMENT`.
- When an online payment is captured, or a COD order is placed, the reservation changes from `CHECKOUT_PENDING_PAYMENT` to `HELD_FOR_FULFILMENT` inside the same transaction as the order status change.
- Expiry (15 minutes, configurable) applies only to unpaid online checkouts. COD and paid orders hold their reservation until packing, cancellation or delivery.
- `held = sum(ACTIVE reservations excluding expired checkout ones)`.
- `available = max(0, on_hand - held - online_stock_buffer)`.
- Online sales check `available`; in-store sales check `on_hand`.
- `SALE` movement is written when each item is marked fulfilled at packing, in the same transaction that consumes the matching reservation, so stock is never double-deducted.
- Only one service function may write `SALE` movements.
- No negative stock from sales. Only an explicit adjustment with a reason may create negative `on_hand`, logged with a warning.
- If a payment is captured after its reservation expired: try to re-reserve; if stock is gone, auto-refund and notify the customer.

### Cart
- `Cart`, `CartItem`.
- Persistent for logged-in users; guest cart merged on login.
- No guest checkout in the MVP: OTP login is required at checkout.

### Orders
- `Order`: `user` required; no `is_active`. Address and price snapshots on the order.
- `amount_due_on_delivery` recalculated after substitutions or partial fulfilment; replaces any `cod_amount` field.
- Status machine: `PLACED, PAYMENT_PENDING, PAID, CONFIRMED, PACKING, OUT_FOR_DELIVERY, DELIVERED`, plus `CANCELLED, PAYMENT_FAILED, REFUND_PENDING, REFUNDED, PARTIALLY_REFUNDED`. COD skips the payment states.
- Every transition logged in `OrderStatusLog`.
- `OrderItem`: `quantity_ordered`, `quantity_fulfilled`, `item_status`, snapshots of price/GST/HSN/name.
- Substitutions: keep the original line with status `SUBSTITUTED` and add a new line with a `replaces` link; the substitute is charged at its current selling price with GST recomputed; the customer chooses a substitution preference at checkout (`substitute / refund / call me`).
- At end of packing the order is finalised: unfulfilled quantities are cancelled, reservations released, and a refund or adjustment made.
- One order = one invoice.

### Numbering
- `NumberSeriesCounter` keyed by `(prefix, financial_year)`, incremented inside a locked transaction (`select_for_update`); no reset job.
- Financial-year label: `start = year if month >= 4 else year - 1`; `label = f"{start % 100:02d}-{(start + 1) % 100:02d}"` (e.g. `26-27`). Tests for Mar 31, Apr 1, Dec 31.
- Order numbers: `BS/26-27/000001`.
- Invoice numbers: `INV/26-27/000001`; max 16 characters (GST rule, to be confirmed by CA); sequential and gapless.

### Invoices and refunds
- Invoice generated at packing/dispatch using fulfilled quantities.
- Refunds after an invoice need a credit note (`CreditNote` model later; noted in docs, not built in MVP).

### Payments
- `Payment`, `Refund`, `WebhookEvent` (unique `event_id`).
- Webhook flow: verify Razorpay signature FIRST, then in one transaction insert the `WebhookEvent` row (unique constraint is the idempotency gate; on conflict return 200), process, mark processed.
- Out-of-order events handled through allowed state transitions only.
- Razorpay amounts are in paise: exact `Decimal` conversion at the gateway boundary only; raise on fractional paise; no float rounding.

### Coupons
- `Coupon`, `CouponRedemption` with a unique constraint on `(coupon, order)`. Lock the coupon row (`select_for_update`) while checking limits and redeeming.

### Delivery
- `DeliveryZone` carries the fee and free-delivery threshold.
- `PinCodeZone` only links pin codes to a `DeliveryZone`.
- `DeliverySlotDefinition` holds cutoff and capacity; `DeliverySlotDate` per date.
- Slot capacity is computed on the fly inside the order-placement transaction with a lock; no cached `orders_count`.
- `DeliveryAssignment`, `CashSettlement` for COD cash reconciliation.

### Auth and security
- Customer auth: phone + OTP login; optional email.
- Staff auth: password login.
- OTP stored as an HMAC (server secret over phone + purpose + code) with constant-time comparison, short expiry, limited verify attempts, rate limits per phone.
- Never log OTP codes or message bodies, including in `NotificationLog`.
- Authorization checks on every object access.

### Supporting models
- `Cart/CartItem`, `Coupon/Banner/FeaturedProduct`, `NotificationLog`, `AuditLog`.

## 3. Service and integration boundaries

- Business logic lives in `services.py` per app; views are thin.
- Integrations are behind interfaces: SMS/OTP/WhatsApp provider, email provider, payment gateway, search, shipping (future).
- Console backend used in development for notifications and (until real credentials) for OTP/SMS/WhatsApp and email.

### CSV import flow (Slice 20, D47)

The "Import CSV" page on the variant list uploads a file, parses it (`csv_import.py`) and plans it against the database (`csv_import_plan.py`) without writing anything: each row becomes CREATE, UPDATE or UNCHANGED, with issues for bad cells and for clashes only the database can see (an unknown brand or category slug, a SKU that differs only in letter case, a SKU under another product, a barcode another SKU holds). Confirm keeps the uploaded bytes in the session together with their SHA-256 and the plan's fingerprint. `apply_import` (`csv_import_apply.py`) locks products and then variants in ascending `pk` order inside one `transaction.atomic()`, plans again under the lock, refuses when the fingerprint has moved, and then writes products and variants row by row with `full_clean()`, one audit row per created or changed variant (`VARIANT_CREATE`, `VARIANT_UPDATE`). Any problem rolls the whole file back, so a file is applied completely or not at all; `bulk_create()` and queryset `update()` are never used because they skip validation and the audit call.

### Provider defaults (locked)
- Payments: Razorpay, test mode only until told otherwise.
- OTP/SMS/WhatsApp: MSG91 behind abstraction; console backend in development; real integration later after DLT registration.
- Email: Brevo, console backend in development until real credentials.
- Shipping (later business phase, own-brand pan-India): abstraction ready for Shiprocket or Delhivery; not built in MVP.

## 4. Inventory and order integrity rules (REVIEW CAREFULLY)

- `select_for_update()` and database transactions wherever stock or payment state changes, to avoid race conditions.
- Reservation expiry only for unpaid online checkouts; COD and paid orders hold until packing/cancellation/delivery.
- `SALE` movements written only at fulfilment, in the same transaction as reservation consumption.
- No negative stock from sales; only explicit adjustment may create negative `on_hand`, with reason and warning.
- Payment captured after reservation expiry: re-reserve, and if stock gone, auto-refund and notify.
- Webhook idempotency via unique `WebhookEvent.event_id`; webhook, not the browser redirect, is the source of truth for payment status.
- Duplicate webhooks must never double-charge, double-ship, or double-decrement stock.

## 5. India-specific handling

- GST: HSN code and GST rate per variant, editable data, never hardcoded. Default own-brand packaged-food GST `5%` until confirmed by CA. Compute and display GST-inclusive prices (MRP is inclusive in India).
- Invoicing: GSTIN and HSN on invoice; CGST/SGST split for intra-state Madhya Pradesh sales; IGST for inter-state (later business phase, Phase 8 in the build plan). Support for inter-state is wired as a capability, not necessarily used in Phase 1.
- Legal Metrology: own-brand variant fields (net quantity, MRP, manufacturer details, best before) shown on product pages.
- FSSAI: licence number in footer and on own-brand product pages; expiry tracked.
- Consumer Protection (E-Commerce) Rules, 2020: seller details, grievance officer contact, transparent pricing, refund/cancellation policy, no hidden charges.
- DPDP Act: collect only necessary data, consent for marketing, data export/deletion path, privacy policy.
- Currency/locale: INR `₹` formatting with Indian digit grouping, IST timezone, `en-IN`.
- Hindi (Devanagari) product names and addresses supported; fonts must render correctly.

## 6. Frontend

- Server-rendered Django templates + HTMX + Alpine.js with Tailwind CSS.
- Mobile-first, accessible, fast on slow networks: lazy-load images, small JS payload, responsive images in WebP. Target Lighthouse performance >= 85 on mobile.
- Phase 1 uses Tailwind via pinned CDN only. A build step for Tailwind is needed before launch; recorded in decisions.
- HTMX and Alpine.js vendored into `static/` rather than loaded from a CDN.
- i18n scaffolding from the start using Django's translation framework; Hindi added later.

## 7. Infrastructure and quality

- Python 3.12, Django 5.x (current LTS), PostgreSQL 16.
- Celery + Redis for background jobs.
- Docker + docker-compose for local dev: web, postgres 16, redis, celery worker, celery beat.
- Environment config via `django-environ`; secrets from environment only; `.env.example` with placeholders; `.env` in `.gitignore`.
- Tooling: `uv` + `uv.lock`, pin all dependencies; `ruff` (lint + format); `mypy` with `django-stubs`; `pytest` + `pytest-django`; `factory_boy`; pre-commit hooks.
- CI: GitHub Actions workflow `.github/workflows/ci.yml`, run on push to `main` and on pull requests. One job, `checks`, on `ubuntu-24.04` with a Postgres 16 service container. Steps: `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`, `pytest`. Secret scanning (gitleaks) runs in pre-commit. Job env is defined in ci.yml.
- Deployment target: single VPS or managed container service in India region (DigitalOcean Bangalore / AWS Mumbai / Azure Central India) with Gunicorn + Nginx, managed PostgreSQL with daily backups, HTTPS via Let's Encrypt, Sentry, uptime monitoring.
- Security: OWASP Top 10 patterns, CSRF, XSS-safe templates, secure session cookies, HTTPS-only, HSTS, security headers, CSP. Parameterised queries via the ORM. Rate-limit login/OTP/checkout/search. Never log secrets, OTPs, or full payment data. Encrypt sensitive config; rotate keys. Validate uploads (type, size) and CSV imports.
- Search: start with PostgreSQL full-text + trigram similarity behind an interface; semantic/embedding search later.
- Media: S3-compatible object storage (or local in dev) with image resizing.
- Tests required for pricing/GST, cart totals, stock reservation and races, order state transitions, webhook idempotency, coupon rules, permissions, delivery-zone/slot validation; high coverage on `services/`.
- Structured logging, Sentry, health-check endpoint, database backups with documented restore procedure.
- Accessibility: semantic HTML, labels, keyboard navigation, sufficient contrast.

## 8. Deferred (not built in MVP)

- PWA (manifest + service worker).
- Full Hindi i18n content (scaffolding now, content later).
- Pan-India shipping for own-brand products (Shiprocket/Delhivery).
- Subscriptions / monthly basket.
- Loyalty program.
- FastAPI microservice for AI features (semantic product search, WhatsApp ordering assistant, demand forecasting). Business logic stays in service modules callable from an API later.

## 9. Open items

See `docs/open_questions.md`. Items that affect architecture are flagged there with who must answer.
