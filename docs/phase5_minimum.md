# What the soft launch needs (COD plus credit)

A checklist for the smallest Phase 5 that can run the shop every day with cash on delivery and
credit (khata), and for the owner tasks that take the longest to arrange. Nothing here is built
yet unless it says so. Tick an item only when it works in production and someone has tried it.

## 1. Software, minimal Phase 5

- [ ] Staff order list: filters by status, delivery day and payment method; each row shows the
      customer, the amount, the payment method and the verification state.
- [ ] The order list shows the delivery gate refusal for an order that cannot go out yet, with
      the reason (unverified phone or email), so staff know what to ask the customer.
- [ ] Search the order list by order number or by phone.
- [ ] Pick list per delivery day: one printable or on-screen list of the items to pick, grouped
      by order, with quantities and pack labels.
- [ ] Packing a picked order consumes its stock reservations and writes the `SALE`
      `InventoryMovement` for each line (the Phase 4 writer the reservation code waits for).
- [ ] Move an order to out for delivery from the staff screen, with the gate error shown in plain
      words instead of a crash when the customer is not verified.
- [ ] Move an order to delivered from the staff screen.
- [ ] Record COD cash collected: the amount, who collected it and when; one daily reconciliation
      list that adds up the collected orders and shows any order still unpaid.
- [ ] Staff cancel with a reason on screen (the `cancel_order_by_customer` rules and the audit
      path exist; the staff screen is missing).
- [ ] Basic roles (owner, staff, delivery) with per-screen permissions, so a delivery person sees
      only the delivery list and a packer cannot read the ledger or the cost prices.
- [ ] Credit payments received entered by staff, through `record_payment`, with the customer and
      the amount visible before saving.
- [ ] The verification state is visible while staff create or review a credit account, so credit
      is never given to an unverified customer by accident.

## 2. Owner tasks that cost lead time

- [ ] MSG91 account opened and DLT registration done: entity, header and the seven message texts
      (the six order message texts named in D69 plus the OTP text).
- [ ] A real SMTP provider and a sender address for order and verification emails, with the
      domain records (SPF, DKIM) set by the provider.
- [ ] The shop WhatsApp number entered in `ShopSettings.whatsapp_number` (blank hides the button
      on the order page today).
- [ ] The physical stock count finished, so the website ledger starts from real quantities.
- [ ] Final product data: the real product list, pack sizes, MRP, HSN and GST rates, veg marks
      and photos, with the owner's answers to the open questions.

## 3. Phase 7 readiness

- [ ] Hosting chosen and paid for, with the production database and media storage in place (D76: one paid India-region VPS with PostgreSQL in Compose and media in Cloudflare R2).
- [ ] Domain and HTTPS working on the production host.
- [ ] Backups running on a schedule, and one restore test done from a backup.
- [ ] Monitoring and error alerts: the site, the database, the disk and failed background jobs.
- [ ] Privacy policy and terms pages published, with the grievance officer details.
- [ ] A data export the owner can run (orders, customers, credit ledger) without a developer.
- [ ] The checklist for the day of go-live written and rehearsed: DNS, migrate, seed, staff
      accounts, the first test order.
- [ ] A rollback plan written: how to return to the previous release and the previous database
      backup, and who decides.

## 4. Not needed for the soft launch

- [ ] Online payment (Razorpay) — Phase 4, in test mode only, and not a soft-launch dependency; the live provider is chosen at KYC time (D76).
- [ ] WhatsApp API messages — plain SMS and email are enough at the start.
- [ ] Per-IP rate limits — the per-phone limits stay; revisit after real traffic.
- [ ] Email login or Google login — phone and password are enough.
- [ ] Profile editing of the name or phone — the owner can change them in the admin.
- [ ] A reorder button — customers can add items to the cart again by hand.
- [ ] Invoices — the billing software at the counter keeps issuing them for now.
