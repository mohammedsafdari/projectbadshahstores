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
- [ ] Slice 21 must write an audit entry for every price change it makes: `snapshot_variant_from_db` before and `record_variant_change` after (`apps/catalog/audit.py`, D44); Slice 20 does this for the import. Default: yes (a reminder, not a business question).
- [ ] Should creating or deleting a variant, or changing a product, brand or category, also be audited? — owner/developer; default in use: no, only edits of existing variants (D44)
- [ ] Which staff roles may read the audit log? — owner; needed when the staff roles exist; default in use: superusers only (D44)
- [ ] Which staff may receive, adjust, damage and count stock before the Phase 5 roles exist? — owner; default in use: users with the Stock view permission and `inventory.add_inventorymovement`, and superusers (D45)
- [ ] How many days ahead should the expiry screen warn? — owner; default in use: filters for 7, 30 and 60 days plus "already expired" (D45)
- [ ] Should low stock or near expiry also send a message (WhatsApp, SMS or email)? — owner; default in use: screens only, notifications wait for Phase 6 (D45)
- [ ] Should the Adjust page refuse to take stock below zero? — owner/developer; default in use: no, `adjust_stock` allows it and logs a warning (D43); Damage cannot go below zero
- [ ] Do you need to know how much of each received lot is left (batch-level stock)? — owner; default in use: no, the Expiring lots screen shows the variant's total (D42, D45)
- [ ] Should the CSV export hide `cost_price` from some staff (for example packers) or from everyone except the owner? — owner; needed with the Phase 5 roles; default in use: anyone with `catalog.view_productvariant` gets it (D46)
- [ ] Should every CSV export be logged (who downloaded the catalogue, when)? — owner/developer; default in use: no (D46)
- [ ] Would an `.xlsx` export be better than CSV for the staff (Excel mangles barcodes and HSN codes in CSV)? — owner; default in use: CSV only, import will validate barcodes and HSN codes (D46)
- [ ] Do you want a separate stock-level export (on-hand per SKU) for the billing software or stock counts? — owner; needed by Slice 20; default in use: no stock column in the catalogue export (D46)

### Added in Slice 20a (CSV import and the billing-software sample)
Each item says when to ask again. At the start of every slice, Claude lists the items whose "ask again before" point is that slice (or earlier) and asks them first.
- [ ] Item master export from the billing software, one row per item (code, name, barcode, HSN, GST rate, MRP, selling rate, unit), 30 to 50 rows, customer data removed — owner; ask again before Slice 20b, and at the latest before Slice 22; default in use: the importer reads only our own export format
- [ ] SKU scheme: should `sku_code` equal the billing software's item code, or be our own scheme? — owner; ask again before Slice 22; default in use: whatever is in the file is the SKU; the importer matches on it exactly
- [ ] Who is the stock master while the shop sells in store and online (billing software, website, or a manual count)? This also decides how opening stock enters the ledger (the import has no stock column) — owner; ask again before Slice 22 and again before Phase 3; default in use: the website ledger starts from received stock and counts (D43)
- [ ] Category tree: the top-level categories and their children. Slugs become storefront URLs, so changing them later breaks links — owner; ask again before Slice 22; default in use: none, the importer refuses unknown category slugs (D47)
- [ ] How are pack sizes grouped into one product (for example Sugar 1 kg and Sugar 5 kg)? The billing invoice has only free-text names — owner; ask again before Slice 22; default in use: one product per product slug in the file
- [ ] Should the importer create missing brands and categories? — developer/owner; low priority; ask again before Slice 22; default in use: no, an unknown slug is a row error (D47)
- [ ] Do we need partial files (for example only sku_code and prices)? — owner; low priority; ask again at Slice 21; default in use: every column of the export must be present (D47)
- [ ] Should the importer refuse HSN codes with 5 or 7 digits (HSN codes have 2, 4, 6 or 8 digits; Excel drops a leading zero)? — CA/legal; ask again before Phase 5; default in use: yes, refused, to catch a dropped zero (D47)
- [ ] Does the shop sell on credit to regular customers (the invoice says "Credit Tax Invoice")? Does the online store need a credit or khata option? — owner; low priority; ask again before Phase 3 (payments) and Phase 5 (invoices); default in use: no credit sales online
- [ ] Should the online store continue the billing software's invoice series (for example `A006693`) or use its own prefix? GST needs a consecutive series — CA/legal; low priority; ask again before Phase 5; default in use: own prefix `INV/...` (see the invoice prefix item above)
- [ ] Check the GST rates and HSN codes of the first 100 items before they are loaded (the sample invoice shows 0%, 5% and 18%; for example Magic Scrubber 0%, Makhana 0%, Jira 5%) — CA/owner; ask again before Slice 22; default in use: rates come from the file, no rate is built into the code
- [ ] The billing software's printed GST differs from straight rounding by a paisa on some lines (for example Phenyle 30.00 at 5% prints 28.58 taxable; the invoice GST total is 92.20, our rule gives 92.22). Which rounding does the CA accept? — CA/legal; low priority; ask again before Phase 5 (extends the rounding item above); default in use: our half-up rule (Slice 13c)
- [ ] The sample invoice bills Rawa and Maida at 250 g for 15.00 each, which looks like shop-packed portions. Is repacking frequent? — owner; low priority; ask again before Phase 3 (extends the repacking item above); default in use: two `ADJUSTMENT` movements with a reason

### Added in Slice 20f (CSV import follow-ups)
- [ ] Should the importer refuse a product whose brand or category is archived (`is_active` false)? — owner; low priority; ask again before Slice 22; default in use: accepted, an archived brand or category still matches by slug (D47)

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
- Every item that is not answered now also gets an "ask again before <slice or phase>" point. At the start of each slice, Claude scans this list and asks the owner the items that have become due (a few at a time, most costly to change first) before writing code. Items marked "low priority" are asked only at their point, never earlier.
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
