"""The D76 guardrail: the provider name stays in its allow-listed files."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

# Everything else in apps/ and templates/ says "the gateway" instead.
ALLOWED = {
    "apps/payments/razorpay_gateway.py",
    "apps/payments/gateways.py",
    "templates/payments/gateways/razorpay.html",
}


def test_the_provider_name_appears_only_where_it_is_allowed() -> None:
    root = Path(settings.BASE_DIR)
    offenders: list[str] = []
    for folder in ("apps", "templates"):
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".html"):
                continue
            relative = path.relative_to(root).as_posix()
            if relative in ALLOWED:
                continue
            if relative.startswith("apps/payments/tests/"):
                continue
            if relative.startswith("apps/") and "/migrations/" in relative:
                continue
            if "razorpay" in path.read_text(encoding="utf-8").lower():
                offenders.append(relative)

    assert offenders == []
