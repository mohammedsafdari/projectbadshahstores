# Slice stages (the agent follows this after the slice work)

Every slice prompt ends with: "After the work, follow `docs/slice_stages.md` with SLICE = <id>, MUTATIONS = <count>, TESTS = <files to run while working>". This file says what to run and in what order. It is written to save time: the full suite takes about two minutes, a full mutation run about ten, so run each only when this file says so.

## Rules for every stage

1. **Batch your fixes.** Run a command, read the WHOLE output, collect every failure from it, fix all of them, and only then run again. Never fix one problem and rerun at once.
2. **Small first, full last.** While you work and fix, run only the new or changed test files (`docker compose exec web python -m pytest -q <files>`). The full suite runs only in Stage 4.
3. **Commands you may run (nothing else):** `git status --short` (read only, no other git command, ever); `uv run ruff format .`; `uv run ruff check .`; `uv run mypy apps`; `docker compose exec web python manage.py makemigrations --check --dry-run`; `docker compose exec web python -m pytest -q ...`; `python tools\review.py checks`; `python tools\review.py suite 3`; `python tools\review.py mutate <file> [--only ID,ID]`; and the `makemigrations <app>` commands the slice prompt names.
4. Never touch `.env`. Never edit `tools/review.py`, a migration the prompt did not ask for, `pyproject.toml` or a setting unless the slice prompt names it. Never edit or delete an existing test to make it pass without saying so, with the reason and the old and new assertion, in the report. Never loosen an assertion.
5. If a command runs longer than ten minutes without output, say so in the report and go on with what you can; do not start extra commands.

## Stage 1: the slice work

Done as the slice prompt says, including its docs. Before Stage 2 run `git status --short` once to check you only touched what the prompt named.

## Stage 2: quick checks (small)

Run `uv run ruff format .`, `uv run ruff check .`, `uv run mypy apps`, `docker compose exec web python manage.py makemigrations --check --dry-run` and `docker compose exec web python -m pytest -q <the new and changed test files>`. Fix everything found (batched, rule 1), then run the same list again. Repeat until green. Do not run the full suite yet.

## Stage 3: breakages

1. Write `tools/mutations/mutations-<SLICE>.json` in the format `tools/review.py` reads. `tests` holds only the test files that exercise the mutated product files (at most about ten, and the baseline must pass in under 30 seconds). At least MUTATIONS mutations, each ONE logical change in ONE product file under `apps/` or `templates/`, never a test, a migration, a model field, a constraint or a choice (the test database is reused, so a schema change would not take effect). `old` occurs exactly once in the file; the changed file must still import and render. Vary the kinds: flip a comparison, turn a guard into `if False:`, swap an operator, drop a term from a sum, replace a variable with its older twin, remove a filter, swap two statements, change a constant, change a sentence, invert a template condition. Use the seed list in the slice prompt first, then choose more yourself.
2. Run `python tools\review.py mutate tools\mutations\mutations-<SLICE>.json` once. Read every line.
3. For each SURVIVED or ERROR line decide, in one sentence: **equivalent** (it cannot change behaviour; explain why; no test) or **test gap** (add a focused test to the right file; it must fail with the mutation and pass without it). A NOT APPLIED or ERROR caused by a bad mutation (does not import, text not found once) is rewritten, not counted. A real BUG in the product code: do not change the product code in this stage; write a test with `pytest.mark.xfail(strict=True, reason="BUG: ...")` and put the bug at the top of the report.
4. Fix all survivors in one batch, then run **only the survivors**: `python tools\review.py mutate tools\mutations\mutations-<SLICE>.json --only ID,ID,...`. Repeat at most four times, until every survivor is caught or explained as equivalent. Do **not** rerun the whole mutation file.

## Stage 4: final verification (once)

Run `python tools\review.py checks`, then `python tools\review.py suite 3`. If anything fails, fix every failure (batched), run the failing part again, and then run `python tools\review.py suite 3` one last time. Never run `suite 3` after each single fix. All five `checks` lines must be PASS and the three suite runs must show the same test count and all passed.

## Stage 5: the report and the commands for the owner

Run `git status --short` last. It must list only the files the slice prompt names, the new and changed test files and `tools/mutations/mutations-<SLICE>.json`. If any other product file or a migration appears, say so at the very top.

**Write the report short (about 60 lines at most), in this order:**
1. One line: all green, or what is not.
2. Test count before (the baseline you noted at the start) and after, and the run times of Stage 4 (`took N s` lines).
3. The raw last line of each Stage 2 and Stage 4 command, from the last full pass.
4. Mutations: the count written, the first-run result as one line (`N caught, M survived`), then **only the survivors and bad mutations, one line each**: id, verdict (test gap, equivalent, BUG), the test added or the reason. Caught mutations are not listed one by one. The last `mutations:` line of the survivor rounds.
5. Every existing test you changed, with the old and the new assertion and the reason (or "none").
6. Every decision you took that the prompt did not cover (one line each).
7. The `git status --short` output.
8. Where the time went (one line per stage, from the `took` lines and your own estimate).

**Then print two blocks for the owner. Do NOT run them.**

**A. Commands to verify (Windows Command Prompt, from the repo folder):**
```
git status --short
python tools\review.py checks
python tools\review.py suite 1
python tools\review.py mutate tools\mutations\mutations-<SLICE>.json
```
followed by a short list (at most six lines) of things to look at in the browser if the slice changed a page, with the exact URLs or clicks, and what the owner should see. Write "no page changed" when none did.

**B. Commands to commit and push (for the owner to run after the checks pass):** one `git add` line that names every file from `git status --short` one by one (never `git add -A`, never `git add .`), then a `git commit -m "Slice <SLICE>: <short summary>"` line, then `git push`. Name the branch you expect (`main`).

**STOP.** Do nothing after the two blocks, and run no extra commands.
