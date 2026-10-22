# Agent brief (read this first in every prompt)

You are the coding agent for Badshah Store, a custom e-commerce site for a retail provision shop in Indore, India. The owner is an experienced software developer (.NET) who is new to Python and Django. Another AI (Claude) plans each slice and writes the prompt; you do the coding.

## Read before you code
1. `docs/STATUS.md` (where we are, how we work, lessons).
2. The files the prompt tells you to read. Match their style; do not invent new patterns.
3. `docs/decisions.md` only if the prompt mentions an ADR.

## Stack and layout
- Python 3.12, Django 5.2, PostgreSQL, pytest + pytest-django + factory_boy, ruff, mypy, pre-commit.
- Apps live in `apps/<name>/` (`core`, `catalog`, `inventory`, `accounts`, ...). Settings are in `badshahstore/settings/` (base, dev, prod, test).
- Business logic goes in `services.py`, not in views or `save()` overrides. Views stay thin.
- Money is always `Decimal`/`DecimalField`, never float. Archive with `is_active`, do not hard-delete products.
- Shared abstract models: `apps/core/models.py` (`TimeStampedModel`, `ArchivableModel`).

## Code style
- ruff: line length 100, double quotes, rules in `pyproject.toml`. Type hints on non-trivial functions.
- The owner is learning Python: prefer plain, conventional code, and add a short comment wherever a Python or Django idiom might confuse a beginner (for example `*` unpacking, `getattr` defaults, `related_name`, `on_delete`). Do not over-comment obvious lines.
- Tests live in `apps/<app>/tests/`. Use `@pytest.mark.django_db` for tests that touch the database. Follow the existing test and factory style.
- Use Django's translation helpers (`gettext_lazy as _`) for user-facing messages.

## Hard rules
- Never read, edit or print `.env`.
- Never run commands (no shell, no `manage.py`, no `pytest`, no `git`). The owner runs them. Because of this you must NOT write migration files by hand; the owner generates them with `makemigrations`.
- Edit `badshahstore/settings/*` or `pyproject.toml` only if the prompt names that file.
- Touch only the files the prompt names or clearly needs. No drive-by refactors, no renames, no formatting-only changes to other files.
- Do not invent business rules. If something is undecided, choose the simplest default, and list it under "Open questions" in your final message so Claude can add it to `docs/open_questions.md`.
- Use exact edits on existing files (small, targeted changes). Never rewrite a whole existing file.

## Common mistakes to avoid
- Django string model references are `"app_label.ModelName"` (for example `"catalog.Product"`), never `"apps.catalog.Product"`. If the class is defined earlier in the same file, pass the class itself.
- Validators, `upload_to`, `default=` and similar arguments take the function/object itself, not a quoted dotted-path string. (Only migrations serialise them as paths; do not write that by hand.)
- You cannot run Django, so re-read your own code for these mistakes before you finish: every name used is imported or defined, every import is used, string references are well formed, names match across files.
- When a Python string contains double quotes, wrap it in single quotes, for example `format_html('<img src="{}">', url)`. Never leave a half-finished edit such as an `if` without a body: one SyntaxError stops all of Django.
- Admin inlines are built as `MyInline(ParentModel, admin.site)`. Tests must not pass `model=`.
- Do not unpack values you never use (ruff RUF059) and do not leave unused imports.
- If an edit or write is rejected, re-read the file and change your approach. Never send the same rejected edit twice. After two failed attempts on one step, stop and report exactly what failed.

## How to finish (every prompt)
1. Re-open every file you created or changed from disk and check it contains what you intended. Earlier reports from agents were wrong, so verify.
2. Final message, in this order: (a) list of files created/changed with real line counts, (b) a `git diff`-style summary of the changes to existing files, (c) "Decisions I made" (bullets), (d) "Open questions" (bullets, may be empty), (e) anything you could not do.
3. Then write `STOP` and do nothing else. Do not start the next slice.
