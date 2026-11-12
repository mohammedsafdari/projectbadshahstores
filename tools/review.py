"""Review helper for Badshah Store slices. Run it from the repo root folder.

    python tools\\review.py checks
    python tools\\review.py suite 3
    python tools\\review.py mutate tools\\mutations\\mutations-43.json

It uses only the standard library. The test runs go through Docker (the
official run) and ruff and mypy through uv, exactly as docs/STATUS.md says.
Every command prints one short line, and the same lines are saved in
review-report.txt next to this script (it is git-ignored), so you can paste
that file to Claude. The agent may run it too; "mutate" always puts every file
back, and compares `git status` before and after the run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).with_name("review-report.txt")
PYTEST = ["docker", "compose", "exec", "-T", "web", "python", "-m", "pytest", "-q"]
DJANGO = ["docker", "compose", "exec", "-T", "web", "python", "manage.py"]


def say(line: str) -> None:
    """Print one line and append it to the report file."""
    print(line, flush=True)
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(command: list[str]) -> tuple[int, str]:
    """Run a command and return (exit code, all output as text)."""
    done = subprocess.run(  # noqa: S603 - fixed command lists, no user input
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


def checks() -> None:
    steps = [
        ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."]),
        ("ruff check", ["uv", "run", "ruff", "check", "."]),
        ("mypy apps", ["uv", "run", "mypy", "apps"]),
        ("manage.py check", [*DJANGO, "check"]),
        ("makemigrations --check", [*DJANGO, "makemigrations", "--check", "--dry-run"]),
    ]
    for name, command in steps:
        code, out = run(command)
        say(f"{'PASS' if code == 0 else 'FAIL'}  {name}: {last_line(out)}")


def suite(times: int) -> None:
    for number in range(1, times + 1):
        code, out = run(PYTEST)
        say(f"{'PASS' if code == 0 else 'FAIL'}  full suite run {number}: {last_line(out)}")
        if code != 0:
            for line in out.strip().splitlines()[-12:]:
                say("    " + line)


def mutate(path: str) -> None:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    tests = spec["tests"]
    command = [*PYTEST, "-x", "--reuse-db", *tests]
    code, out = run(command)
    say(f"baseline: {last_line(out)}")
    if code != 0:
        say("STOP: the tests do not pass before any change, so no mutation was tried.")
        return
    _, status_before = run(["git", "status", "--short"])
    caught = 0
    survivors: list[str] = []
    for item in spec["mutations"]:
        target = Path(item["file"])
        original = target.read_bytes().decode("utf-8") if target.exists() else ""
        old, new = item["old"], item["new"]
        if "\r\n" in original:  # a Windows file: match its line endings
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        found = original.count(old)
        if found != 1:
            say(f"NOT APPLIED  {item['id']}: text found {found} times in {item['file']}")
            continue
        try:
            target.write_text(original.replace(old, new), encoding="utf-8", newline="")
            code, out = run(command)
        finally:
            # Always put the file back, even on Ctrl+C.
            target.write_text(original, encoding="utf-8", newline="")
        if code == 1:
            caught += 1
            say(f"CAUGHT    {item['id']}: {item['note']}")
        elif code == 0:
            survivors.append(item["id"])
            say(f"SURVIVED  {item['id']}: {item['note']}")
        else:
            say(f"ERROR rc={code}  {item['id']}: {last_line(out)}")
    say(f"mutations: {caught} caught, {len(survivors)} survived ({', '.join(survivors) or 'none'})")
    _, status_after = run(["git", "status", "--short"])
    if status_after == status_before:
        say("git status after the run: same as before the run")
    else:
        say("git status CHANGED during the run:")
        for line in status_after.strip().splitlines():
            say("    " + line)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"checks", "suite", "mutate"}:
        print(__doc__)
        return
    say("== " + " ".join(Path(part).name for part in sys.argv[1:]))
    if sys.argv[1] == "checks":
        checks()
    elif sys.argv[1] == "suite":
        suite(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        mutate(sys.argv[2])


if __name__ == "__main__":
    main()
