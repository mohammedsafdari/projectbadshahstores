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
