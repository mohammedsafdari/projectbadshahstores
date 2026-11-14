"""The private cost price upload: plan, apply, refuse (D85)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.catalog.models import ProductVariant
from apps.core.models import AuditLog
from apps.orders.tests.staff_helpers import staff
from apps.reports.cost_prices import (
    COST_PRICE_UPDATE_ACTION,
    UNCHANGED,
    UPDATE,
    CostPriceError,
    CostPriceRejected,
    apply_cost_prices,
    parse_cost_csv,
    plan_cost_prices,
)
from apps.reports.tests.helpers import make_variant, staff_client

pytestmark = pytest.mark.django_db

COST_URL = "reports_cost_prices"


def _cost_file(*rows: tuple[str, str]) -> bytes:
    """A two-column file: the SKU and the cost the upload asks for."""
    text = "sku_code,cost_price\n" + "".join(f"{sku},{cost}\n" for sku, cost in rows)
    return text.encode()


def test_the_plan_shows_an_update_and_an_unchanged_row() -> None:
    changed = make_variant(name="Changed", cost="50.00")
    same = make_variant(name="Same", cost="40.00")
    raw = _cost_file((changed.sku_code, "60.00"), (same.sku_code, "40.00"))

    plan = plan_cost_prices(raw, actor=staff())

    assert plan.ok
    actions = {row.sku_code: row.action for row in plan.rows}
    assert actions[changed.sku_code] == UPDATE
    assert actions[same.sku_code] == UNCHANGED
    assert plan.counts() == {UPDATE: 1, UNCHANGED: 1}


def test_applying_writes_the_cost_its_timestamp_and_one_audit_row() -> None:
    variant = make_variant(name="Atta", cost="50.00")
    before = variant.updated_at
    owner = staff()
    raw = _cost_file((variant.sku_code, "60.00"))

    result = apply_cost_prices(raw, actor=owner, filename="costs.private.csv")

    variant.refresh_from_db()
    assert result.updated == 1
    assert variant.cost_price == Decimal("60.00")
    assert variant.updated_at > before
    log = AuditLog.objects.get(action=COST_PRICE_UPDATE_ACTION)
    assert log.changes == {"cost_price": ["50.00", "60.00"]}
    assert log.actor_id == owner.pk
    assert log.target_type == "catalog.productvariant"
    assert "costs.private.csv" in log.note


def test_only_the_cost_price_changes() -> None:
    variant = make_variant(name="Atta", price="95.00", cost="50.00", stock="7.000")
    sku = variant.sku_code
    raw = _cost_file((sku, "60.00"))

    apply_cost_prices(raw, actor=staff(), filename="costs.private.csv")

    variant.refresh_from_db()
    assert variant.cost_price == Decimal("60.00")
    assert variant.sku_code == sku
    assert variant.selling_price == Decimal("95.00")
    assert variant.mrp == Decimal("190.00")
    assert variant.gst_rate_percent == Decimal("5.00")
    assert variant.product.name_en == "Atta"


def test_an_unknown_sku_is_refused_and_nothing_is_written() -> None:
    raw = _cost_file(("NOPE-1", "10.00"))

    plan = plan_cost_prices(raw, actor=staff())

    assert plan.ok is False
    assert "Unknown SKU" in plan.issues[0].message
    with pytest.raises(CostPriceRejected):
        apply_cost_prices(raw, actor=staff(), filename="costs.private.csv")
    assert ProductVariant.objects.count() == 0


def test_a_negative_cost_is_refused() -> None:
    variant = make_variant(cost="50.00")

    plan = plan_cost_prices(_cost_file((variant.sku_code, "-5.00")), actor=staff())

    assert plan.ok is False
    assert "must be at least 0" in plan.issues[0].message
    with pytest.raises(CostPriceRejected):
        apply_cost_prices(
            _cost_file((variant.sku_code, "-5.00")),
            actor=staff(),
            filename="costs.private.csv",
        )
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("50.00")


def test_a_cost_that_is_not_a_number_is_refused() -> None:
    variant = make_variant(cost="50.00")

    plan = plan_cost_prices(_cost_file((variant.sku_code, "eighty")), actor=staff())

    assert plan.ok is False
    assert "is not a number" in plan.issues[0].message


def test_a_cost_above_the_selling_price_is_a_warning_not_a_refusal() -> None:
    variant = make_variant(price="95.00", cost="50.00")
    raw = _cost_file((variant.sku_code, "120.00"))

    plan = plan_cost_prices(raw, actor=staff())

    assert plan.ok is True
    assert len(plan.warned()) == 1
    assert "above the selling price" in plan.warned()[0].warning
    apply_cost_prices(raw, actor=staff(), filename="costs.private.csv")
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("120.00")


def test_a_blank_cost_leaves_the_variant_as_it_is() -> None:
    variant = make_variant(cost="50.00")

    plan = plan_cost_prices(_cost_file((variant.sku_code, "")), actor=staff())

    assert plan.ok is True
    assert plan.rows[0].action == UNCHANGED
    result = apply_cost_prices(
        _cost_file((variant.sku_code, "")), actor=staff(), filename="costs.private.csv"
    )
    assert result.updated == 0
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("50.00")


def test_the_formula_guard_is_removed_before_a_sku_is_matched() -> None:
    parsed = parse_cost_csv(_cost_file(("'=1+1", "10.00")))
    plan = plan_cost_prices(_cost_file(("'=1+1", "10.00")), actor=staff())

    # One leading quote in front of a formula starter is the export's guard.
    assert parsed.rows[0].sku_code == "=1+1"
    assert plan.ok is False
    assert plan.issues[0].message.startswith("Unknown SKU")


def test_a_duplicate_sku_is_refused() -> None:
    variant = make_variant(cost="50.00")
    raw = _cost_file((variant.sku_code, "60.00"), (variant.sku_code, "70.00"))

    plan = plan_cost_prices(raw, actor=staff())

    assert plan.ok is False
    assert "already appears on line" in plan.issues[0].message


def test_a_file_without_a_cost_column_is_refused() -> None:
    plan = plan_cost_prices(b"sku_code,price\nA,10.00\n", actor=staff())

    assert plan.ok is False
    assert "Missing columns" in plan.issues[0].message


def test_the_preview_and_the_apply_refuse_a_changed_variant() -> None:
    variant = make_variant(cost="50.00")
    raw = _cost_file((variant.sku_code, "60.00"))
    plan = plan_cost_prices(raw, actor=staff())

    # Somebody else edits the variant between the preview and the click.
    variant.selling_price = Decimal("96.00")
    variant.save(update_fields=["selling_price", "updated_at"])

    with pytest.raises(CostPriceRejected):
        apply_cost_prices(
            raw,
            actor=staff(),
            filename="costs.private.csv",
            expected_fingerprint=plan.fingerprint(),
        )


def test_a_staff_member_without_the_capability_cannot_plan_or_apply() -> None:
    variant = make_variant(cost="50.00")
    raw = _cost_file((variant.sku_code, "60.00"))
    packer = staff("PACKER")

    with pytest.raises(CostPriceError):
        plan_cost_prices(raw, actor=packer)
    with pytest.raises(CostPriceError):
        apply_cost_prices(raw, actor=packer, filename="costs.private.csv")


def test_the_page_needs_the_cost_capability() -> None:
    assert staff_client("PACKER").get(reverse(COST_URL)).status_code == 403


def test_the_upload_page_previews_then_applies() -> None:
    variant = make_variant(cost="50.00")
    client = staff_client()

    raw = _cost_file((variant.sku_code, "60.00"))
    upload = client.post(
        reverse(COST_URL),
        {"csv_file": SimpleUploadedFile("costs.private.csv", raw)},
    )
    assert upload.status_code == 200
    assert b"What this file would do" in upload.content

    applied = client.post(reverse(COST_URL), {"apply": "1"}, follow=True)

    assert applied.status_code == 200
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("60.00")
    assert AuditLog.objects.filter(action=COST_PRICE_UPDATE_ACTION).count() == 1


def test_the_apply_without_a_preview_writes_nothing() -> None:
    variant = make_variant(cost="50.00")
    response = staff_client().post(reverse(COST_URL), {"apply": "1"}, follow=True)

    assert response.status_code == 200
    variant.refresh_from_db()
    assert variant.cost_price == Decimal("50.00")
    assert AuditLog.objects.filter(action=COST_PRICE_UPDATE_ACTION).count() == 0


def test_every_committed_seed_file_leaves_the_cost_price_blank() -> None:
    seed_folder = Path(settings.BASE_DIR) / "apps" / "catalog" / "seed_data"
    checked = 0
    for path in sorted(seed_folder.glob("*.csv")):
        text = path.read_text(encoding="utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or "cost_price" not in rows[0]:
            continue
        for row in rows:
            assert row["cost_price"].strip() == "", f"{path.name} commits a cost price"
            checked += 1

    assert checked > 0
