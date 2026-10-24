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
- Never run commands, except the checks listed under "Checks you may run" below. Never run `git`, `manage.py`, `makemigrations`, `migrate`, or anything else. Never write migration files by hand; the owner generates them with `makemigrations`.
- Edit `badshahstore/settings/*` or `pyproject.toml` only if the prompt names that file.
- Touch only the files the prompt names or clearly needs. No drive-by refactors, no renames, no formatting-only changes to other files.
- Do not invent business rules. If something is undecided, choose the simplest default, and list it under "Open questions" in your final message so Claude can add it to `docs/open_questions.md`.
- Use exact edits on existing files (small, targeted changes). Never rewrite a whole existing file.

## Common mistakes to avoid
- Django string model references are `"app_label.ModelName"` (for example `"catalog.Product"`), never `"apps.catalog.Product"`. If the class is defined earlier in the same file, pass the class itself.
- Validators, `upload_to`, `default=` and similar arguments take the function/object itself, not a quoted dotted-path string.(Only migrations serialise them as paths; do not write that by hand.)
- You cannot run Django yourself, so re-read your own code for these mistakes before you run the checks and before you finish: every name used is imported or defined, every import is used, string references are well formed, names match across files.
- When a Python string contains double quotes, wrap it in single quotes, for example `format_html('<img src="{}">', url)`. Never leave a half-finished edit such as an `if` without a body: one SyntaxError stops all of Django.
- Admin inlines are built as `MyInline(ParentModel, admin.site)`. Tests must not pass `model=`.
- Do not unpack values you never use (ruff RUF059) and do not leave unused imports.
- If an edit or write is rejected, re-read the file and change your approach. Never send the same rejected edit twice. After two failed attempts on one step, stop and report exactly what failed.

## Test-writing and layout rules
- `SomeFactory(...)` already creates and saves the row. Never call `.save()` on its result again. `SomeFactory.build(...)` makes an unsaved object, sub-objects included, so pass already-saved parents in; a `.save()` after `.build(...)` is a real insert.
- One `pytest.raises(IntegrityError)` per test function: the database transaction is unusable after the first error.
- Name the exact exception in `pytest.raises(...)`. Never use `pytest.raises(Exception)` (ruff B017). If you call something only for its side effect, do not assign the result to a variable (ruff F841).
- Keep every line, including long `help_text` and test names, at 100 characters or fewer (ruff E501). Split long strings into adjacent string literals.
- Compare values as their real types: dates as `datetime.date`, money and quantities as `Decimal`, never as strings.
- Do not guess how Django, pytest or factory_boy behave. Known fact: `full_clean()` reports a violated CheckConstraint under the key `"__all__"`. If you are unsure, assert something you are sure of and list the doubt under "Assumptions I could not verify" in your final message.
- If your change alters how an existing model behaves (a `save()` or `delete()` override, a new constraint, a custom manager), search the tests of every app for code that uses that model, list the affected tests in your final message, and fix the ones the change breaks if the prompt allows it. If the prompt does not allow it, say so under "anything you could not do".
- Inside a model class the order is: fields, custom manager, `Meta`, `__str__`, `save`, then other methods (ruff DJ012).
- When you override a Django or QuerySet method, copy the parent's exact argument types and return type from django-stubs (mypy rejects anything narrower).
- Put helper classes such as managers and querysets at module level, above the model, not nested inside a model class, unless the prompt says otherwise.
- Put each comment directly above the code it explains.

## Checks you may run
Run these only when the prompt does not say "no checks", only AFTER all edits are done and re-read, and only in this order:
1. `uv run ruff check .` (read-only: never add `--fix`)
2. `uv run ruff format --check .` (read-only: never run `ruff format` without `--check`)
3. `uv run mypy .`
4. `docker compose exec web python -m pytest -q` (only when the prompt changed tests or code that tests cover; the containers are already running, so never start, stop or rebuild them)
Rules for the checks:
- Each command at most twice in total. If a command is not found or cannot run, say so and continue without it.
- Fix a failure only in the files the prompt named. If a failure points to any other file (a model, a migration, settings, or a test the prompt did not name), do not touch it: stop and report it.
- After a failed check you may make ONE fix attempt and re-run that command once. If it still fails, stop and report. No retry loops.
- Never run any other command, and never run commands after you have written your final message.
- Do not edit `.env`, and do not change a check's configuration to make it pass.

## How to finish (every prompt)
1. Re-open every file you created or changed from disk and check it contains what you intended. Earlier reports from agents were wrong, so verify.
2. Final message, in this order: (a) list of files created/changed with real line counts, (b) a `git diff`-style summary of the changes to existing files, (c) "Decisions I made" (bullets), (d) "Open questions" (bullets, may be empty), (e) anything you could not do, (f) "Assumptions I could not verify" (bullets, may be empty): every Django, pytest or factory_boy behaviour you relied on without being sure of it.
(g) "Checks run": for each command you ran, the command and its output pasted unedited (not summarised), or "not run".
3. Then write `STOP` and do nothing else. Do not start the next slice.
