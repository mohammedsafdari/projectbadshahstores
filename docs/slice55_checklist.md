# Slice 55 checklist: the first staging run

Staging runs on an Oracle Always Free VM behind a free DuckDNS name with a Let's
Encrypt certificate (no Cloudflare in front, no purchase). Nothing in slices 52
to 54f1 has run for real yet, so every step here can turn up a small fix.

Conventions: commands for your PC are Windows 11 Command Prompt. Commands for
the VM are Ubuntu bash and always start with "On the server". Never paste a
secret into a chat, a prompt file or the repository.

Replace `NAME` below with your DuckDNS name (for example `badshahstores`), so the
host is `NAME.duckdns.org`.

## 0. What must exist first

- Oracle account active, home region Mumbai or Hyderabad.
- DuckDNS name `NAME` reserved.
- The Sentry DSN, the healthchecks.io ping URL and the `age1...` public key in
  Bitwarden.
- R2 is NOT needed for the first deploy. With `R2_BUCKET_NAME` empty the shop
  keeps photos in the local media folder. Sections 8 and 9 wait for R2.
- If the VM is an Ampere (ARM) shape: `docker/Dockerfile.prod` downloads the x64
  Tailwind binary, so a small fix (choose the arm64 file on an ARM machine) must
  be committed and pushed BEFORE section 5. An AMD x64 VM needs no fix.

## 1. Create the VM (Oracle console)

1. Compute, Instances, Create instance. Name `badshah-staging`.
2. Image: Ubuntu 22.04 or 24.04. Shape: an Always Free shape only (check the
   "Always Free eligible" label). Ampere A1 with 2 OCPU and 12 GB is the roomy
   one; AMD Micro (1 GB) is too small to build the images.
3. Networking: keep the default VCN and a public subnet, assign a public IPv4.
4. SSH key: let Oracle generate a pair and save the PRIVATE key file somewhere
   safe on your PC (for example `C:\Users\<you>\.ssh\badshah-staging.key`), and
   keep a copy in Bitwarden.
5. Boot volume: the default 50 GB is fine.
6. Create, wait for "Running", note the public IP address.

## 2. Network rules (Oracle's own firewall)

Docker bypasses `ufw`, so the firewall that counts is the one outside the VM.
Virtual cloud network, the subnet's Security List, Ingress Rules. Keep only:

| Source CIDR | Protocol | Port | Why |
|---|---|---|---|
| your home IP /32 | TCP | 22 | SSH, only from you |
| 0.0.0.0/0 | TCP | 80 | HTTP (certificate check and redirect) |
| 0.0.0.0/0 | TCP | 443 | HTTPS |

Delete the default rule that allows 22 from everywhere. If your home IP changes
you edit this one rule. (Production will allow 80 and 443 only from Cloudflare's
published ranges; staging has no Cloudflare, so it is open.)

## 3. DuckDNS points at the VM

On duckdns.org put the VM's public IP next to `NAME` and click update. From
your PC (Command Prompt) check it:

    nslookup NAME.duckdns.org

The answer must be the VM's IP.

## 4. Log in and prepare the server

From your PC:

    ssh -i C:\Users\<you>\.ssh\badshah-staging.key ubuntu@<VM-IP>

On the server:

    sudo apt-get update && sudo apt-get -y upgrade
    sudo apt-get install -y git curl age certbot
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker $USER

Log out and in again (so the docker group applies), then:

    docker --version
    docker compose version
    age --version

A swap file protects the small VM during the image build:

    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
    sudo mkswap /swapfile && sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

## 5. Get the code

On the server:

    git clone https://github.com/mohammedsafdari/projectbadshahstores.git badshahstore
    cd badshahstore

## 6. The certificate (Let's Encrypt for the DuckDNS name)

nginx will not start without its two certificate files, so get them BEFORE the
first deploy. certbot needs port 80 free, which it is until the stack runs.

On the server:

    sudo certbot certonly --standalone -d NAME.duckdns.org -m <your-email> --agree-tos --no-eff-email
    sudo mkdir -p /etc/badshahstore/certs
    sudo cp /etc/letsencrypt/live/NAME.duckdns.org/fullchain.pem /etc/badshahstore/certs/origin.pem
    sudo cp /etc/letsencrypt/live/NAME.duckdns.org/privkey.pem /etc/badshahstore/certs/origin.key
    sudo chmod 644 /etc/badshahstore/certs/origin.pem
    sudo chmod 600 /etc/badshahstore/certs/origin.key

The certificate lasts 90 days. Put a reminder in your calendar for day 60 and
renew by hand (certbot's own timer cannot renew while nginx holds port 80):

    cd ~/badshahstore
    docker compose -f docker-compose.prod.yml stop nginx
    sudo certbot renew
    sudo cp /etc/letsencrypt/live/NAME.duckdns.org/fullchain.pem /etc/badshahstore/certs/origin.pem
    sudo cp /etc/letsencrypt/live/NAME.duckdns.org/privkey.pem /etc/badshahstore/certs/origin.key
    docker compose -f docker-compose.prod.yml start nginx

## 7. The `.env` file

On the server, in `~/badshahstore`:

    cp deploy/production.env.example .env
    nano .env

Generate the random values on the server and paste them in (`-hex` keeps them
safe inside database addresses):

    python3 -c "import secrets; print(secrets.token_urlsafe(50))"
    openssl rand -hex 24

Use a new `openssl rand -hex 24` for each of `POSTGRES_PASSWORD` and
`REDIS_PASSWORD`. Values to set for staging:

| Variable | Staging value |
|---|---|
| `SECRET_KEY` | the `token_urlsafe` output |
| `POSTGRES_USER`, `POSTGRES_DB` | `badshah` |
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | two different `openssl` values |
| `ALLOWED_HOSTS` | `NAME.duckdns.org` |
| `CSRF_TRUSTED_ORIGINS` | `https://NAME.duckdns.org` |
| `SITE_BASE_URL` | `https://NAME.duckdns.org` |
| `CLIENT_IP_HEADER` | `HTTP_X_REAL_IP` (no Cloudflare on staging) |
| `SECURE_HSTS_SECONDS` | `3600` (stays low until after the soft launch) |
| `SENTRY_DSN` | your DSN |
| `SENTRY_ENVIRONMENT` | `staging` |
| `AGE_RECIPIENT` | your `age1...` public key |
| `BACKUP_PING_URL` | your healthchecks.io ping URL |
| `PAYMENT_PROVIDER` | empty until the Razorpay test keys exist |
| `ORDER_EMAIL_ENABLED`, `ORDER_SMS_ENABLED` | `0` |

Leave every R2 variable empty for now. If `prod.py` refuses the `CHANGE_ME`
Razorpay lines with the provider empty, note the exact message; that is a small
fix for the agent, not for you to edit around. Save a copy of the finished
`.env` as a Secure Note in Bitwarden (staging only).

Then check the file is private:

    chmod 600 .env

## 8. The first deploy

On the server:

    docker compose -f docker-compose.prod.yml config > /dev/null && echo config-ok
    bash deploy/deploy.sh

It ends with a health check through nginx and writes the rollback markers only
if that passes. If it stops, copy the last 30 lines of its output and send them
to Claude. Do not edit code on the server.

Create the first staff user:

    docker compose -f docker-compose.prod.yml run --rm web python manage.py createsuperuser

## 9. Verification (the eight items from the handoff, staging version)

Tick each one; send Claude any that fail, with the exact message.

1. `config` and `build` succeeded (section 8) and `nginx -t` passes:
   `docker compose -f docker-compose.prod.yml exec nginx nginx -t`
2. Deploys: change nothing, run `bash deploy/deploy.sh` again and load the home
   page (no 502, proves the nginx reload). Then test a deploy with quotes around
   the `ALLOWED_HOSTS` value, and a rollback:
   `bash deploy/deploy.sh "$(cat .deploy-previous)"`
3. CSP: open `https://NAME.duckdns.org/` in a browser, developer tools Console
   open. Visit `/`, a category, a product, the cart, `/offline/`, `/staff/`,
   `/admin/`. Zero CSP errors. (The Cloudflare path itself is production only.)
   The Razorpay TEST payment waits for the test keys; if a Razorpay host is
   blocked, widen exactly one CSP directive.
4. Media: with R2 not yet active, upload a product photo and confirm it shows
   from the local folder. The R2 test waits for activation.
5. Backups (after R2): `bash deploy/backup.sh`, a file in the backup bucket, the
   healthchecks.io check turns green, a Sentry test message arrives, then
   `bash deploy/restore_test.sh` and compare the row counts. Use the private
   `age` key from your PC for any manual decrypt.
6. Firewall: from a phone on mobile data, port 22 must NOT answer; 80 redirects
   to HTTPS; 443 works.
7. `.env` values from section 7 are in place; `ALLOWED_HOSTS` has no quotes
   problem.
8. Lighthouse on staging with `docs/lighthouse.md` (the first run is the
   baseline).

## 10. After the run

Send Claude the results as a list of numbers with PASS or FAIL and the exact
error for each FAIL. Small fixes go to the agent as one prompt per file. When
Oracle or R2 or the card is the blocker, nothing here changes: the section
simply waits.
