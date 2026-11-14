# Badshah Store — Open Questions

Live list. Each item has an owner: **you** (shop owner), **CA/legal** (your advisor), or **provider** (the third-party service). Items marked as placeholders in the docs are listed here so we can close them as data arrives.

## Shop and delivery area
- [ ] Shop full address (Saifee Nagar, Indore), phone, email — [TO BE FILLED] — owner
- [ ] Shop WhatsApp number for the order page button (`ShopSettings.whatsapp_number`) — owner; ask again before launch; default in use: blank, the button is hidden
- [ ] Delivery origin address used for pin-code-based serviceability — owner

## Tax and compliance
- [ ] GST registration type: regular vs composition — CA/legal
- [ ] GSTIN — [TO BE FILLED: GSTIN] — owner/CA
- [ ] Default own-brand packaged-food GST rate — recorded assumption is 5%; confirm with CA — CA
- [ ] HSN codes and GST rates per product/variant — to be filled per variant at data entry; confirm mapping with CA — CA/owner
- [x] Rounding rule for CGST/SGST and invoice totals — answered (11 November 2026, at the invoice slice): all defaults, so every amount stays rounded half-up to paise and SGST is the remainder of CGST (Slice 13c); the invoice uses `gst_breakdown` unchanged (D80) — CA/legal
- [x] HSN code format — answered (11 November 2026): all defaults, so variants keep accepting 4 to 8 digits and the invoice prints the code exactly as stored (D80) — CA/legal
- [x] Invoice number prefix and 16-character GST rule — answered (11 November 2026): all defaults, so the series is `INV/26-27/000001`, the limit is 16 characters and a longer number refuses the send-out (D14, D80) — CA
- [ ] FSSAI licence number — [TO BE FILLED: FSSAI number] — owner/CA
- [ ] FSSAI licence expiry — [TO BE FILLED] — owner/CA
- [ ] Grievance officer name and contact — [TO BE FILLED] — owner/legal
- [ ] Legal Metrology manufacturer details for own-brand variants — to be captured per variant at data entry — owner

## Payments and notifications
- [ ] Razorpay test-mode keys — provider — owner when ready; test mode needs no activation, and the live account stays inactive until close to launch because the 0% platform-fee window starts at activation (D76)
- [ ] Live payment gateway: Razorpay or Cashfree, chosen at KYC time, with Cashfree's AMC in writing if it is chosen (third parties report ₹4,999 a year) — owner/provider; ask again before the Phase 4 wrap-up; default in use: Razorpay stays for Phase 4 in test mode (D76)
- [ ] Bank confirmation that the shop's UPI QR counts as a small-merchant QR, so receipts up to ₹1 lakh a month stay at zero MDR — owner/bank; ask again before launch (Phase 7); default in use: not confirmed (D76)
- [ ] COD policy: cap ₹2,000 at checkout only (recorded); revisit if policy changes — owner
- [x] SMS/WhatsApp provider choice — decided (D76, 10 November 2026): SMS is MSG91 behind the abstraction, with DLT registration first; WhatsApp is only the free `wa.me` button in the MVP, with no WhatsApp Business API — owner
- [ ] DLT registration for SMS/WhatsApp templates — provider/regulator — owner
- [ ] MSG91 DLT entity fee quote (a few thousand rupees, unverified) plus the entity, header and template registration — owner/provider; ask again before Phase 7; default in use: the fee is unverified (D76)
- [ ] Email provider credentials — Brevo free, 300 emails a day shared with transactional email, with SPF and DKIM on the domain — owner when ready (D76)
- [ ] Cancellation fee percent kept from a customer-cancelled online order (`ShopSettings.refund_fee_percent`, copied to each order) — owner; ask again before launch; default in use: 0%, so the whole payment goes back (D78)
- [ ] Does the live gateway report refunds and settlements through the same API the test mode does, and is a settlement report available? — provider; ask again at KYC time; default in use: refunds are checked through the provider's refund API only, never a bank statement (D78)

## Catalog and inventory
- [ ] Which variants are sold loose by weight (pulses, dry fruits, masalas), and in what smallest step? The owner will send the exact list later and staff will mark the variants in the admin (Slice 29c) — owner; ask again before production data entry; default in use: nothing is loose until `is_sold_loose` is set per variant (D42, D55)
- [ ] POS export format for stock/price sync — unknown; planned as one-way CSV import with validation report, plus CSV stock-count adjustment flow — owner (from your billing software); a sample export of about 10 rows is needed by Slice 20 (CSV import); default in use: the website ledger is primary and the billing export is a one-way import
- [ ] Product/variant seed data: categories, brands, own-brand products at launch — owner/phase-2 data entry
- [ ] Please give the FSSAI licence number; it goes into the `SHOP_FSSAI_LICENCE` setting and must be set in production before launch — owner; ask again before Phase 7; default in use: not shown
- [ ] Should the filter panel show how many products each brand has, and offer non-vegetarian and 'not applicable' diet choices once the real catalogue has them? — owner; low priority; ask again before launch (Phase 7); default in use: no counts, Vegetarian only (D51) (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Own-brand products: which products are own-brand, and are they FSSAI-licensed — owner/CA
- [ ] Per-variant HSN/GST rate values — entered at data entry; confirm mapping with CA — CA/owner
- [ ] Can we use manufacturer or brand photos for third-party products, or must we shoot our own? — owner (brand permission, copyright); needed before launch; default in use: the owner uploads their own photos
- [ ] Who takes the own-brand product photos, and should every product have a label photo (net quantity, MRP, FSSAI number) for Legal Metrology? — owner/CA; needed before launch; default in use: no label photo required
- [ ] Is a 2 MB limit, 8000 px longest side and JPG/PNG/WebP enough for the staff's phone cameras, and how many photos per product? — owner; needed before launch; default in use: those limits, constants in `apps/catalog/validators.py`
- [ ] Photo files are not deleted from disk when an image row or a product is deleted — developer; a clean-up job is needed before production; default in use: files are left in place
- [ ] When to add resized and WebP versions of photos — developer; planned for the storefront slices (23 and 25) or Phase 6; default in use: originals only
- [ ] Production hardening of the stock ledger: a Postgres trigger or a restricted database role so movement rows cannot be changed with raw SQL — developer; before production; default in use: the Django-level guard only (D42)
- [x] Purchase cost per received lot, for stock valuation — answered (11 November 2026): all defaults, so `ProductVariant.cost_price` stays the one cost figure and no invoice prints a cost — owner/CA
- [ ] Should a purchase with an expiry date in the past be refused or only warned about? — owner; default in use: allowed, no warning; the Receive page accepts a past date (D43, D45)
- [ ] Should every damage or write-off entry need a reason (expired, broken, lost)? — owner; default in use: yes, the reason is required (D43)
- [ ] Who records in-store sales in the ledger (`IN_STORE_SALE`): the billing-software import in Slice 20, or nothing yet? — owner; needed before Slice 20; default in use: nothing writes `IN_STORE_SALE` yet (D43)
- [ ] Audit log retention: how long to keep rows. Default: forever. Owner: shop owner. Needed before production.
- [ ] Slice 21 must write an audit entry for every price change it makes: `snapshot_variant_from_db` before and `record_variant_change` after (`apps/catalog/audit.py`, D44); Slice 20 does this for the import. Default: yes (a reminder, not a business question).
- [ ] Should creating or deleting a variant, or changing a product, brand or category, also be audited? — owner/developer; default in use: no, only edits of existing variants (D44)
- [x] Which staff roles may read the audit log? — answered (D75, 9 November 2026): the `AUDIT_READ` capability decides, with owner and manager allowed by default; change it in the role capability matrix — owner
- [x] Which staff may receive, adjust, damage and count stock before the Phase 5 roles exist? — answered (D75, 9 November 2026): the `STOCK_WRITE` capability alone decides, with owner and manager allowed by default; change it in the role capability matrix — owner
- [x] Should a capability replace the old Django permission on the Stock, audit and credit screens, or add to it? — answered (D75 amendment, 9 November 2026): it replaces it; only the Stock list also opens for Django's Stock view permission, and the CSV export is unchanged — owner
- [ ] How many days ahead should the expiry screen warn? — owner; default in use: filters for 7, 30 and 60 days plus "already expired" (D45)
- [ ] Should low stock or near expiry also send a message (WhatsApp, SMS or email)? — owner; default in use: screens only, notifications wait for Phase 6 (D45)
- [ ] Should the Adjust page refuse to take stock below zero? — owner/developer; default in use: no, `adjust_stock` allows it and logs a warning (D43); Damage cannot go below zero
- [ ] Do you need to know how much of each received lot is left (batch-level stock)? — owner; default in use: no, the Expiring lots screen shows the variant's total (D42, D45)
- [ ] Should the CSV export hide `cost_price` from some staff (for example packers) or from everyone except the owner? — owner; ask again before launch (Phase 7); default in use: the `EXPORT_CSV` capability decides who may export (owner and manager by default, D75), and the file still includes `cost_price` (D46)
- [ ] Should every CSV export be logged (who downloaded the catalogue, when)? — owner/developer; default in use: no (D46)
- [ ] Would an `.xlsx` export be better than CSV for the staff (Excel mangles barcodes and HSN codes in CSV)? — owner; default in use: CSV only, import will validate barcodes and HSN codes (D46)
- [ ] Do you want a separate stock-level export (on-hand per SKU) for the billing software or stock counts? — owner; needed by Slice 20; default in use: no stock column in the catalogue export (D46)

### Added in Slice 20a (CSV import and the billing-software sample)
Each item says when to ask again. At the start of every slice, Claude lists the items whose "ask again before" point is that slice (or earlier) and asks them first.
- [x] Who is the stock master while the shop sells in store and online? — answered (D55, 3 Nov 2026; the route and the export confirmed in D63, 6 Nov 2026): the billing software is the main stock source, the website is refreshed by regular imports and the physical count happens at the production stage — owner
- [ ] Do we need partial files (for example only sku_code and prices)? — owner; low priority; ask again at Slice 21; default in use: every column of the export must be present (D47)
- [ ] Should the importer refuse HSN codes with 5 or 7 digits (HSN codes have 2, 4, 6 or 8 digits; Excel drops a leading zero)? — CA/legal; ask again before launch (Phase 7); default in use: yes, refused, to catch a dropped zero (D47)
- [x] Should the online store continue the billing software's invoice series (for example `A006693`) or use its own prefix? — answered (11 November 2026): all defaults, so the website keeps its own `INV/...` series (D80) — CA/legal
- [x] The billing software's printed GST differs from straight rounding by a paisa on some lines (for example Phenyle 30.00 at 5% prints 28.58 taxable; the invoice GST total is 92.20, our rule gives 92.22) — answered (11 November 2026): all defaults, so our half-up rule stands and the website's invoice will differ by a paisa from the billing software (D80) — CA/legal

### Added in Slice 22 (seed catalogue, D49)
- [x] GST conflict in the billing item master: the tax columns say 5% on every row, the HSN text carries 5, 12 or 18 — answered (11 November 2026): all defaults, so the seed keeps 5% and the invoice prints whatever rate the variant carries; no rate is built into the code (D80) — CA/owner
- [ ] 15 billing rows are not loaded (2 with MRP 0, 12 with selling rate above MRP, 1 with a 5-digit HSN); they are listed in `apps/catalog/seed_data/nestle_review.csv`. Please read the real MRP and HSN from the packs — owner; ask again before launch (Phase 7); default in use: not loaded (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Export of the rest of the shop (staples, spices, tea, own-brand, about 45 items) in the same billing format — owner; ask again before launch (Phase 7); default in use: `demo.csv` with `DEMO-` SKUs and provisional HSN/GST, loaded only with `--demo` (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] The rest of the category tree beyond the five starter categories (chocolates-confectionery, noodles-soups, milk-dairy, tea-coffee, spices-masalas); slugs become URLs — owner; ask again before launch (Phase 7); default in use: the five starter categories (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Pack weights in grams for KitKat, Munch and Milkybar rows (the billing names carry price points such as "10/"), so unit prices and grouping work — owner; low priority; ask again before launch (Phase 7); default in use: unit PACKET, pack size 1 (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Are the two 1 kg Everyday milk powder rows (03302 at 600 and 03304 at 500) the same pack? — owner; low priority; ask again before launch (Phase 7); default in use: 03302 is not loaded (MRP problem and bad HSN) (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [x] How to enter real purchase rates (cost price) without committing them to the public repository (a private CSV import) — answered (D85, 14 November 2026): a staff-only page at `/staff/reports/cost-prices/` behind `MANAGE_COST_PRICES` takes a `sku_code`,`cost_price` CSV, shows the plan, then writes only `ProductVariant.cost_price` with one audit row per change; `*.private.csv` and `private/` are git-ignored and `cost_price` stays blank in every committed seed file — owner/developer
- [ ] Billing stock is unusable (45 of 55 rows negative, none positive). A physical count is needed before launch to start the ledger — owner; ask again before Phase 3; default in use: the website ledger starts from received stock and counts (D43)
- [ ] Check diet type (veg mark) on the packs of all 33 real products — owner; low priority; ask again before launch (Phase 7); default in use: VEG on every real row (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Product names with price points ("KitKat ₹10 pack") go stale when an MRP changes; decide on a naming rule once pack weights are known — owner; low priority; ask again before launch (Phase 7); default in use: price point in the name (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)

### Added in Slice 26 (search, D53)
- [ ] Who fills `search_keywords` with Hinglish spellings (for example "aata" for atta) for the real products? — owner; low priority; ask again before launch (Phase 7); default in use: blank except the demo rows (asked at Slice 28: the owner chose the default; confirmed at the start of Slice 38)
- [ ] Should we log the search words customers type, so a missing product becomes visible to the owner? — owner/developer; ask again before Phase 6; default in use: no logging (D53)
  - Slice 27 default: suggestions are not logged either

### Added in Slice 29a (Phase 3 answers, D55)
- [ ] Which billing-software features does the shop use every day (counter billing, purchases, supplier dues, khata for walk-ins, GST return data), and when should an in-store POS replace it? — owner; ask again after Phase 7; default in use: the billing software stays at the counter (D55 amendment)

### Added in Slice 30
- [ ] OTP policy numbers: codes valid for 5 minutes, 5 wrong tries per challenge, 30 seconds between requests and 5 requests per phone per rolling hour — owner; ask again before launch; default in use: those numbers in `apps/accounts/otp.py` (D59)
- [ ] Per-IP rate limit and SMS-abuse protection before a real SMS provider is connected — owner/developer; ask again before real SMS; default in use: none, only the per-phone limits (D59)
- [ ] MSG91 account and DLT registration for the login SMS template — owner/provider; ask again before launch; default in use: the console SMS backend (D59)

### Added in Slice 31
- [ ] Per-line caps: 99 packets and 50 loose units per cart line — owner; low priority; ask again before launch; default in use: `MAX_PACKETS_PER_LINE = 99` and `MAX_LOOSE_PER_LINE = Decimal("50")` in `apps/cart/rules.py` (D60)
- [ ] Guest cart lifetime: how long a guest cart should last before it is forgotten — owner; low priority; ask again before launch; default in use: Django's 14-day session age, unchanged (D60)
- [ ] Deleting saved carts that nobody has touched for a long time — owner; low priority; ask again before launch; default in use: never delete; a clean-up job is needed before production (D60)

### Added in Slice 33
- [ ] Does the shop deliver on its weekly off day and on holidays? — owner; ask again before launch; default in use: every day delivers, with no holiday or weekly-off rule (D62)
- [x] Should the pin-code check also appear on the product page? — answered: no, cart page only (owner chose the default on 7 November 2026).

### Added in Slice 35
- [ ] Choose the order-number prefix the shop wants on the website (for example `BS`), set in `ShopSettings.order_number_prefix`; a blank prefix uses `BS` — owner; ask again before launch; default in use: `BS/26-27/000001`, with a new counter each financial year (D64)

### Added in Slice 37a
- [ ] Which customers get credit and at what limit? — owner; before launch; default in use: no customer has credit until the owner sets a limit in the admin
- [x] Should customers get payment reminders or a dues list? — answered (D79 amendment, 11 November 2026): staff see a dues list sorted by the largest balance and can send a manual reminder; there are no automatic reminders — owner

### Added in Slice 37b
- [x] Should a credit customer be able to pay part of an order on credit and the rest in cash? — answered (D74, 9 November 2026): yes. The order uses as much available credit as it needs, and the rest is cash on delivery; the customer ticks one khata checkbox and cannot type an amount — owner

### Added in Slice 38
- [ ] DLT templates for the six order message texts (D69) — owner; ask again before real SMS; default in use: the console SMS backend
- [ ] Real SMTP provider and sender address for order emails — owner; ask again before launch; default in use: console email in development, off in production
- [x] Should the customer get the cancel message when staff cancel an order? — answered (Slice 42a, 11 November 2026): yes, built in 42a (D69); default in use: yes (D69) — owner

### Added in Slice 38e
- [x] How staff and superusers may use the shop login (OTP and/or password) while /admin keeps its own separate sign-in — answered (D72, 8 November 2026): separate accounts; a staff phone gets its own customer row and /admin keeps the staff-only natural key — owner
- [ ] Real SMTP provider and sender address for verification emails (D71) — owner; ask again before launch; default in use: console email in development

### Added in Slice 38f
- [ ] Per-IP limit and bot protection for sign-up and login — owner; ask again before launch; default in use: none

### Added in Slice 38g
- [x] Orders and verification state for staff in Phase 5 (see docs/phase5_minimum.md) — answered (Slice 42b, 11 November 2026): the staff order list, pick list, detail, delivery list and cash page exist, and the credit admin shows the verification state — owner

### Added in Slice 42a (staff order actions, D79)
- [x] Should the send-out need both `SEND_OUT` and `START_PACKING`? — answered (owner chose the default, 11 November 2026): `SEND_OUT` alone, because a packer holds both by default (D79) — owner
- [x] Should the unpaid list of the daily cash reconciliation be limited to one delivery day, or list every order that still owes cash? — answered (owner chose the default, 11 November 2026): every order that still owes cash, listed until it is collected or cancelled (D79) — owner
- [x] Should the reconciliation compare the day's cash with the money in the till, or is the grand total enough? — answered (owner chose the default, 11 November 2026): the grand total only, with no "handed over" step (D79) — owner
- [x] Should a delivery person be able to open the order list and the cash reconciliation? The `DELIVERY` role has `DELIVER_COD` only, so today they cannot — answered (Slice 42b, 11 November 2026): the cash page opens with `DELIVER_COD`; the order list still needs `VIEW_ORDERS`, so a delivery person cannot open it — owner
- [x] Should the cash collected at the door be read as a note (a slip of paper per order) instead of typed? — answered (owner chose the default, 11 November 2026): typed on the screen (D79) — owner

### Added in Slice 42b (staff screens, D79 amendment)
- [ ] DLT template text for the credit reminder (`EVENT_CREDIT_REMINDER`): "Badshah Stores: Your credit account has Rs <amount> pending. Please pay at the shop." (`<amount>` is the balance with two decimals) — owner; ask again before real SMS; default in use: the console SMS backend
- [ ] Should the credit reminder also go by email when the customer has an address? — owner; ask again before real SMS; default in use: SMS only
- [ ] Should "Send refund now" be limited to the owner and manager, or is `REFUND_PAYMENTS` enough? — owner; ask again before the staff screens go live; default in use: `REFUND_PAYMENTS` decides (manager and owner by default)
- [ ] Should the by-hand refund note be a fixed list of reasons (cash at the counter, UPI transfer, other) instead of free text? — owner; ask again before the staff screens go live; default in use: free text of up to 200 characters, required
- [ ] Should a refund marked by hand send the customer the processed-refund message? — owner; ask again before the staff screens go live; default in use: yes, the existing `EVENT_REFUND_PROCESSED` message
- [ ] Should the dues page list a customer whose balance has just reached zero? — owner; low priority; ask again before launch; default in use: only a positive balance is listed

### Added in Slice 43 (GST tax invoices, D80)
- [ ] Delivery-fee GST: which rate applies, and is the delivery charge part of the taxable value or exempt? — CA; before the first real invoice; default in use: 18.00% inside the fee, set in `ShopSettings.delivery_fee_gst_percent` with the help text "UNCONFIRMED: ask your CA"
- [ ] Is the shop a regular GST taxpayer or a composition dealer? A composition dealer issues a bill of supply, not a tax invoice, and the whole invoice page would change — CA; before the first real invoice; default in use: a regular taxpayer's tax invoice with CGST and SGST
- [ ] How should a cancelled invoice be dealt with: a marked invoice as now, or a credit note with its own number? — CA; before the first real cancellation; default in use: the invoice is marked `Cancelled` and keeps its number, and no credit note is issued
- [ ] How many HSN digits must the printed invoice show (4, 6 or 8) for each product? — CA; before the first real invoice; default in use: the code stored on the variant, 4 to 8 digits
- [ ] Bundle a Devanagari font for the invoice and packing-slip PDFs, so a Hindi product name or address prints instead of `?` — developer; before Phase 6; default in use: `pdf_text` replaces every non-ASCII character with `?`

### Added in Slice 44a (coupons, D81)
- [x] The fifteen promotion questions (the three kinds, stacking, the minimum order, the usage limits, the validity times, first-order coupons, credit and cash-on-delivery orders, released redemptions, the code format, the capability and deletion) — answered (owner chose the defaults, 12 November 2026): a percentage with an optional maximum, a fixed amount and free delivery; one coupon per order and no stacking; the minimum is checked on the item total before the discount; an optional total cap and a per-customer limit of one; `starts_at`/`ends_at` plus an active switch; a first-order coupon was wanted; coupons are allowed on credit and cash-on-delivery orders; a released redemption counts against nothing; codes are 4 to 20 capitals, digits and hyphens; `MANAGE_PROMOTIONS` for the owner and manager; a coupon is never deleted (D81) — owner
- [ ] Is the discount a trade discount, so GST is calculated on the discounted value shared across the invoice lines? — CA; before launch; default in use: the coupon lowers the item total, the invoice shares the discount across the lines by gross value, and GST is worked out on the discounted value (D81, D82)

### Added in Slice 44b-1 (coupons in the order and the invoice, D82)
- [x] Where does the customer enter a coupon code? — answered (default in use, 13 November 2026): at checkout; the cart's coupon box is Slice 44b-2, and no page passes a code to `place_order` yet — owner
- [x] What does an invalid coupon do at order time? — answered (default in use, 13 November 2026): it stops the order with the coupon's own sentence and nothing is placed — no order, no stock hold, no redemption, no number, and the cart stays — owner
- [x] How does the invoice show the discount and the GST? — answered (default in use, 13 November 2026): one discount amount per line plus one `Discount (CODE)` line in the totals, with GST on the discounted value; the CA question above stays open until launch — owner/CA
- [x] Where does staff see a coupon on an order? — answered (default in use, 13 November 2026): the staff order detail shows the code and the two discounts; the pick list does not, because it only says what to pick — owner
- [ ] A `PAYMENT_FAILED` order keeps its coupon use counted until it is cancelled (developer, check before launch); default in use: a redemption is released only when an order is cancelled before send-out, because D77 keeps a failed order's stock and allows a retry

### Added in Slice 44b-2 (the coupon box at checkout, D83)
- [x] Where is the coupon code entered, and may the customer change it? — recorded (13 November 2026): at checkout, in a box inside the one checkout form; the customer may apply a code, see the discount and remove it before ordering, and the code is a preview that `place_order` judges again under its lock — owner
- [ ] A coupon box on the cart page is not built (developer, later); default in use: the checkout page is the only place a code is entered

### Added in Slice 42c (closing the 42b test gaps)
- [ ] Should the credit payment confirm step get a server-side double-submit token? — owner; ask again before the staff screens go live; default in use: only an inline `onsubmit` disables the button, so the same fields can still be posted twice
- [ ] Should a stuck SENT refund also get an automatic check against the provider, instead of a person looking at its dashboard? — owner; ask again before the staff screens go live; default in use: a person checks the dashboard and marks the refund by hand; no automatic retry or provider call is added

### Added in Slice 39
- [ ] Should capabilities be settable per person (an override on `User`) instead of only per role? — owner; ask again before Phase 5; default in use: per role only (D75)
- [ ] Should a cash part above the COD cap be allowed when the customer agrees, or should the order always be refused? — owner; low priority; ask again before Phase 5; default in use: refused (D74)
- [ ] Should the split-order SMS name the cash amount at the door, and should the placed credit SMS show the credit part instead of the grand total? — owner; ask again before real SMS and DLT; default in use: the texts are unchanged (D74; the A8 inspect found no door-payment message uses `grand_total`, but the placed credit text does)

## Accounts and access
- [ ] Initial staff accounts and role split (owner/manager/packer/delivery) — owner; MVP supports it, not necessarily used at launch
- [ ] Staff 2FA before launch — recommended, owner; not built in Phase 1
- [ ] Real provider accounts wait until the local MVP works — owner; Razorpay, MSG91, Brevo

## Infrastructure and deployment
- [ ] Final VPS provider and size — recorded: one paid VPS in an India region, reference DigitalOcean Bangalore, 1 vCPU / 2 GB to start and 2 vCPU / 4 GB if memory is tight (D76); the provider is not final — owner; ask again before Phase 7
- [ ] Oracle Cloud Always Free pay-as-you-go status, scarce capacity and idle reclaim (CPU under about 10-20% for a week) — owner; ask again before Phase 7; default in use: staging only, never production (D76)
- [ ] Production domain and registrar, with Cloudflare DNS and HTTPS — owner/deployment; ask again before Phase 7; default in use: Cloudflare free DNS and Let's Encrypt
- [ ] Cloudflare R2 free-tier limits re-verified before relying on them — owner; ask again before Phase 7; default in use: 10 GB, 1M writes and 10M reads a month, no egress fees, not re-verified (D76)
- [ ] GST treatment of hosting and SMS invoices (input credit and the invoice details the CA needs) — CA/legal; ask the CA before Phase 7; default in use: add GST where it applies and claim nothing

## Storefront content
- [ ] Home page still says "Online store coming soon" and has the Alpine demo (owner and developer, before launch) — owner/developer; the real shop introduction, the delivery promise and a photograph are not written yet; default in use: the placeholder copy and the demo are kept exactly as they were (D84)
- [ ] Home page banners: five, hand-ordered, with an optional link; featured products: twelve, hand-ordered — owner; ask again before launch; default in use: five banners and twelve featured products, in the order staff set (D84)
- [ ] Banner image clean-up and a click count are not built (developer, later) — developer; a replaced or removed image leaves its old file on disk, and no banner counts its clicks (D84)

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

### Added in Slice 45 (reports, D85)
- [ ] Four GST points for the CA, whose answers replace the note on the GST report and in its CSV ("Unconfirmed until the CA answers: GST on a coupon discount, delivery fee GST, regular or composition dealer, cancelled invoices.") — CA/owner; ask again before launch; default in use: the note stays on the page and as the first row of the CSV
- [ ] Should the cost be frozen on the order line, so a margin report does not change when a cost price is edited later? — owner/developer; ask again before launch; default in use: no (every margin figure is at today's cost price, and the report says so, D85)
- [ ] Should a report be emailed to the owner on a schedule (daily sales, weekly margin)? — owner; ask again before launch; default in use: no, the pages and the CSV downloads only (D85)
