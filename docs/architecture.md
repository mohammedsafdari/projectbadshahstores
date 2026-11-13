# Badshah Store — Architecture

Single modular monolith: one Django deployable app, Django apps under `apps/`, business logic in `services.py` per app, thin views. Money is `Decimal` INR with 2 decimals, never `float`. Timezone `Asia/Kolkata`, `USE_TZ = True`, locale `en-IN` are Django settings, not shop settings.

**Status:** Phase 1 (scaffold) is done and Phase 2 (catalogue and inventory) is in progress. The data-model, integrity and India-specific sections below describe the target design. What is actually built right now, and what comes next, is tracked in `docs/STATUS.md`.

## 0. Initial configuration values

Seed values stored in the database, not hardcoded in logic.

- Delivery fee: ₹30
- Free delivery above: ₹500
- Minimum order: ₹200 on the product subtotal (excludes delivery fee)
- COD cap: ₹2,000, checked on the order total at checkout only
- Delivery day (MVP): no slot is shown; an order placed before the 17:00 cutoff is delivered the same day, otherwise the next day, with no time window (D55 amendment)
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

- `accounts` address book pages: `address_list` `/account/addresses/`, `address_add` `/account/addresses/add/`, `address_edit` `/account/addresses/<int:pk>/edit/`, `address_archive` `/account/addresses/<int:pk>/archive/` and `address_set_default` `/account/addresses/<int:pk>/default/` (D65).

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
- Status machine: `PLACED, PAYMENT_PENDING, PAID, CONFIRMED, PACKING, OUT_FOR_DELIVERY, DELIVERED`, plus `CANCELLED, PAYMENT_FAILED, REFUND_PENDING, REFUNDED, PARTIALLY_REFUNDED`. COD and credit both skip the payment states.
- `payment_method` is `COD` or `CREDIT`; a credit order stores `0.00` as `amount_due_on_delivery`.
- Every transition logged in `OrderStatusLog`.
- `OrderItem`: `quantity_ordered`, `quantity_fulfilled`, `item_status`, snapshots of price/GST/HSN/name.
- Substitutions: keep the original line with status `SUBSTITUTED` and add a new line with a `replaces` link; the substitute is charged at its current selling price with GST recomputed; the customer chooses a substitution preference at checkout (`substitute / refund / call me`).
- At end of packing the order is finalised: unfulfilled quantities are cancelled, reservations released, and a refund or adjustment made.
- One order = one invoice.
- `Order.checkout_group` is unique and adopts the stock-reservation group, so one checkout can create at most one order. Placing a COD or credit order freezes the address and money, writes the order and items, writes `"" → PLACED` and `PLACED → CONFIRMED` log rows and clears the cart in one transaction (D64).
- A credit order is charged to the customer's khata in the same transaction; cancelling it before delivery reverses the charge, and a COD cancel writes no credit row (D68).
- A coupon (D82): `Order` carries `discount_total` (the item discount), `delivery_discount` and `coupon_code` (20 characters, blank by default, stored in capitals). `delivery_fee` stays the full fee the cart worked out, so the one equation is `grand_total = item_total - discount_total + delivery_fee - delivery_discount`, with `order_discounts_gte_0`, `order_item_discount_lte_item_total` and `order_delivery_discount_lte_fee`. `place_order(..., coupon_code="")` judges a non-blank code with `evaluate_coupon(..., for_update=True)` after the user lock and before the stock reservation, raises `CouponRefusedError` (`reason`, `message`) on a refusal so nothing is written, and writes the `CouponRedemption` with the order. `apps/promotions/services.py:release_redemption` runs in the cancel transaction and sets `released_at` only when the order has no invoice; a `PAYMENT_FAILED` order keeps its use (D77).
- Checkout pages (D66): `checkout` at `/checkout/` (GET and POST) and `order_confirmation` at `/checkout/done/<uuid:group>/` (GET). The first GET stores a UUID in `request.session["checkout_group"]` and the form carries it back; a repeated group returns the same order's confirmation instead of a second order, and the confirmation is visible only to the order's owner. Credit is offered only to an active account, and the fixed lock order is the user row, then variants ascending, then order rows, then the credit account last (D67, D68).

### Credit (khata)
- `CreditAccount`: one per user (`OneToOneField`), `credit_limit` and cached `balance` (`DecimalField(12, 2)`), and `is_active`. A suspended account or a user without an account has no available credit.
- `CreditEntry`: an append-only ledger row per change, with `amount`, `balance_after`, `entry_type`, optional `order`, `payment_mode`, `reference`, `note`, `entry_date` and `created_by`. `save()` on an existing row, `delete()` and queryset update/delete raise `AppendOnlyError`.
- Sign convention: positive means the customer owes more (`ORDER`), negative means less (`PAYMENT`, `CANCEL_REVERSAL`); `ADJUSTMENT` is either sign but never zero. `balance` is what the customer owes now and `available = max(credit_limit - balance, 0)`.
- Services in `apps/credit/services.py`: `set_credit_limit`, `set_credit_active`, `available_credit`, `charge_order`, `reverse_order_charge`, `record_payment` and `record_adjustment`. A charge and its reversal are idempotent: partial unique constraints allow one ORDER row and one CANCEL_REVERSAL row per order. `check_credit_integrity()` proves the cached balance, the `balance_after` chain and each ORDER entry against the order total; it also reports `credit_order_without_charge`, `cancelled_credit_order_not_reversed` and `charge_on_non_credit_order` (D68).
- Lock order: the user row, then stock variants in ascending id, then order rows, then the credit account row last. Limit and status changes write `AuditLog` rows through `record_change`; the admin lets owners and managers record payments and adjustments through the services, and never saves an entry directly.

### Numbering
- `NumberSeriesCounter` keyed by `(prefix, financial_year)`, incremented inside a locked transaction (`select_for_update`); no reset job.
- Financial-year label: `start = year if month >= 4 else year - 1`; `label = f"{start % 100:02d}-{(start + 1) % 100:02d}"` (e.g. `26-27`). Tests for Mar 31, Apr 1, Dec 31.
- Order numbers: `BS/26-27/000001`. `next_number(prefix, when)` locks the counter row inside the order transaction; a blank `ShopSettings.order_number_prefix` falls back to `BS` (D64).
- Invoice numbers: `INV/26-27/000001`; max 16 characters (GST rule, to be confirmed by CA); sequential and gapless.

### Invoices and refunds
- Built in Slice 43 (D80). `orders.Invoice` is one to one with `Order` (`PROTECT`): `invoice_number` (unique, 16 characters, `INV/26-27/000001`), `issued_at`, the frozen seller (`seller_name`, `seller_address`, `seller_gstin`, `seller_state`, `seller_state_code`), the frozen buyer (`buyer_name`, `buyer_phone`, `buyer_address`), `place_of_supply`, `taxable_total`, `cgst_total`, `sgst_total`, `gst_total`, `delivery_fee`, `grand_total`, `cancelled_at` and `cancel_reason`. The named constraints are `invoice_money_gte_0`, `invoice_cgst_plus_sgst_matches` and `invoice_grand_total_matches`.
- A discounted invoice (D82) adds `Invoice.discount_total` and `Invoice.coupon_code` and `InvoiceLine.discount_amount` (all at least 0 in the existing money constraints). `_allocate_discount(grosses, discount)` shares the item discount by each line's gross value and gives the remainder to the last line with a gross above zero; the line's GST is then split from the discounted gross and `rate` stays the full unit price. The fee line is the fee actually charged, and there is none when it is 0.00. The PDF prints a `Disc.` column and a `Discount (CODE)` totals line, and `find_invoice_problems` also reports `total_mismatch` and `discount_mismatch`.
- `orders.InvoiceLine` holds `description`, `hsn_code`, `unit`, `quantity`, `rate`, `gst_rate_percent`, `taxable_value`, `cgst`, `sgst`, `line_total` and `is_fee_line`, under `invoiceline_money_gte_0` and `invoiceline_total_matches`. The delivery charge is the one line with `is_fee_line=True`, taxed at `ShopSettings.delivery_fee_gst_percent` (default 18.00, unconfirmed).
- The invoice is generated at send-out from the fulfilled quantities, inside the same transaction as the `SALE` movements (`apps/orders/invoices.py`). Its number comes from `next_number`, so it is sequential and gapless, and a rolled-back send-out gives the number back. Every printed value is frozen, so a later edit to the settings or the catalogue never changes an issued invoice.
- `mark_invoice_cancelled` marks a cancelled order's invoice with the reason and keeps its number; nothing is deleted or renumbered. The customer cancel path cannot reach a sent-out order, so it has no hook.
- Pages: `/staff/orders/<order number>/invoice/` and `/staff/orders/<order number>/invoice.pdf` need `Capability.VIEW_INVOICES` (owner and manager by default), and `/orders/<checkout group>/invoice.pdf` is the order owner's own copy. `apps/orders/invoice_pdf.py` draws the PDF with reportlab; `pdf_text` replaces a character the built-in fonts cannot draw with `?` and prices print as `Rs. 1234.56`.
- `python manage.py check_invoice_integrity` reports a line sum that differs, a sent-out order with no invoice and a gap in a number series, read-only (D80).
- Refunds after an invoice need a credit note (`CreditNote` model later; noted in docs, not built in MVP, and still an open question).

### Payments

- Built in Slice 40 (D77). `apps/payments/gateway.py` is the provider boundary: `PaymentSession`, `GatewayEvent` (with the allow-listed `safe` payload), `PaymentGatewayError`, `GatewayNotConfigured`, `InvalidWebhookSignature`, `get_gateway`, `online_payment_available` and `enabled_payment_methods`. A provider offers `create_session`, the header-based `parse_webhook(body, headers)`, `fetch_order_payments(gateway_order_id)`, `create_refund(gateway_payment_id, amount, idempotency_key)` and `fetch_payment_refunds(gateway_payment_id)`. Everything outside `razorpay_gateway.py` and `templates/payments/gateways/razorpay.html` uses that interface only; `gateways.py` maps `PAYMENT_PROVIDER` to `RazorpayGateway` or to the deterministic `FakeGateway`, which is refused unless `PAYMENT_ALLOW_FAKE_GATEWAY` is on.
- `Payment` is one attempt for the online part of an order: a unique provider order id, `CREATED`/`CAPTURED`/`FAILED` (plus `REFUND_PENDING`, `REFUNDED` and `PARTIALLY_REFUNDED` for Slice 41), a provider payment id, method, failure text, `needs_refund`, `captured_at`, `failed_at` and the allow-listed reply. `Order.online_amount` sits beside `credit_applied` and `amount_due_on_delivery`. `WebhookEvent` stores each provider event once (unique `event_id`), with its type, the same allow-listed payload, linked payment, processed time and error.
- Webhook flow: the neutral `/payments/webhook/<provider>/` view checks the provider name (404 for any other), verifies the provider signature (`hmac.compare_digest`), then in one transaction inserts the `WebhookEvent` row (the unique constraint is the idempotency gate; a repeat returns 200 and applies nothing), locks the order and then the payment, checks the amount, applies one event once through the shared apply step and marks it processed. The return page calls `refresh_payment_from_gateway`, which asks `fetch_order_payments` outside any transaction or lock, prefers a captured event over a failed one, then locks the order and the payment and applies the same step without writing a `WebhookEvent`; the status fragment stays database-only. A capture walks the order `PAYMENT_PENDING` → `PAID` → `CONFIRMED` and sends the placed and staff notices once; a failure sets `PAYMENT_FAILED` and keeps the hold for a retry. Out-of-order and late events move the order only through the allowed transitions, and a capture for an order that is no longer waiting for it never revives it.
- `Refund` is built in Slice 41 (D78): one row per captured payment, `CUSTOMER_CANCEL` or `SHOP_CANCEL`, `REQUESTED`/`SENT`/`PROCESSED`/`FAILED`, the amount sent back plus the fee the shop keeps (both always adding up to the payment), a unique idempotency key `refund-<payment pk>`, the provider refund id, attempts and times. `apps/payments/refunds.py` owns `ensure_refund`, `send_refund` (claim the attempt under the lock, call the provider outside it, store the answer under the lock again), `apply_gateway_refund`, `refresh_refund_from_gateway` and `reconcile_refunds`; the lock order is the order, then the payment, then the refund. A cancellation creates the refund in its own transaction and queues the provider call with `transaction.on_commit`, so a rolled-back cancel never refunds. `needs_refund` stays true until a refund is `PROCESSED`; the order stays `CANCELLED` and the payment row stays `CAPTURED`. The provider boundary gains `GatewayRefund`, `create_refund` and `fetch_payment_refunds`. `refund.processed` and `refund.failed` are matched by provider payment id; an unknown refund event is stored, ignored and explained in `WebhookEvent.error`. `check_payment_integrity` proves the money rules read-only, and `send_pending_refunds` runs the reconciliation every 300 seconds through Celery beat.
- `expire_unpaid_online_orders` cancels an online order older than the shop's reservation window plus `PAYMENT_GRACE_MINUTES`, releases the hold, restores the order lines to the saved cart and sends `EVENT_PAYMENT_FAILED` after commit. It runs every 300 seconds through the Celery beat entry and is also the `expire_unpaid_orders` management command.
- Razorpay amounts are in paise: the exact `Decimal` conversion happens only inside `razorpay_gateway.py`; the stored online amount always has two decimal places, so multiplying by 100 always gives whole paise, and no float rounding is used. Incoming webhook paise are divided by 100 and quantised to two places. No fee, MDR or "UPI is free" value is stored in code or shown to the customer.

### Coupons
- `Coupon` is a `TimeStampedModel` (D81): `code` (unique, max 20, stored in capitals, 4 to 20 characters from A-Z, 0-9 and -), `description` (max 200, staff only), `kind` (`PERCENT`, `FIXED`, `FREE_DELIVERY`), `percent_off` (max 100), `max_discount`, `amount_off`, `min_order_value` (default 0), `total_limit` and `per_customer_limit` (both optional, the second defaults to 1), `starts_at` and `ends_at` (optional, aware; valid while `starts_at <= now < ends_at`), `first_order_only` and `is_active`. Money is `Decimal` with two places, never a float. `save()` and `clean()` trim and upper-case the code; `clean()` also refuses a bad code format, a wrong or missing field for the kind, and an end that is not after the start. Nine named check constraints repeat the rules in the database: `coupon_percent_off_range`, `coupon_amount_off_gt_0`, `coupon_max_discount_gt_0`, `coupon_min_order_value_gte_0`, `coupon_total_limit_gte_1`, `coupon_per_customer_limit_gte_1`, `coupon_ends_after_starts`, `coupon_kind_fields_match` and `coupon_code_is_upper` (`code = UPPER(code)`).
- `CouponRedemption` records one use: `coupon`, `order` and `user` (all `PROTECT`), `discount_amount` and `delivery_discount_amount` (at least 0), `released_at` and `created_at`, with the `(coupon, order)` unique constraint (D16). A released row counts against no limit. A coupon is never deleted; staff switch it off.
- `apps/promotions/rules.py` holds the one decision: `CouponResult` and `evaluate_coupon(raw_code, *, user, item_total, delivery_fee, now=None, for_update=False)`, with the check order `LOGIN_REQUIRED`, `NOT_FOUND`, `INACTIVE`, `NOT_STARTED`, `EXPIRED`, `BELOW_MINIMUM`, `NOT_FIRST_ORDER`, `TOTAL_LIMIT_REACHED`, `PER_CUSTOMER_LIMIT_REACHED`, `NO_BENEFIT`. The coupon discounts the item total only; only `FREE_DELIVERY` touches the fee; the minimum is checked before the discount; nothing exceeds the item total. `for_update=True` locks the coupon row (`select_for_update`) while the limits are checked and redeemed, so two checkouts cannot both take the last use. `NOT_COUNTING_STATUSES` decides which orders leave a customer "new". Slice 44b wires it into checkout.

### Delivery
- `DeliveryZone` carries the zone name and an active switch; the fee and free-delivery threshold are shop-wide in `ShopSettings` (D58, D62).
- `PinCodeZone` only links pin codes to a `DeliveryZone`.
- `DeliverySlotDefinition` holds cutoff and capacity; `DeliverySlotDate` per date; not built in the MVP, no slots are shown (D55 amendment).
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
- Payments: Razorpay, test mode only during Phase 4; the live provider (Razorpay or Cashfree) is chosen at KYC time (D76).
- OTP/SMS/WhatsApp: MSG91 for SMS behind the abstraction, with DLT registration; console backend in development. WhatsApp in the MVP is only the free `wa.me` button, with no WhatsApp Business API (D70, D76).
- Email: Brevo free (300 emails a day), console backend in development until real credentials; SPF and DKIM on the domain (D76).
- Hosting and costs: see `docs/hosting_and_costs.md` and D76.
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
- Deployment target: one paid VPS in an India region (reference DigitalOcean Bangalore; the provider is not final), running the same Docker Compose stack as development, with Gunicorn + Nginx, PostgreSQL 16 in Compose on the same server and a nightly encrypted `pg_dump` to Cloudflare R2, HTTPS via Let's Encrypt, Sentry, uptime monitoring. Oracle Cloud Always Free is staging only (D76, `docs/hosting_and_costs.md`).
- Security: OWASP Top 10 patterns, CSRF, XSS-safe templates, secure session cookies, HTTPS-only, HSTS, security headers, CSP. Parameterised queries via the ORM. Rate-limit login/OTP/checkout/search. Never log secrets, OTPs, or full payment data. Encrypt sensitive config; rotate keys. Validate uploads (type, size) and CSV imports.
- Search: start with PostgreSQL full-text + trigram similarity behind an interface; semantic/embedding search later.
- Media: Cloudflare R2 through the S3 API (`django-storages`) in production, local storage in dev, with image resizing (D27, narrowed by D76).
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
