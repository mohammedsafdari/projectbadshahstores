# Badshah Store: Status

Start here in every new chat. Update this file at the end of every slice (see "Slice closeout checklist" below).

## Where we are

Phase 1 (scaffold) is done. Phase 2 (catalogue and inventory) is in progress. Last finished slice: 17 (AuditLog). Next slice: 18 (inventory admin).

Terminology: phase numbers come from the build plan in `PROJECT_INSTRUCTIONS.md` section 9. Where section 1 of that brief says "Phase 2 adds pan-India shipping", it means a later business phase, which is Phase 8 in the build plan.

## What is built

- Accounts: custom user model, login by phone (Phase 1).
- Core: abstract `TimeStampedModel` and `ArchivableModel` (Slice 12).
- Catalog: `Brand`, `Category` (a tree through `parent`), `Product` and `ProductVariant`, with admin (variants editable on the product page), factories and tests (Slices 12 and 13). Pricing helpers in `apps/catalog/services.py` (Slice 13c).
- Catalog images: `ProductImage` (several photos per product, validated uploads, local media storage in dev) and an admin inline with a thumbnail (Slice 14, D41).
- Loose-by-weight and low stock on variants: `is_sold_loose`, `quantity_step` and an optional `low_stock_threshold`, with database constraints (Slice 15b and 15c, D42).
- Inventory: `Supplier` (name, phone, notes; archivable) and the append-only `InventoryMovement` ledger: decimal quantities, database sign rules per movement type, optional batch, expiry and supplier on purchases, and `AppendOnlyError` blocking updates and deletes through `save`, `delete`, queryset `update` and `delete`, and `bulk_update` (Slice 15d to 15f, D42). There is no inventory admin yet.
- Inventory services (`apps/inventory/services.py`, Slice 16, D43): `get_on_hand`, `receive_stock`, `adjust_stock`, `record_damage` and `count_stock`. Every write runs in `transaction.atomic()`, locks the variant row with `select_for_update()` and goes through one private `_record_movement`. Quantities are `Decimal` or `int` with at most 3 decimals, whole numbers for packet variants. Errors: `InventoryError`, `InvalidQuantityError`, `InsufficientStockError`. Concurrency tests prove the lock: removing it makes them fail.
- Integrity check: `find_integrity_problems()` in `apps/inventory/integrity.py` and the read-only command `check_inventory_integrity` (exit code 1 only on an ERROR). It is run by hand; nothing schedules it yet.
- Audit log (Slice 17, D44): append-only `core.AuditLog` (actor, action, target_type, target_id, changes as JSON `{field: [old, new]}`, note). `AppendOnlyError` now lives in `apps/core/models.py` (inventory imports it). One write path: `record_change` and `snapshot` in `apps/core/audit.py` (Decimal values are stored as strings, floats are refused, nothing is written when no value changed; call it inside the same transaction as the change). Read-only admin `AuditLogAdmin` (no add, change or delete; reading needs the `core.view_auditlog` permission, superusers have it). Variant helpers in `apps/catalog/audit.py` (`AUDITED_VARIANT_FIELDS`, `snapshot_variant_from_db`, `record_variant_change`, the formset helpers, `audit_actor`): editing an existing variant on its own admin page (`save_model`) or inside the product page (`save_formset`) writes a `VARIANT_UPDATE` row. Stock is not audited here; the ledger covers it.
- Everything else in `docs/architecture.md` is target design only; the other app folders are empty shells.

## Slice log

| Slice | What changed |
| --- | --- |
| 1 to 10 | Phase 1 scaffold: settings split, Docker Compose, CI, pre-commit, custom user model, health endpoint, README, ADRs up to D35. |
| 11 | Removed the no-op `USE_X_FORWARDED_PROTO` setting (D36); README note about shared compose containers. |
| 12 | `core` base models; `Brand` and `Category`; admin, factories, tests; ASCII-only slugs (D37, D38). |
| 13 | `Product` and `ProductVariant` with database check constraints; admin with variant inline; pricing helpers; tests (D39, D40). |
| 13d | `docs/open_questions.md` updated with Phase 2 items. |
| 13e to 13i | Documentation sync: this file, ADRs D37 to D40, architecture, README, new open questions. |
| 14a to 14d | Pillow dependency and dev media URL; upload validators; `ProductImage` model, factory and tests; admin inline with thumbnail; Dockerfile makes media and staticfiles writable (D41). |
| 14e | Documentation sync: ADR D41, open questions, architecture, this file, agent brief. |
| 15a | ADR D42 (inventory decisions and loose-by-weight quantities); D23 narrowed. |
| 15b to 15c | `ProductVariant`: `is_sold_loose`, `quantity_step`, `low_stock_threshold`, with constraints. |
| 15d | `Supplier` model. |
| 15e | `InventoryMovement` ledger model with check constraints. |
| 15f | Append-only guard; tidy-up; test-writing rules and read-only checks added to `docs/AGENT_BRIEF.md`. |
| 15g | Documentation sync: this file, architecture, open questions. |
| 16a | ADR D43 (inventory services) and three open questions. |
| 16b | `services.py` core (validation, lock, single write path), `get_on_hand`, `receive_stock`, with tests; types tightened. |
| 16c | `adjust_stock`, `record_damage`, `count_stock`, with tests. |
| 16d | Concurrency tests (threads, `transaction=True`); thread-test rule in `docs/AGENT_BRIEF.md`. |
| 16e | `apps/inventory/integrity.py`: `find_integrity_problems`, with tests. |
| 16f | `check_inventory_integrity` management command, with tests. |
| 16g | Documentation sync: this file, ADR D43 corrections, README. |
| 17a | ADR D44 (AuditLog) and open questions. |
| 17b | `core.AuditLog` model, append-only guard, `AppendOnlyError` moved to `core`, factory and tests. |
| 17c | `apps/core/audit.py`: `record_change` and `snapshot`, with tests. |
| 17d | Read-only `AuditLogAdmin`, with tests (an unwanted `has_view_permission` override was removed). |
| 17e | `apps/catalog/audit.py` and `ProductVariantAdmin.save_model` write variant audit rows. |
| 17f | `ProductAdmin.save_formset` audits variants edited on the product page; tests post the real admin page. |
| 17g | Documentation sync: this file, ADR D44 additions, open questions. |

## Remaining Phase 2 slices

14. (done) ProductImage and media storage.
15. (done) Inventory models: `InventoryMovement` ledger, `Supplier`, loose-by-weight and low-stock fields (D42).
16. (done) Inventory services and integrity check (D43).
17. (done) AuditLog in core (D44): stock is not audited (the ledger covers it); variant edits in the admin are.
18. Inventory admin (receive and adjust actions, read-only movement history, low-stock and expiry views).
19. CSV export (service plus admin action).
20. CSV import with a validation report (dry run, then commit). REVIEW CAREFULLY.
21. Bulk price update admin action (must write audit rows: `snapshot_variant_from_db` before, `record_variant_change` after; queryset `update()` skips any automatic hook, D44).
22. Seed command (about 100 realistic SKUs loaded through the importer; idempotent).
23. Storefront listing, category and brand pages.
24. Filters (HTMX).
25. Product detail page (variants, savings, FSSAI info, JSON-LD).
26. Search service (PostgreSQL full-text and trigram behind an interface).
27. Search-as-you-type UI.
28. Phase 2 wrap-up (docs, ADRs, README, smoke test, Python recap).

StockReservation stays in Phase 3.

## Decided in Slice 15 (kept for reference)

All four were decided and recorded as ADR D42 in `docs/decisions.md`, together with the loose-by-weight rule. The list below is the original questions.

1. Current stock: derive it as the sum of movements (what `docs/architecture.md` says) instead of a cached StockLevel table. Add a cache later only if it is slow.
2. Batch and expiry: optional batch number and expiry date on received stock only; no first-expiry-first-out logic (the brief asks for expiry and batch tracking).
3. Supplier: a minimal optional record (name, phone, notes) on purchases.
4. Low-stock threshold: an optional number per variant; no alert when blank.

## Known gaps

- Category tree cycles are not prevented (nothing stops A to B to A). Add validation before Slice 23.
- `Invoice` and `Address` models are in the brief but not yet in the data model of `docs/architecture.md`; design them before Phase 3 (addresses) and Phase 5 (invoices).
- Rounding rules and HSN format are defaults awaiting the CA (see `docs/open_questions.md`); they must be confirmed before invoices are issued (Phase 5).
- Photo files are not deleted from disk when an image row or product is deleted; add a clean-up job before production (see `docs/open_questions.md`).
- Only original photos are stored; resized and WebP versions come with the storefront slices (23 and 25) or Phase 6.
- The ledger is append-only in Django only (`save`, `delete` and queryset methods); raw SQL can still change rows. A Postgres trigger or a restricted database role is a production hardening step (see `docs/open_questions.md`).
- Loose-by-weight quantities exist in the ledger and on variants; the cart, orders and reservations (Phase 3) must use decimal quantities too.
- Operations that touch several variants (the Slice 20 import) must lock the variants in ascending `pk` order (D43); there is no helper for that yet.
- `receive_stock` checks the supplier's `is_active` on the object passed in and does not lock the supplier, so a supplier archived at the same moment could be used once more.
- `check_inventory_integrity` is run by hand; nothing schedules it (cron or a job) yet.
- The variant audit covers edits of existing variants on the two admin pages only. Creating or deleting a variant, other models (product, brand, category), and changes made in the shell or with queryset `update()` write no audit row. Slices 20 and 21 must call the helpers in `apps/catalog/audit.py` (D44).
- `cost_price` is not shown in the product-page variant inline, so it is audited only when edited on the variant's own admin page.
- `AuditLog` is append-only in Django only; raw SQL can still change rows (same production hardening step as the ledger, D42 and D44). Retention of audit rows is an open question.

## How we work

- The agent (Freebuff) never touches `.env`. It may run only the read-only checks listed in `docs/AGENT_BRIEF.md` (ruff, mypy, pytest), once, at the end, and it pastes their raw output; it never runs any other command. This is a trial that started in Slice 15f: if it breaks the rule even once, we go back to no commands. Every prompt ends with a STOP line, and the agent shows the diff and stops.
- Each prompt states a goal, the files to read first and the rules. The agent does the coding and starts by reading `docs/AGENT_BRIEF.md`. Existing files change by small exact-match edits (each OLD text must appear exactly once, or nothing changes); new files may be written in full. Keep each prompt small: one long multi-step prompt once stalled in a retry loop.
- The agent edits settings or `pyproject.toml` only if the prompt names them. After any `pyproject.toml` edit, run the TOML check.
- Check the real `git diff` and `--stat`, not the agent's pasted diff. Commit in small steps after `git status` and `git diff --stat`.
- Every commit message starts with the slice id, for example `Slice 14a: Add ProductImage model`.
- Windows 11, Command Prompt. Python and Django idioms are explained as we go, because the owner is new to Python.
- Business questions nobody can answer yet go into `docs/open_questions.md` with the default in use; work continues unless the answer cannot be changed later.

Everyday commands (they run inside the container):

- `docker compose exec web python manage.py check`
- `docker compose exec web python manage.py makemigrations <app> --name <short_name>`
- `docker compose exec web python manage.py migrate`
- `docker compose exec web python -m pytest -q`
- `pre-commit run --all-files` (run all hooks once before the first commit attempt; it may reformat files, then run `git add -u`)

## Lessons

- Agent reports can be wrong: invented text, inconsistent line numbers, even checking the wrong file name. Verify key facts with `findstr`, `type`, `git ls-files` and `git diff`.
- The questions file is `docs/open_questions.md` (underscore).
- Django's Unicode slug check rejects Devanagari vowel signs (D37).
- Rewriting a long file in one prompt corrupted it once; use exact-match edits.
- On this Windows PC `git push` needed `git config --global http.sslBackend schannel`.
- Pre-commit may reformat files; run `git add -u` and commit again.
- The agent sometimes reports edits that never reached disk. Check `git status` and `git diff` before anything else.
- Django string model references are `"app_label.ModelName"`. Validators and `upload_to` take the function itself, not a quoted dotted path.
- After adding a dependency: refresh the lock file (`docker compose run --rm --no-deps web uv lock`), rebuild, then recreate containers with `--renew-anon-volumes`, otherwise the old `/opt/venv` volume hides the new package.
- Uploaded media lives in the `/app/media` Docker volume, not in the project's `media\` folder, and `--renew-anon-volumes` deletes it (dev data only). The Dockerfile creates `/app/media` and `/app/staticfiles` owned by the `app` user; without that, uploads failed with PermissionError.
- Verify docs work as well: ADRs D37 to D40 were once listed here without being written to `docs/decisions.md`.
- Before committing code, run the whole gate once: pytest, `makemigrations --check --dry-run`, `pre-commit run --all-files`; then `git add -u` and `git add apps/<app>`, and check that new migration files appear in `git status`.
- A change that alters how an existing model behaves (a `save` override, a constraint, a manager) can break old tests. Name them in the prompt and let the agent fix them.
- Agent edits can merge or mangle neighbouring lines, even in docs (two bullets of `AGENT_BRIEF.md` were once joined). Review the real diff of every commit, including docs.
- A race test must be able to fail: remove `select_for_update()` (keep `.objects`; a typo that crashes the code proves nothing), run the test a few times, expect assertion errors, then `git restore` the file.
- Factories can hide rows: `InventoryMovementFactory` also creates a +10 purchase for its variant. In ledger tests build the variant with `ProductVariantFactory` and add movements by hand.
- Two test functions with the same name silently replace each other (ruff F811). After the agent writes tests, list them with `findstr /n "def test_"`.
- With checks allowed, the agent once wrote files nobody asked for (a note and an empty report file) and printed a wrong path in its report. Run `git status` for untracked files and say "create exactly these files and no others".
- Exact-match edits at the end of a file or inside a long line failed silently or split lines. New files with verbatim content (`integrity.py`, the concurrency tests) worked.
- mypy does not know reverse accessors such as `variant.movements`; query from the model that owns the foreign key (`InventoryMovement.objects.filter(variant_id=...)`).
- Pasting text with a stray `>` into Command Prompt creates junk files; check `git status` before every commit.
- When the agent loops on a rejected edit, stop it, throw its files away (`git restore`, delete the untracked files) and write the change another way. Its half-applied files (duplicated lines, an IndentationError, two tests with one name) were worse than none (17e).
- "Change nothing else" was ignored once (unrelated inventory tests rewritten) and an unrequested `has_view_permission` returning True let every staff user read the audit log; only a test that a staff user without permissions gets 403 caught it. Name every permission method in the prompt and review test-file diffs too.
- Run the whole suite yourself before committing; an agent report with a failing test inside it is not a pass (17c).
- Django's admin escapes HTML, so `->` appears as `-&gt;` in page text; assert on the escaped form.
- In an admin `save_model` or `save_formset` the form has already put the new values on the object; read the old values from the database before saving.
- django-stubs generics such as `BaseFormSet[Any]` fail at run time unless the module starts with `from __future__ import annotations`.

## Slice closeout checklist

1. Tests pass (`docker compose exec web python -m pytest -q`), `manage.py check` is clean and `makemigrations --check --dry-run` says no changes.
2. Commit with the slice id, push, and confirm CI is green.
3. Update this file: last finished slice, what is built, the slice log, remaining slices, known gaps.
4. Add an ADR to `docs/decisions.md` for every new decision (next free number), noting where it narrows the brief.
5. Update `docs/architecture.md` only if the target design changed.
6. Update `docs/open_questions.md`: new doubts, and answers received.
7. Update `README.md` only if setup or commands changed.
8. Commit the docs as `Slice <n>: Update docs`.
9. Start a new chat: attach `PROJECT_INSTRUCTIONS.md` and `docs/STATUS.md`, add other docs only when the next slice touches them, and say "Continue at Slice <next>".
