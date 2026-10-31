"""Load the seed catalogue through the CSV importer (Slice 22, ADR D49)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.csv_import_apply import ImportRejected
from apps.catalog.seed import (
    SEED_DIR,
    SeedError,
    ensure_brands_and_categories,
    receive_demo_stock,
    seed_file,
)


class Command(BaseCommand):
    help = "Load the seed catalogue (nestle.csv, optionally demo.csv) through the CSV importer."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--demo", action="store_true", help="Also load demo.csv.")
        parser.add_argument(
            "--demo-stock",
            action="store_true",
            dest="demo_stock",
            help="Receive demo stock once. Requires --demo and DEBUG=True.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        demo = bool(options["demo"])
        demo_stock = bool(options["demo_stock"])

        if demo_stock and not demo:
            raise CommandError("--demo-stock requires --demo.")

        # Plain "seed_catalog" (real Nestle rows) is allowed everywhere; the demo
        # flags are dev-only.
        if (demo or demo_stock) and not settings.DEBUG:
            raise CommandError("--demo and --demo-stock require DEBUG=True.")

        self._seed_one(SEED_DIR / "nestle.csv")
        if demo:
            self._seed_one(SEED_DIR / "demo.csv")
        if demo_stock:
            count = receive_demo_stock()
            self.stdout.write(f"Demo stock received for {count} variant(s).")

    def _seed_one(self, path: Path) -> None:
        if not path.is_file():
            raise CommandError(f"Seed file not found: {path}")

        raw = path.read_bytes()
        try:
            brands_created, categories_created = ensure_brands_and_categories(raw)
        except SeedError as exc:
            raise CommandError(str(exc)) from exc

        try:
            result = seed_file(path)
        except ImportRejected as exc:
            for issue in exc.issues:
                self.stdout.write(
                    self.style.ERROR(
                        f"{path.name}: line {issue.line}, column {issue.column}: {issue.message}"
                    )
                )
            raise CommandError(f"{path.name} was rejected by the importer.") from exc

        self.stdout.write(
            f"{path.name}: brands created {brands_created}, categories created "
            f"{categories_created}, created {result.created}, updated {result.updated}, "
            f"unchanged {result.unchanged}."
        )
