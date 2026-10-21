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
- [ ] Does the shop sell anything loose by weight (dal, rice, sugar)? — needed before Slice 15 (inventory models); default in use: whole-number stock only (D23); changing it later touches the inventory ledger — owner
- [ ] POS export format for stock/price sync — unknown; planned as one-way CSV import with validation report, plus CSV stock-count adjustment flow — owner (from your billing software); a sample export of about 10 rows is needed by Slice 20 (CSV import); default in use: the website ledger is primary and the billing export is a one-way import
- [ ] Product/variant seed data: categories, brands, own-brand products at launch — owner/phase-2 data entry
- [ ] Own-brand products: which products are own-brand, and are they FSSAI-licensed — owner/CA
- [ ] Per-variant HSN/GST rate values — entered at data entry; confirm mapping with CA — CA/owner

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
