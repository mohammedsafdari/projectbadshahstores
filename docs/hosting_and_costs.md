# Hosting and running costs (10 November 2026)

The owner reviewed hosting and running costs on 10 November 2026. The decisions are ADR D76 in
`docs/decisions.md`. Every rupee and dollar figure here is an **estimate** unless it says otherwise.

## 1. The stack

| Component | Choice | Monthly cost | Notes |
| --- | --- | --- | --- |
| Production server | One paid VPS in an India region (reference: DigitalOcean Bangalore; provider not final) | ₹1,080 (about $12, estimate) | 1 vCPU / 2 GB to start; 2 vCPU / 4 GB at about $24 (estimate) if memory is tight. Same Docker Compose stack as dev. |
| Database | PostgreSQL 16 in Compose on the same VPS | ₹0 extra | This replaces the "managed PostgreSQL" wording. |
| Backups | Nightly encrypted `pg_dump` to Cloudflare R2 | ₹0 within the free tier | A restore test is required before go-live. |
| Media | Cloudflare R2 through the S3 API (`django-storages`) | ₹0 within the free tier | 10 GB storage, 1M writes and 10M reads a month, no egress fees (not re-verified). |
| DNS | Cloudflare free | ₹0 | |
| Domain | Registrar of the owner's choice | About ₹90 (estimate) | The registrar price is unverified. |
| Email | Brevo free | ₹0 | 300 emails a day, shared with transactional email; SPF and DKIM on the domain. |
| SMS | MSG91 with DLT registration | ₹360-840 (estimate) | 600 OTP logins plus 2 order SMS per order at about 30 orders a day. |
| WhatsApp | Free `wa.me` button only | ₹0 | No WhatsApp Business API in the MVP (D70). |
| Payment gateway | Charged only on prepaid orders | See section 3 | About 2% plus 18% GST platform fee, plus UPI MDR above ₹2,000. |
| Staging | Oracle Cloud Always Free | ₹0 | Staging only. Do not use for production until the risks in D76 item 2 are verified. |

Error tracking and monitoring (Sentry and uptime checks) are planned for Phase 7 and are **not**
included in these figures: their cost is unverified.

## 2. Monthly cost estimate

Assumptions, all estimates: about 30 orders a day, 600 OTP logins a month, 2 order SMS per order,
₹90 per dollar, and nothing else paid. Add GST where it applies.

| Plan | Monthly estimate | What it covers |
| --- | --- | --- |
| Lean | ₹450-930 | Staging-style free host (Oracle Always Free), domain, SMS; not recommended for production. |
| Recommended | ₹1,530-2,010 | Paid VPS ₹1,080, domain about ₹90, SMS ₹360-840, everything else ₹0. |

The payment gateway is extra and only on prepaid orders. At about 2% plus 18% GST, ₹50,000 of
prepaid orders costs about ₹1,180 (estimate), before any UPI MDR above ₹2,000.

One-time costs: the DLT entity fee (a few thousand rupees, unverified) and the domain registration.

## 3. Payment-fee facts (from 10 November 2026)

- **UPI MDR.** Since 15 October 2026, bank-funded UPI payments above ₹2,000 carry 0.4% MDR, capped
  at ₹300. Payments up to ₹2,000 are still zero. The old assumption "UPI is free" is wrong.
- **Gateway platform fee.** This is separate from MDR: about 2% plus 18% GST at Razorpay on most
  domestic methods.
- **Razorpay launch offer.** 0% platform fee for 90 days or ₹5 lakh, counted from live-account
  activation. Do not activate the live account until close to launch. Test mode needs no
  activation.
- **Cashfree.** 0% up to ₹20 lakh GMV until 31 March 2027 for sign-ups from 21 July 2026. Launch
  will probably be after that date, so do not plan around it. Its AMC is unclear (third parties
  report ₹4,999 a year): get the terms in writing.
- **Zero-fee path.** The shop's own delivery workers collect by UPI QR or cash at the door.
  Small-merchant QR receipts up to ₹1 lakh a month stay at zero MDR; confirm the classification
  with the shop's bank.

## 4. Code guardrails for Phase 4 onward

1. All Razorpay-specific code lives in one module behind a small provider interface.
2. Gateway fees and MDR are never hard-coded.
3. Enabled payment methods are configurable.
4. No text in the UI or the docs says UPI is free.

## 5. Verify before go-live

- [ ] Final VPS provider, region and size, with a memory test on the real stack.
- [ ] Oracle Always Free: capacity, idle-reclaim behaviour and pay-as-you-go status, before any
      use beyond staging.
- [ ] One full restore from an encrypted R2 `pg_dump`.
- [ ] R2 free-tier limits re-verified (10 GB, 1M writes, 10M reads, no egress fees).
- [ ] Live gateway chosen at KYC time; Cashfree AMC in writing if it is chosen.
- [ ] Razorpay 0% window start date confirmed against the planned launch date.
- [ ] Bank confirmation of the small-merchant UPI QR classification.
- [ ] MSG91 DLT entity fee quoted, and entity, header and templates registered.
- [ ] Domain registrar price, Cloudflare DNS and SPF/DKIM records on the real domain.
- [ ] GST treatment of hosting and SMS invoices confirmed with the CA.
- [ ] A last search of the UI and the docs for any "UPI is free" claim.
