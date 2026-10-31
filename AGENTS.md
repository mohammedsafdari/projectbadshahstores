# Badshah Store: agent rules

You start every session with no memory of this project. Before any work, read, in this order:
1. `docs/AGENT_BRIEF.md` (coding rules, the check commands, the format of your final message)
2. `docs/STATUS.md` (what is done, what is next)
3. The decisions (ADRs) in `docs/decisions.md` that the task names

## What this project is
A Django 5.2 / PostgreSQL online store for a provision shop in Indore, India. Built in numbered
slices. Money, stock and GST code needs extra care and tests.

## Hard rules
- Never read, print or edit `.env` or any other secret file.
- The GitHub repository is PUBLIC. Never put cost prices, purchase rates or personal data in any
  committed file.
- Edit `pyproject.toml`, settings files and migrations only if the task names them.
- Never invent business facts (GST rates, HSN codes, prices, FSSAI numbers, addresses). If a fact is
  missing, say so in "Open questions" in your final message.
- Do not change code outside the task. No refactors, no extra files, no "improvements".
- Never run `git commit`, `git push`, `git reset` or `git checkout`. The owner commits.
- Never run `ruff format` without `--check`, and never `ruff check --fix`.
- Run only the four check commands listed in `docs/AGENT_BRIEF.md`, once, at the end.
- Do not guess how Django or pytest behave. List every unverified assumption in your final message.

## The owner
The owner is an experienced .NET engineer who is new to Python. Add a short plain-English comment
above every non-obvious Python idiom (generators, comprehensions, `*args`, `with`, decorators, etc.).
Commands you tell the owner to run must use Windows 11 Command Prompt syntax.

## Finishing
When the task is done: give the final message in the format from `docs/AGENT_BRIEF.md`, show the
diff summary, and STOP. Do nothing extra after that.
