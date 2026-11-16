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
- [ ] Confirm that the live gateway reports refunds through the same API the test mode does, and
      whether a settlement report is available. Until then reconciliation asks the refund API only
      (D78), and the cancellation fee percent stays 0 (the whole payment goes back).

## 6. Phase 7 nginx cache rules

The rules below are implemented in `deploy/nginx/default.conf`, which `docker-compose.prod.yml`
copies into the nginx image (D95, and section 8 for the file list). This list stays as the statement
of what the server must do. The reason static files and media may be cached for a year is that
neither name can ever
mean new bytes: `prod.py` now uses `ManifestStaticFilesStorage`, so a static file is called
`app.<content hash>.css`, and an uploaded photo keeps a random uuid in its name, so a changed photo
is a new address.

- **Static files:** `Cache-Control: public, max-age=31536000, immutable`. This makes `collectstatic`
  part of **every** deploy, because the manifest and the hashed names must exist before the first
  request; a deploy that forgets it serves missing files.
- **Media:** the same header (`public, max-age=31536000, immutable`), for the same reason: a new
  picture is a new file name, so an old name never needs to change.
- **HTML:** never cached — `Cache-Control: no-store` from Django already, and nginx must not add a
  cache header of its own. A page depends on who is asking, so a proxy must not keep it.
- **Compression:** `gzip on` for text types (HTML, CSS, JS, JSON, XML and SVG). Brotli is optional
  and needs its own module.
- **Cloudflare:** cache by file extension (CSS, JS, the image types, fonts) and not HTML. Its
  "Cache Everything" rule must not be switched on for the site, because that would cache pages,
  which the private-page audit of Slice 49 exists to prevent.
- **The installable web app (D91):** `/sw.js`, `/manifest.webmanifest` and `/offline/` must reach
  Django and must not be cached by nginx or by Cloudflare. Both answer `Cache-Control: no-cache`
  themselves. The `.js` extension rule above would cache `/sw.js` for a year, so it needs its own
  bypass rule (`location = /sw.js` with `proxy_pass` and no cache header, or the Cloudflare
  "Bypass Cache" rule for that exact path): a worker kept for a year can never be replaced.
- **`collectstatic` carries the new files:** the five icons in `static/img/pwa/` and
  `static/js/pwa-register.js` are static files like any other, so the deploy step of every release
  must copy them, and the hashed names mean the manifest and the page must be rebuilt together.

## 7. The CSS build in a deploy

The stylesheet every page links is built, never committed (D93). `static/css/app.css` comes from
`static/src/tailwind.css` and `tailwind.config.js` through the pinned standalone Tailwind CLI, and
production uses `ManifestStaticFilesStorage`, so the file must exist before `collectstatic` reads
it: the hash in the name is taken from the built bytes.

The order of every deploy is therefore:

1. **Build the CSS:** `tailwindcss -c tailwind.config.js -i static/src/tailwind.css -o static/css/app.css --minify`
   (inside the image, which carries the CLI; Slice 53 writes the production image and the script).
2. **`collectstatic`:** copies `static/css/app.css` and everything else into `STATIC_ROOT` and writes
   the manifest with the hashed names, so `app.<hash>.css` is the name the page asks for.
3. **Restart:** the running containers pick up the new files and the new manifest.

A deploy that skips step 1 fails at the first page render rather than serving an old stylesheet: the
manifest storage cannot find a file to hash for a name that was never built, and every page links
that one name. A deploy that skips step 2 serves 404s for every static file. Neither is recoverable
by a browser cache, so both belong in the deploy script itself and not in a checklist.

`deploy/deploy.sh` does exactly this order (D95): it builds the images, and the production
Dockerfile builds the stylesheet and then runs `collectstatic` inside the image, so the two steps
are part of the build and not of a person's memory.

## 8. The production stack files

The code half of Phase 7 is `prod.py` (D94); these are the files that run it (D95). None of them
has been executed yet: the first real build is the owner's command in the slice report, and the
first real deploy is on staging in Slice 55.

| File | What it does | Who edits it |
| --- | --- | --- |
| `docker/Dockerfile.prod` | The production image: gunicorn in the `app` stage, and the static files in the `nginx` stage. Builds the CSS and runs `collectstatic` inside the image. | developer, with a slice |
| `docker-compose.prod.yml` | The stack: `db`, `redis`, `web`, `worker`, `beat`, `nginx`. Only nginx publishes ports. Project name `badshahstore-prod`, no `container_name`. | developer, with a slice |
| `deploy/nginx/default.conf` | The vhost: port 80 health plus redirect, port 443 with TLS, the static and media cache rules, the three never-cached PWA paths, and the proxy headers. | developer, with a slice |
| `deploy/deploy.sh` | One deploy, in one order: build, migrate, check, up, wait for `web`, reload nginx, health, prune, then the two rollback markers. Rollback is the same script with the saved commit. | developer, with a slice |
| `deploy/production.env.example` | Every variable the settings read, one comment line each, with placeholder values only. Copy it to `.env` on the server. | owner (values), developer (names) |
| `.env` | The server's real values, including every secret. Never committed. | owner only |
| `/etc/badshahstore/certs/origin.pem` and `origin.key` | The Cloudflare Origin Certificate, mounted read-only into nginx. Never in the repository. | owner, on the server |

### Deploy and rollback (run on the Ubuntu server, from the repository folder)

```
bash deploy/deploy.sh                  # deploy origin/main
bash deploy/deploy.sh <git-ref>        # deploy a branch, tag or commit
bash deploy/deploy.sh "$(cat .deploy-previous)"   # roll back one deploy
```

The script refuses to run without a `.env` or with a changed tracked file, builds the images, waits
for `db` and `redis`, runs `migrate --noinput` and `check --deploy`, brings the stack up, waits for
`web` to be healthy, reloads nginx and asks nginx for `/health/`. On any failure it prints the last
40 log lines of `web` and `nginx` and exits non-zero.

The reload is what makes a code-only deploy safe: nginx looks the name `web` up once, when it
starts, so a deploy that rebuilds only the app image leaves the nginx container in place while
Compose recreates `web`, possibly at a new address, and the old one would answer 502 on every page.

Two git-ignored markers record the deploy, both written only at the end of a deploy that passed its
health check: `.deploy-good` is the commit now running, and `.deploy-previous` takes the previous
`.deploy-good` when the two differ. So a failed deploy, and a repeat of the same commit, leave the
rollback target alone. A rollback is itself a deploy, so after one `.deploy-previous` holds the
version that was rolled back from, and a second rollback goes forward again.

**The migration caveat for a rollback.** A deploy may already have run a migration, and checking
out the earlier commit does not undo it: the old code then runs against a newer schema. So the
script never rolls back by itself, and a rollback is a person's decision, made after reading what
failed. Nothing in the script deletes a volume (`down -v`, `volume rm`, `prune --volumes`): the
database volume is the shop's only copy of its orders, and backups are Slice 54.

### What must be verified on staging (Slice 55)

- `nginx -t` with the real certificate in `/etc/badshahstore/certs`.
- The `Content-Security-Policy` header reaching the browser unchanged, and the payment page's one
  inline script still running (its nonce is why a proxy must not touch the header).
- A Razorpay test payment from the checkout to the confirmation page.
- An R2 upload and the photo displaying from the media host.
- `deploy.sh` end to end, and one rollback with the saved commit.
- The firewall: ports 80 and 443 reachable **only from Cloudflare's published ranges**, restricted
  in the **provider's own firewall** (the DigitalOcean Cloud Firewall, or the Oracle security list
  for staging), which filters outside the server. Plain `ufw` is not enough: Docker publishes a
  container's ports by writing its own `iptables` rules, which are evaluated *before* `ufw`'s, so a
  `ufw` rule naming 80 and 443 does not restrict `nginx`'s published ports at all (a rule on the
  host would have to go into the `DOCKER-USER` chain). Port 22 stays closed to everything except
  the owner's address, also in the provider's firewall. A Slice 55 server task, and until it is done
  the `CF-Connecting-IP` header is forgeable by anyone who reaches the origin directly.
- The memory test of section 5: does the whole stack fit in 2 GB?
