"""The daily stock email: low stock, expiry, the text and the job (D89).

The expiry figure is an estimate, so several tests build the stock by hand:
receive the lots, then adjust the on-hand total, so the allocation of what is
left can be checked.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.core.models import SHOP_SETTINGS_CACHE_KEY
from apps.inventory.alerts import (
    ExpiryRow,
    LowStockRow,
    expiry_rows,
    low_stock_rows,
)
from apps.inventory.services import adjust_stock, receive_stock
from apps.inventory.tests.factories import StockReservationFactory
from apps.notifications.models import NotificationLog
from apps.notifications.stock_digest import EVENT_STOCK_DIGEST, build_stock_digest
from apps.notifications.tasks import send_stock_digest
from apps.orders.tests.checkout_helpers import set_settings

pytestmark = pytest.mark.django_db

SHOP_EMAIL = "shop@example.com"
APPLY = "apps.notifications.tasks.retry_notification.apply_async"


@pytest.fixture(autouse=True)
def _clear_shop_settings_cache():
    """The settings row is cached, and the cache is not rolled back."""
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    yield
    cache.delete(SHOP_SETTINGS_CACHE_KEY)


def _variant(
    *,
    name: str = "Atta",
    threshold: str | None = None,
    stock: str | None = "10.000",
    is_active: bool = True,
    product_active: bool = True,
):
    """An active variant in stock, optionally with a low stock threshold.

    ``stock=None`` leaves the shelf empty, so an expiry test can build the
    on-hand figure from its own lots alone.
    """
    product = ProductFactory(name_en=name, is_active=product_active)
    variant = ProductVariantFactory(
        product=product,
        is_active=is_active,
        low_stock_threshold=None if threshold is None else Decimal(threshold),
    )
    if stock is not None:
        receive_stock(variant=variant, quantity=Decimal(stock))
    return variant


def _lot(variant, quantity: str, *, batch: str, days: int, today):
    """Receive one lot that expires ``days`` from today (negative is past)."""
    receive_stock(
        variant=variant,
        quantity=Decimal(quantity),
        batch_number=batch,
        expiry_date=today + timedelta(days=days),
    )


def _low_row(name: str = "Atta", *, available: str = "1.000") -> LowStockRow:
    """One low stock row, built without touching the database."""
    return LowStockRow(
        product=name,
        variant="1 kg",
        sku=f"SKU-{name}",
        unit="KG",
        available=Decimal(available),
        threshold=Decimal("2.000"),
    )


def _expiry_row(name: str = "Atta", *, days: int = 2) -> ExpiryRow:
    """One expiry row, built without touching the database."""
    return ExpiryRow(
        product=name,
        variant="1 kg",
        sku=f"SKU-{name}",
        batch_number=f"B-{name}",
        expiry_date=timezone.localdate() + timedelta(days=days),
        remaining=Decimal("2.000"),
    )


# ---------------------------------------------------------------------------
# Low stock
# ---------------------------------------------------------------------------
def test_low_stock_lists_variant_at_threshold() -> None:
    _variant(threshold="2.000", stock="2.000")

    rows = low_stock_rows()

    assert len(rows) == 1
    assert rows[0].available == Decimal("2.000")
    assert rows[0].threshold == Decimal("2.000")


def test_low_stock_skips_blank_threshold() -> None:
    # Nothing on the shelf and still no alert: a blank threshold is not zero.
    _variant(threshold=None, stock=None)

    assert low_stock_rows() == []


def test_low_stock_skips_above_threshold() -> None:
    _variant(threshold="2.000", stock="3.000")

    assert low_stock_rows() == []


def test_low_stock_uses_available_not_on_hand() -> None:
    variant = _variant(threshold="2.000", stock="5.000")
    # A fulfilment hold takes four away, so only one is orderable.
    StockReservationFactory(variant=variant, quantity=Decimal("4.000"))

    rows = low_stock_rows()

    assert len(rows) == 1
    assert rows[0].available == Decimal("1.000")


def test_low_stock_skips_inactive_variant() -> None:
    _variant(threshold="2.000", stock="1.000", is_active=False)

    assert low_stock_rows() == []


def test_low_stock_skips_a_variant_of_an_inactive_product() -> None:
    _variant(threshold="2.000", stock="1.000", product_active=False)

    assert low_stock_rows() == []


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
def test_expiry_lists_lot_inside_window() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "4.000", batch="B1", days=7, today=today)

    expired, expiring = expiry_rows(today)

    assert expired == []
    assert [row.batch_number for row in expiring] == ["B1"]
    assert expiring[0].remaining == Decimal("4.000")


def test_expiry_skips_lot_outside_window() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "4.000", batch="B1", days=8, today=today)

    expired, expiring = expiry_rows(today)

    assert expired == []
    assert expiring == []


def test_expired_lot_goes_in_expired_list() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "4.000", batch="B1", days=-1, today=today)

    expired, expiring = expiry_rows(today)

    assert [row.batch_number for row in expired] == ["B1"]
    assert expiring == []


def test_expiry_skips_lot_with_zero_estimated_remaining() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "3.000", batch="OLD", days=2, today=today)
    _lot(variant, "5.000", batch="NEW", days=3, today=today)
    # Only three are on the shelf, so the newest lot takes all of them.
    adjust_stock(variant=variant, quantity_change=Decimal("-5"), reason="sold at counter")

    expired, expiring = expiry_rows(today)

    assert expired == []
    assert [row.batch_number for row in expiring] == ["NEW"]
    assert expiring[0].remaining == Decimal("3.000")


def test_expiry_estimate_gives_stock_to_newest_lot_first() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "3.000", batch="OLD", days=2, today=today)
    _lot(variant, "5.000", batch="NEW", days=3, today=today)
    adjust_stock(variant=variant, quantity_change=Decimal("-4"), reason="sold at counter")

    _expired, expiring = expiry_rows(today)

    assert [row.batch_number for row in expiring] == ["NEW"]
    assert expiring[0].remaining == Decimal("4.000")


def test_expiry_estimate_caps_at_lot_quantity() -> None:
    today = timezone.localdate()
    variant = _variant(stock=None)
    _lot(variant, "3.000", batch="OLD", days=2, today=today)
    _lot(variant, "2.000", batch="NEW", days=3, today=today)

    _expired, expiring = expiry_rows(today)

    by_batch = {row.batch_number: row.remaining for row in expiring}
    # The newer lot keeps its own two, and the older one the other three, even
    # though five were on the shelf for it to take.
    assert by_batch == {"NEW": Decimal("2.000"), "OLD": Decimal("3.000")}


def test_alert_queries_do_not_grow_with_variants() -> None:
    _variant(name="Atta", threshold="5.000", stock="1.000")
    # A warm-up read fills the settings cache, so it cannot count as a query.
    low_stock_rows()

    with CaptureQueriesContext(connection) as small:
        low_stock_rows()
    for index in range(3):
        _variant(name=f"Extra {index}", threshold="5.000", stock="1.000")
    with CaptureQueriesContext(connection) as large:
        rows = low_stock_rows()

    assert len(rows) == 4
    assert len(large) == len(small)


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------
def test_digest_is_none_when_nothing_to_report() -> None:
    assert build_stock_digest([], [], []) is None


def test_digest_subject_counts() -> None:
    digest = build_stock_digest(
        [_low_row("Atta"), _low_row("Rice")],
        [_expiry_row("Milk")],
        [_expiry_row("Curd", days=-1), _expiry_row("Paneer", days=-2)],
    )

    assert digest is not None
    subject, _body = digest
    assert subject == "Badshah Stores stock alert: 2 low, 1 expiring, 2 expired"


def test_digest_cuts_each_section_at_50_with_more_line() -> None:
    low = [_low_row(f"Item {index:02d}") for index in range(55)]

    digest = build_stock_digest(low, [], [])

    assert digest is not None
    _subject, body = digest
    assert "...and 5 more" in body
    # Fifty list lines and no more, whatever the size of the list.
    assert len([line for line in body.splitlines() if line.startswith("- ")]) == 50


def test_digest_says_estimate() -> None:
    digest = build_stock_digest([], [_expiry_row("Milk")], [_expiry_row("Curd", days=-1)])

    assert digest is not None
    _subject, body = digest
    assert "estimate: assumes the oldest purchases are sold first" in body
    assert body.count("estimate: assumes the oldest purchases are sold first") == 2


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------
def test_task_sends_one_email_to_shop_email(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    _variant(name="Atta", threshold="5.000", stock="1.000")
    set_settings(email=SHOP_EMAIL)

    result = send_stock_digest()

    assert result == 1
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [SHOP_EMAIL]
    assert "stock alert" in mailoutbox[0].subject


def test_task_skips_when_email_switch_off(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = False
    _variant(name="Atta", threshold="5.000", stock="1.000")
    set_settings(email=SHOP_EMAIL)

    assert send_stock_digest() == 0
    assert mailoutbox == []


def test_task_skips_when_shop_email_blank(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    _variant(name="Atta", threshold="5.000", stock="1.000")
    set_settings(email="")

    assert send_stock_digest() == 0
    assert mailoutbox == []


def test_task_skips_when_nothing_to_report(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    _variant(name="Atta", threshold="1.000", stock="10.000")
    set_settings(email=SHOP_EMAIL)

    assert send_stock_digest() == 0
    assert mailoutbox == []
    assert NotificationLog.objects.count() == 0


def test_task_writes_one_log_row_with_stock_digest_event(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    _variant(name="Atta", threshold="5.000", stock="1.000")
    set_settings(email=SHOP_EMAIL)

    send_stock_digest()

    log = NotificationLog.objects.get()
    assert log.event == EVENT_STOCK_DIGEST
    assert log.channel == NotificationLog.Channel.EMAIL
    assert log.purpose == NotificationLog.Purpose.STAFF
    assert log.status == NotificationLog.Status.SENT
    assert log.order_id is None
    # The masked address only, never the whole one.
    assert SHOP_EMAIL not in log.recipient_hint


def test_digest_failure_is_logged_and_not_retried(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    _variant(name="Atta", threshold="5.000", stock="1.000")
    set_settings(email=SHOP_EMAIL)

    with (
        patch(
            "apps.notifications.tasks._send_digest_mail",
            side_effect=RuntimeError("smtp down"),
        ),
        patch(APPLY) as apply_async,
    ):
        result = send_stock_digest()

    assert result == 0
    log = NotificationLog.objects.get()
    assert log.status == NotificationLog.Status.FAILED
    # It has no order, so it stays FAILED instead of waiting for a retry.
    apply_async.assert_not_called()


def test_beat_entry_runs_at_8_am(settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["send-stock-digest"]

    assert entry["task"] == "apps.notifications.tasks.send_stock_digest"
    assert entry["schedule"].hour == {8}
    assert entry["schedule"].minute == {0}
    # 8 o'clock means 8 o'clock in the shop's own zone.
    assert settings.CELERY_TIMEZONE == settings.TIME_ZONE == "Asia/Kolkata"
