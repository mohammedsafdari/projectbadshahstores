# Badshah Store — Open Questions

Live list. Each item has an owner: **you** (shop owner), **CA/legal** (your advisor), or **provider** (the third-party service). Items marked as placeholders in the docs are listed here so we can close them as data arrives.

## Shop and delivery area
- [ ] Shop pin code — [TO BE FILLED: shop pin code] — owner
- [ ] Served pin codes (delivery zones) — [TO BE FILLED: served pin codes] — owner
- [ ] Shop full address (Saifee Nagar, Indore), phone, email — [TO BE FILLED] — owner
- [ ] Delivery origin address used for pin-code-based serviceability — owner

## Tax and compliance
- [ ] GST registration type: regular vs composition — CA/legal
- [ ] GSTIN — [TO BE FILLED: GSTIN] — owner/CA
- [ ] Default own-brand packaged-food GST rate — recorded assumption is 5%; confirm with CA — CA
- [ ] HSN codes and GST rates per product/variant — to be filled per variant at data entry; confirm mapping with CA — CA/owner
- [ ] Rounding rule for CGST/SGST and invoice totals — needed by Phase 5 (invoices); default in use: each amount rounded half-up to paise, SGST is the remainder of CGST (Slice 13c) — CA/legal
- [ ] HSN code format — variants accept 4 to 8 digits (Slice 13a); confirm with CA — CA/legal
- [ ] Invoice number prefix and 16-character GST rule — recorded assumption `INV/...`; confirm with CA — CA
- [ ] FSSAI licence number — [TO BE FILLED: FSSAI number] — owner/CA
- [ ] FSSAI licence expiry — [TO BE FILLED] — owner/CA
- [ ] Grievance officer name and contact — [TO BE FILLED] — owner/legal
- [ ] Legal Metrology manufacturer details for own-brand variants — to be captured per variant at data entry — owner

## Payments and notifications
- [ ] Razorpay test-mode keys (and later live keys) — provider — owner when ready; we default to test mode only until told otherwise
- [ ] COD policy: cap ₹2,000 at checkout only (recorded); revisit if policy changes — owner
- [ ] SMS/WhatsApp provider choice finalized — recorded default MSG91 behind abstraction, console backend in dev — owner; MSG91 needs DLT registration first
- [ ] DLT registration for SMS/WhatsApp templates — provider/regulator — owner
- [ ] Email provider credentials — recorded default Brevo, console backend in dev — owner when ready

## Catalog and inventory
- [ ] Which products are sold loose by weight (dal, rice, sugar), in kg or litres, and in what smallest step (suggested 0.100)? — owner; needed before Slice 22 (seed data); default in use: nothing is loose until `is_sold_loose` is set per variant (D42)
- [ ] POS export format for stock/price sync — unknown; planned as one-way CSV import with validation report, plus CSV stock-count adjustment flow — owner (from your billing software); a sample export of about 10 rows is needed by Slice 20 (CSV import); default in use: the website ledger is primary and the billing export is a one-way import
- [ ] Product/variant seed data: categories, brands, own-brand products at launch — owner/phase-2 data entry
- [ ] Own-brand products: which products are own-brand, and are they FSSAI-licensed — owner/CA
- [ ] Per-variant HSN/GST rate values — entered at data entry; confirm mapping with CA — CA/owner
- [ ] Can we use manufacturer or brand photos for third-party products, or must we shoot our own? — owner (brand permission, copyright); needed before Slice 22 (seed data) and launch; default in use: the owner uploads their own photos
- [ ] Who takes the own-brand product photos, and should every product have a label photo (net quantity, MRP, FSSAI number) for Legal Metrology? — owner/CA; needed before launch; default in use: no label photo required
- [ ] Is a 2 MB limit, 8000 px longest side and JPG/PNG/WebP enough for the staff's phone cameras, and how many photos per product? — owner; needed before Slice 22; default in use: those limits, constants in `apps/catalog/validators.py`
- [ ] Photo files are not deleted from disk when an image row or a product is deleted — developer; a clean-up job is needed before production; default in use: files are left in place
- [ ] When to add resized and WebP versions of photos — developer; planned for the storefront slices (23 and 25) or Phase 6; default in use: originals only
- [ ] Production hardening of the stock ledger: a Postgres trigger or a restricted database role so movement rows cannot be changed with raw SQL — developer; before production; default in use: the Django-level guard only (D42)
- [ ] Purchase cost per received lot, for stock valuation — owner/CA; needed by Phase 5 reports; default in use: `ProductVariant.cost_price`
- [ ] Is repacking (a loose sack into packets) frequent enough to need a `REPACK` movement type? — owner; default in use: two `ADJUSTMENT` movements with a reason
- [ ] Should a purchase with an expiry date in the past be refused or only warned about? — owner; default in use: allowed, no warning; the Receive page accepts a past date (D43, D45)
- [ ] Should every damage or write-off entry need a reason (expired, broken, lost)? — owner; default in use: yes, the reason is required (D43)
- [ ] Who records in-store sales in the ledger (`IN_STORE_SALE`): the billing-software import in Slice 20, or nothing yet? — owner; needed before Slice 20; default in use: nothing writes `IN_STORE_SALE` yet (D43)
- [ ] Audit log retention: how long to keep rows. Default: forever. Owner: shop owner. Needed before production.
- [ ] Slices 20 and 21 must write an audit entry for every price change they make: `snapshot_variant_from_db` before and `record_variant_change` after (`apps/catalog/audit.py`, D44). Default: yes (a reminder, not a business question).
- [ ] Should creating or deleting a variant, or changing a product, brand or category, also be audited? — owner/developer; default in use: no, only edits of existing variants (D44)
- [ ] Which staff roles may read the audit log? — owner; needed when the staff roles exist; default in use: superusers only (D44)
- [ ] Which staff may receive, adjust, damage and count stock before the Phase 5 roles exist? — owner; default in use: users with the Stock view permission and `inventory.add_inventorymovement`, and superusers (D45)
- [ ] How many days ahead should the expiry screen warn? — owner; default in use: filters for 7, 30 and 60 days plus "already expired" (D45)
- [ ] Should low stock or near expiry also send a message (WhatsApp, SMS or email)? — owner; default in use: screens only, notifications wait for Phase 6 (D45)
- [ ] Should the Adjust page refuse to take stock below zero? — owner/developer; default in use: no, `adjust_stock` allows it and logs a warning (D43); Damage cannot go below zero
- [ ] Do you need to know how much of each received lot is left (batch-level stock)? — owner; default in use: no, the Expiring lots screen shows the variant's total (D42, D45)

## Accounts and access
- [ ] Initial staff accounts and role split (owner/manager/packer/delivery) — owner; MVP supports it, not necessarily used at launch
- [ ] Staff 2FA before launch — recommended, owner; not built in Phase 1
- [ ] Real provider accounts wait until the local MVP works — owner; Razorpay, MSG91, Brevo

## Infrastructure and deployment
- [ ] Deployment target region — recorded options: DigitalOcean Bangalore / AWS Mumbai / Azure Central India — owner
- [ ] S3-compatible storage provider for production media — owner
- [ ] Production domain and HTTPS — owner/deployment

## Deferred (not blockers, tracked for later)
- [ ] PWA — deferred
- [ ] Full Hindi i18n content — scaffolding now, content later
- [ ] Pan-India shipping for own-brand products — later business phase (Phase 8 in PROJECT_INSTRUCTIONS.md, not the current catalogue and inventory phase)
- [ ] Subscriptions / monthly basket — deferred
- [ ] Loyalty program — deferred
- [ ] FastAPI AI service (semantic search, WhatsApp assistant, demand forecasting) — deferred
- [ ] Tailwind build step before launch — deferred debt, recorded in decisions

## How to use this file
- Items with `[TO BE FILLED: ...]` are placeholders in the docs, not guesses.
- New items: name the owner, when it is needed (slice or phase) and the default in use meanwhile.
- Do not invent pin codes, GSTIN, FSSAI, HSN codes, GST rates, or invoice prefix rules here.
- When an item is filled, update the relevant doc and remove it from this list.

## Defaults chosen while waiting
Revisit these if the shop says otherwise.
- Slugs are plain ASCII (for example chana-dal). Hindi text goes in name fields.
- Diet type defaults to "Not applicable"; food items must be set to veg or non-veg.
- Variants have a numeric pack size next to the unit, used for unit pricing.
- A barcode is optional, but unique when set.
- Savings percent is rounded down, so a discount is never overstated.
- Unit price is shown per kg, per 100 g, per litre, per 100 ml, per piece or per packet.
- Expiry tracking is per received lot only: the expiry view can say a lot is near expiry while the variant still has stock, but not how many units of that lot remain (D42).
- A low-stock alert exists only when a variant has a `low_stock_threshold`; a blank threshold means no alert.
- Cart, order and reservation quantities (Phase 3) will be decimals too, so loose items can be sold (D42).
