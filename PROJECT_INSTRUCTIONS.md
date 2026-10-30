You are a senior full-stack engineer and technical lead. Help me design and build a production-ready, custom e-commerce website for my retail provision (grocery/kirana) shop in Indore, India. I am a software developer but **new to Python**, so explain non-obvious Python/Django idioms briefly as you go, prefer clear and conventional code over clever code, and add short comments where a Python beginner might get confused.

Work in small vertical slices. After each slice: show the files changed, how to run it, how to test it, and what to review. Ask me before making any significant assumption not covered here. Do not invent business rules; if something is missing, list it as an open question.

---

### 1. Business context

- **Business**: Retail provision shop (daily-needs/grocery store) in **Indore, Madhya Pradesh, India**. Shop name: `Badshah Store`. Locality: `Saifee Nagar`.
- **What we sell**:
  1. **Third-party branded products** (packaged staples, snacks, beverages, household items, etc.) bought from other brands/distributors.
  2. **Our own branded products**: `spices`. These are higher margin and should be promotable, and eventually shippable across India.
- **Catalogue size at launch**: `100` SKUs (plan for growth to several thousand).
- **Goal**: Take the business online. Phase 1 is a **hyperlocal delivery** store for Indore: customers browse, order, pay online or cash on delivery, and receive delivery within a defined area and time slot. Phase 2 adds pan-India shipping for our own-brand products.
- **Customers**: Mostly mobile users on average Indian 4G connections, many on mid/low-range Android phones. Many prefer UPI and cash on delivery. Many search and talk in **Hinglish / Hindi / English**. Repeat purchases (weekly/monthly staples) matter far more than one-off orders.
- **Delivery**: `Own Staff`. Delivery radius: `10km`. Minimum order value: `200 rs`. Delivery fee: `30 rs`. Free delivery above `500 rs`. Delivery slots: `all day`. Same-day cutoff time: `4 PM`.
- **Existing systems**: Billing/POS software: `custom billing software`. Can it export stock/prices (CSV/API)? `Yes`.
- **Owner/staff using the admin**: `2` people, low-to-moderate technical comfort. The admin must be simple, fast and mobile-friendly.
- **Competition**: Quick-commerce apps (Blinkit, Zepto, BigBasket, JioMart). We compete on trust, local relationships, own-brand products and convenience, not on rock-bottom prices.

### 2. Tech stack (use exactly this unless you give me a strong reason to change it)

**Backend and web app**
- **Python 3.12+** with **Django 5.x** (modular monolith, one deployable app).
- **Django REST Framework** (or Django Ninja) only where a JSON API is actually needed (e.g. cart AJAX, future mobile app); otherwise server-rendered pages.
- **PostgreSQL** database. Use Django migrations. Use `Decimal`/`DecimalField` for all money, never floats. Store money in INR.
- **Celery + Redis** for background jobs (notifications, invoice PDFs, stock sync, payment reconciliation, scheduled tasks).
- **Django admin**, customised, as the primary back-office (products, orders, stock, delivery zones, coupons). Add custom admin actions: bulk price update, CSV import/export, print pick list, print invoice.

**Frontend**
- Server-rendered Django templates + **HTMX** + **Alpine.js** for interactivity (cart updates, search-as-you-type, slot selection) with **Tailwind CSS**.
- Mobile-first, accessible, fast on slow networks (lazy-load images, small JS payload, responsive images in WebP). Target Lighthouse performance ≥ 85 on mobile.
- Language: English first, structure for **i18n so Hindi can be added** later (use Django's translation framework from the start).
- Installable **PWA** (manifest + service worker for basic caching) in a later phase.

**Integrations**
- **Payments**: **Razorpay** (UPI, cards, netbanking, wallets) with signature-verified **webhooks** as the source of truth for payment status. Also **Cash on Delivery**. Never store card data; use the gateway's hosted checkout.
- **Authentication**: **Phone number + OTP login** (primary), optional email. OTP via SMS provider `[MSG91 / Twilio / Fast2SMS]` behind an abstraction so the provider can be swapped. Rate-limit OTP requests.
- **Notifications**: WhatsApp Business Cloud API (order confirmation, out-for-delivery, delivered) and SMS fallback, behind a notification service abstraction.
- **Maps/address**: Pin-code validation against allowed delivery zones; optional Google Maps/Places autocomplete later.
- **Shipping (Phase 2, own-brand products pan-India)**: abstraction ready for **Shiprocket** or Delhivery.
- **Email**: transactional email via `[PROVIDER]` (e.g. SES/Brevo).
- **File/media storage**: S3-compatible object storage (or local in dev) with image resizing.
- **Search**: Start with PostgreSQL full-text search + trigram similarity (handles typos and Hinglish spellings reasonably). Design the search layer behind an interface so semantic/embedding search can be added later.

**Infrastructure and quality**
- **Docker + docker-compose** for local dev (web, db, redis, celery worker, celery beat).
- Environment config via environment variables (`django-environ`); never commit secrets. Provide `.env.example`.
- **Tooling**: `uv` (or `pip` + `venv`), `ruff` (lint+format), `mypy`/type hints on non-trivial code, `pytest` + `pytest-django`, `factory_boy`, pre-commit hooks.
- **CI**: GitHub Actions running lint, type check and tests on every push.
- **Deployment target**: single VPS or managed container service in India region (`[DigitalOcean Bangalore / AWS Mumbai / Azure Central India]`) with Gunicorn + Nginx, managed PostgreSQL with daily backups, HTTPS via Let's Encrypt, Sentry for error tracking, uptime monitoring.
- **Future (do not build yet, but don't block it)**: a separate **FastAPI** microservice for AI features (semantic product search, WhatsApp ordering assistant, demand forecasting). Keep business logic in service modules that can be called from an API later.

### 3. Architecture rules

- **Modular monolith** with Django apps, each with clear boundaries: `accounts`, `catalog`, `inventory`, `cart`, `orders`, `payments`, `delivery`, `notifications`, `promotions`, `core`, `adminpanel` (customisations), `storefront` (templates/views).
- Put business logic in **service modules** (`services.py`), not in views or model `save()` overrides. Views stay thin.
- **Inventory is a ledger**: every stock change is an `InventoryMovement` row (purchase received, sale, return, adjustment, damage) and current stock is derived or cached with integrity checks. Stock reservation at checkout with expiry.
- **Order lifecycle as an explicit state machine**, enforced in one place:
  `PLACED → PAYMENT_PENDING → PAID → CONFIRMED → PACKING → OUT_FOR_DELIVERY → DELIVERED`, plus `CANCELLED`, `PAYMENT_FAILED`, `REFUND_PENDING`, `REFUNDED`, `PARTIALLY_REFUNDED`. COD orders skip payment states. Log every transition with timestamp and actor.
- **Idempotency** for payment webhooks and order creation (duplicate webhooks must never double-charge, double-ship, or double-decrement stock).
- Use database transactions and `select_for_update()` wherever stock or payment state changes, to avoid race conditions (two customers buying the last item).
- Soft-delete or archive instead of hard-delete for products, customers and orders.
- Everything auditable: who changed prices, stock, and order status, and when.

### 4. Core data model (propose and refine; show the ERD before coding)

At minimum: `User`/`Customer`, `Address`, `Brand`, `Category` (hierarchical), `Product`, `ProductVariant/SKU` (pack size, unit, barcode, HSN code, GST rate, MRP, selling price), `ProductImage`, `Supplier` (optional), `InventoryMovement`, `StockReservation`, `Cart`, `CartItem`, `Order`, `OrderItem` (store price, GST and name snapshot at purchase time), `Payment`, `Refund`, `DeliveryZone` (pin codes/areas), `DeliverySlot`, `DeliveryAssignment`, `Coupon`/`Promotion`, `Invoice`, `NotificationLog`, `AuditLog`.

Product attributes must support: **flag for own-brand vs third-party**, food-specific fields (veg/non-veg symbol, **FSSAI licence number** for own-brand items, ingredients, allergens, shelf life, net weight/quantity), MRP vs selling price (show savings), unit pricing (price per 100 g / per kg / per litre), tags, stock status, `is_active`, and search keywords including Hindi/Hinglish synonyms (e.g. "atta / aata / wheat flour").

### 5. Customer-facing features (storefront)

1. **Home page**: banners, categories, "Our Own Brand" section, best sellers, "Order again", offers.
2. **Catalogue**: category and brand pages, filters (brand, price, own-brand, in-stock, diet), sorting, pagination or infinite scroll, fast search-as-you-type with typo tolerance.
3. **Product page**: images, price/MRP/savings, pack-size variants, add-to-cart stepper, description, nutrition/ingredients, FSSAI info where applicable, related products. SEO-friendly URLs and structured data (JSON-LD `Product`).
4. **Cart**: persistent for logged-in users, guest cart merged on login, live totals, delivery fee and minimum-order-value messaging, coupon entry, stock-aware limits.
5. **Checkout** (minimum friction): phone OTP login, address selection with **pin-code serviceability check**, delivery slot selection, payment method (UPI/cards/netbanking via Razorpay, or COD), order summary, GST breakdown. Guard against double submission.
6. **Order tracking**: order status timeline, live status updates, invoice download (GST-compliant PDF), cancel (where allowed), reorder in one click.
7. **Account**: addresses, order history, "buy again" list, saved items.
8. **Static/legal pages**: About, Contact (with shop address, map, phone), Shipping & Delivery, Returns & Refunds, Privacy Policy, Terms, and a footer showing **FSSAI licence number** and GSTIN.
9. **Low-bandwidth friendliness**: lightweight pages, skeleton loaders, no layout shift, works acceptably on 3G-like speeds.

### 6. Admin / back-office features (for owner and staff)

- Dashboard: today's orders, revenue, pending packing, low-stock alerts, top products.
- **Product management**: add/edit, **bulk import/export via CSV/Excel** (with validation report), bulk price and stock updates, image upload, activate/deactivate.
- **Inventory**: receive stock, adjust stock, view movement history, low-stock and expiry alerts, reconcile with physical counts.
- **Order management**: filter by status/slot/area, update status, print **pick list** (grouped by aisle/category) and **invoice/packing slip**, assign delivery person, handle substitutions ("if out of stock, replace with X / refund"), partial fulfilment, cancel and refund flows.
- **Delivery management**: zones by pin code, slot capacity, delivery fee rules, delivery-person view (mobile-friendly list of today's drops with address, phone, amount to collect for COD) and mark-delivered with COD cash reconciliation.
- **Promotions**: coupons (percentage/fixed/free delivery, min order, usage limits, validity), featured products, banners.
- **Customers**: list, order history, notes.
- **Reports**: sales by day/product/brand/category, own-brand vs third-party margin, GST summary export, COD pending/collected, stock valuation.
- Role-based permissions: Owner, Manager, Packer, Delivery staff, each with least-privilege access.

### 7. India-specific requirements

- **GST**: store HSN code and GST rate per product; compute and display GST-inclusive prices (MRP is inclusive in India); generate GST-compliant tax invoices (invoice number series, GSTIN, HSN, CGST/SGST split for intra-state Madhya Pradesh sales, IGST for inter-state in Phase 2).
- **Legal Metrology/labelling** data for own-brand products (net quantity, MRP, manufacturer details, best before) shown on product pages.
- **FSSAI** licence number visible in footer and on own-brand product pages; allow for expiry-date and batch tracking.
- **Consumer Protection (E-Commerce) Rules, 2020**: clear seller details, grievance officer contact, transparent pricing and refund/cancellation policy, no hidden charges.
- **DPDP Act (India's data protection law)**: collect only necessary data, consent for marketing messages, data export/deletion path, privacy policy.
- **Payments**: UPI-first UX; support COD with a per-order COD limit option and COD fee option.
- **Currency/locale**: INR (₹) formatting with Indian digit grouping, IST timezone, `en-IN` locale.
- Support Hindi (Devanagari) product names and addresses, and ensure fonts render correctly.

### 8. Security, reliability and quality requirements

- Follow OWASP Top 10: parameterised queries (use the ORM), CSRF protection, XSS-safe templates, secure session cookies, HTTPS-only, HSTS, security headers, content-security-policy.
- **Authorization checks on every object access** (a customer can only see their own orders and addresses).
- Rate-limit login/OTP/checkout/search endpoints. Protect against OTP abuse and bot orders.
- Verify **Razorpay webhook signatures**; treat the webhook (not the browser redirect) as the source of truth for payment status; reconcile pending payments with a periodic Celery job.
- Never log secrets, OTPs, or full payment data. Encrypt sensitive config; rotate keys.
- Validate and sanitise all uploads (type, size) and CSV imports.
- **Automated tests required** for: pricing/GST calculations, cart totals, stock reservation and race conditions, order state transitions, payment webhook idempotency, coupon rules, permission checks, delivery-zone/slot validation. Aim for high coverage on `services/` modules.
- Structured logging, Sentry integration, health-check endpoint, database backups with a documented restore procedure.
- Accessibility: semantic HTML, labels, keyboard navigation, sufficient contrast.

### 9. Build plan (work strictly in this order; stop after each phase and wait for me)

- **Phase 0, Planning**: confirm the stack, list open questions, propose the folder structure, the ERD, and the Django app boundaries. No code yet.
- **Phase 1, Project setup**: repo scaffold, Docker/docker-compose, settings split (dev/prod), env config, linting/typing/testing tooling, CI pipeline, base templates with Tailwind/HTMX, custom user model (phone-based), health check.
- **Phase 2, Catalogue and inventory**: models, migrations, admin with CSV import/export, inventory ledger, seed script with realistic sample provision-shop data (atta, rice, dal, oil, spices, tea, biscuits, own-brand items), product listing/detail pages, search and filters, tests.
- **Phase 3, Cart and checkout (COD first)**: cart logic, delivery zones/slots, address management, OTP login, order creation with stock reservation, order state machine, order confirmation, notifications (console/log backend first), tests.
- **Phase 4, Payments**: Razorpay integration in test mode, webhooks, idempotency, refunds, reconciliation job, tests.
- **Phase 5, Admin operations**: pick lists, invoices (GST PDF), delivery-staff view, promotions, reports, roles/permissions.
- **Phase 6, Notifications and polish**: WhatsApp/SMS/email providers, PWA, SEO (sitemap, meta, JSON-LD), performance tuning, Hindi i18n scaffolding.
- **Phase 7, Production readiness**: security hardening review, load testing basics, deployment scripts, backups/restore test, monitoring, runbook, soft-launch checklist.
- **Phase 8 (later)**: pan-India shipping for own-brand products (Shiprocket), subscriptions/"monthly basket", loyalty, and AI features via a separate FastAPI service (semantic search, WhatsApp ordering assistant, demand forecasting).

### 10. How I want you to work

1. Start with **Phase 0 only**. Ask any clarifying questions you need (max 10, grouped and prioritised), state your assumptions, and propose the ERD and folder structure.
2. In every phase, give me: (a) a short plan, (b) the code, (c) commands to run and test, (d) a review checklist of risky areas, (e) what comes next.
3. Prefer boring, well-documented, conventional solutions. Use official Django patterns. Avoid unnecessary dependencies and over-engineering (no microservices, no premature abstractions) but keep integration points (payments, SMS, WhatsApp, shipping, search) behind interfaces.
4. Write tests alongside code, not afterwards. Don't mark a phase complete until tests pass.
5. Flag anything security-, money- or stock-related with a clear **"REVIEW CAREFULLY"** note.
6. When you're unsure about an Indian regulatory or tax detail, say so and tell me to verify with my CA/legal advisor instead of guessing.
7. Keep a living `README.md`, `docs/architecture.md`, and `docs/decisions.md` (short ADRs for each key decision) up to date.
8. Because I'm learning Python, after each phase give me a 5-line "Python/Django concepts used in this phase" recap.
9. Before every slice, list the open questions in `docs/open_questions.md` that are due (their 'ask again before' point is that slice or earlier) and ask them first, with the default in use. Low-priority items are asked only at their point.
10. Let Freebuff do context-heavy coding. Every prompt names its slice (for example 'Slice 21b'), tells the agent to read `docs/AGENT_BRIEF.md` first, covers one file and ends with STOP. Give a kickoff block at the start of each slice so a new Freebuff chat can begin.

**Definition of done for the MVP**: A customer in Indore can browse the catalogue on a phone, log in with OTP, add products to the cart, choose an address inside the delivery zone and a delivery slot, pay via UPI/card or choose COD, receive a confirmation, and track the order. The shop staff can see the order in the admin, print a pick list, mark it packed and out for delivery, record delivery and COD collection, and issue a GST invoice and refund, with stock updating correctly and no data loss or double-charging under duplicate requests or concurrent orders.
