"""Tests for the seed_catalog management command (Slice 22, ADR D49)."""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.catalog.csv_import import parse_variants_csv
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.catalog.seed import SEED_DIR
from apps.core.models import AuditLog
from apps.inventory.models import InventoryMovement

NESTLE = SEED_DIR / "nestle.csv"
DEMO = SEED_DIR / "demo.csv"
REVIEW = SEED_DIR / "nestle_review.csv"


def test_seed_files_parse_with_zero_issues() -> None:
    for path in (NESTLE, DEMO):
        result = parse_variants_csv(path.read_bytes())
        assert result.ok, (path.name, result.issues)


def test_demo_skus_have_demo_prefix_real_ones_do_not() -> None:
    with NESTLE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    with DEMO.open(newline="", encoding="utf-8-sig") as handle:
        demo_rows = list(csv.DictReader(handle))
    assert all(not row["sku_code"].startswith("DEMO-") for row in rows)
    assert all(row["sku_code"].startswith("DEMO-") for row in demo_rows)


def test_cost_price_empty_in_committed_seed_files() -> None:
    for path in (NESTLE, DEMO):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        assert all(row["cost_price"] == "" for row in rows), path.name
    with REVIEW.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle))
    assert not any("cost" in name.lower() or "purchase" in name.lower() for name in header)


def test_real_rows_brand_and_gst() -> None:
    with NESTLE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert all(row["brand_slug"] == "nestle" for row in rows)
    assert all(Decimal(row["gst_rate_percent"]) == Decimal("5") for row in rows)


@pytest.mark.django_db
def test_nestle_seed_creates_40_variants() -> None:
    call_command("seed_catalog")
    assert ProductVariant.objects.count() == 40


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_seed_creates_88_variants_total() -> None:
    call_command("seed_catalog", "--demo")
    assert ProductVariant.objects.count() == 88


@pytest.mark.django_db
def test_idempotent_second_run() -> None:
    call_command("seed_catalog")
    before = {
        "brand": Brand.objects.count(),
        "category": Category.objects.count(),
        "product": Product.objects.count(),
        "variant": ProductVariant.objects.count(),
        "audit": AuditLog.objects.count(),
    }
    out = StringIO()
    call_command("seed_catalog", stdout=out)
    after = {
        "brand": Brand.objects.count(),
        "category": Category.objects.count(),
        "product": Product.objects.count(),
        "variant": ProductVariant.objects.count(),
        "audit": AuditLog.objects.count(),
    }
    assert before == after
    assert "unchanged 40" in out.getvalue()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_demo_refused_when_debug_false() -> None:
    with pytest.raises(CommandError):
        call_command("seed_catalog", "--demo")
    assert ProductVariant.objects.count() == 0
    assert Brand.objects.count() == 0
    assert Category.objects.count() == 0


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_demo_stock_refused_when_debug_false() -> None:
    with pytest.raises(CommandError):
        call_command("seed_catalog", "--demo", "--demo-stock")
    assert ProductVariant.objects.count() == 0
    assert Brand.objects.count() == 0
    assert Category.objects.count() == 0


@pytest.mark.django_db
def test_demo_stock_without_demo_refused() -> None:
    with pytest.raises(CommandError):
        call_command("seed_catalog", "--demo-stock")
    assert ProductVariant.objects.count() == 0
    assert Brand.objects.count() == 0
    assert Category.objects.count() == 0


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_stock_adds_stock_once() -> None:
    call_command("seed_catalog", "--demo", "--demo-stock")
    assert InventoryMovement.objects.count() == 48
    assert all(
        movement.variant.sku_code.startswith("DEMO-")
        for movement in InventoryMovement.objects.select_related("variant")
    )
    call_command("seed_catalog", "--demo", "--demo-stock")
    assert InventoryMovement.objects.count() == 48


@pytest.mark.django_db
def test_rejected_seed_file_exits_with_error(monkeypatch, tmp_path) -> None:
    import apps.catalog.management.commands.seed_catalog as cmd_module

    bad_dir = tmp_path / "seed_data"
    bad_dir.mkdir()
    header = (SEED_DIR / "nestle.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    (bad_dir / "nestle.csv").write_text(
        header
        + "\nBADSKU,,p,Name,,nestle,Nestle,c,Category,FALSE,VEG,,,,PACKET,1,FALSE,1,,"
        + "1905,5.00,abc,abc,,,,,,,,TRUE,TRUE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cmd_module, "SEED_DIR", bad_dir)

    out = StringIO()
    with pytest.raises(CommandError):
        call_command("seed_catalog", stdout=out)
    assert "nestle.csv" in out.getvalue()
    assert ProductVariant.objects.count() == 0


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_no_stock_without_flag() -> None:
    call_command("seed_catalog", "--demo")
    assert InventoryMovement.objects.count() == 0
