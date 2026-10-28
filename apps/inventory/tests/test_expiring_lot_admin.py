"""Tests for the Expiring lots admin screen (ExpiringLot proxy)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.admin import ExpiringLotAdmin
from apps.inventory.models import ExpiringLot, InventoryMovement
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:inventory_expiringlot_changelist"


def _admin() -> ExpiringLotAdmin:
    return ExpiringLotAdmin(ExpiringLot, admin.site)


def _superuser() -> User:
    return User.objects.create_superuser(phone="+919876543210", password="ok")


def _client_for(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _staff(*codenames: str) -> User:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def _request() -> object:
    request = RequestFactory().get("/admin/inventory/expiringlot/")
    request.user = _superuser()
    return request


def _today() -> date:
    return timezone.localdate()


def _lot(
    days_from_today: int | None,
    *,
    variant: ProductVariant | None = None,
    quantity: str = "10",
    batch: str = "",
) -> InventoryMovement:
    """A received lot that expires ``days_from_today`` days from today (None: no expiry)."""
    expiry = None if days_from_today is None else _today() + timedelta(days=days_from_today)
    return InventoryMovementFactory(
        variant=variant or ProductVariantFactory(),
        quantity_change=Decimal(quantity),
        expiry_date=expiry,
        batch_number=batch,
    )


def _damage(variant: ProductVariant, quantity: str) -> None:
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.DAMAGE,
        quantity_change=-Decimal(quantity),
    )


def _listed(client: Client, **params: str) -> list[int]:
    """Ids of the lots on the screen, in the order shown."""
    response = client.get(reverse(CHANGELIST), data=params)
    assert response.status_code == 200
    return [row.pk for row in response.context["cl"].result_list]


# --- Access ---------------------------------------------------------------


def test_has_add_permission_false_for_superuser() -> None:
    assert _admin().has_add_permission(_request()) is False  # type: ignore[arg-type]


def test_has_change_permission_false_for_superuser() -> None:
    assert _admin().has_change_permission(_request()) is False  # type: ignore[arg-type]


def test_has_delete_permission_false_for_superuser() -> None:
    assert _admin().has_delete_permission(_request()) is False  # type: ignore[arg-type]


def test_changelist_200_for_superuser_and_shows_the_batch() -> None:
    _lot(10, batch="B-123")
    response = _client_for(_superuser()).get(reverse(CHANGELIST))
    assert response.status_code == 200
    assert "B-123" in response.content.decode("utf-8", "replace")


def test_staff_without_permissions_gets_403() -> None:
    assert _client_for(_staff()).get(reverse(CHANGELIST)).status_code == 403


def test_staff_with_only_view_permission_can_read() -> None:
    client = _client_for(_staff("view_expiringlot"))
    assert client.get(reverse(CHANGELIST)).status_code == 200


def test_add_page_403_for_superuser() -> None:
    response = _client_for(_superuser()).get(reverse("admin:inventory_expiringlot_add"))
    assert response.status_code == 403


def test_post_to_change_page_403_and_batch_unchanged() -> None:
    lot = _lot(10, batch="B-1")
    url = reverse("admin:inventory_expiringlot_change", args=[lot.pk])
    response = _client_for(_superuser()).post(url, data={"batch_number": "HACKED"})
    assert response.status_code == 403
    lot.refresh_from_db()
    assert lot.batch_number == "B-1"


# --- Which lots are listed ------------------------------------------------


def test_lot_with_an_expiry_date_and_stock_is_listed() -> None:
    lot = _lot(10)
    assert lot.pk in _listed(_client_for(_superuser()))


def test_lot_without_an_expiry_date_is_not_listed() -> None:
    lot = _lot(None)
    assert lot.pk not in _listed(_client_for(_superuser()))


def test_lot_is_not_listed_when_its_variant_has_no_stock_left() -> None:
    lot = _lot(10, quantity="10")
    _damage(lot.variant, "10")
    assert lot.pk not in _listed(_client_for(_superuser()))


def test_lot_is_still_listed_when_some_stock_is_left() -> None:
    lot = _lot(10, quantity="10")
    _damage(lot.variant, "4")
    assert lot.pk in _listed(_client_for(_superuser()))


def test_expired_lot_is_listed_while_stock_remains() -> None:
    lot = _lot(-5)
    assert lot.pk in _listed(_client_for(_superuser()))


def test_every_lot_of_a_variant_is_listed() -> None:
    variant = ProductVariantFactory()
    first = _lot(10, variant=variant)
    second = _lot(40, variant=variant)
    listed = _listed(_client_for(_superuser()))
    assert {first.pk, second.pk} <= set(listed)


def test_lots_are_listed_soonest_expiry_first() -> None:
    later = _lot(30)
    sooner = _lot(5)
    expired = _lot(-2)
    assert _listed(_client_for(_superuser())) == [expired.pk, sooner.pk, later.pk]


def test_variant_on_hand_column_is_the_total_across_all_lots() -> None:
    variant = ProductVariantFactory()
    _lot(10, variant=variant, quantity="10")
    _lot(40, variant=variant, quantity="5")
    _damage(variant, "3")
    response = _client_for(_superuser()).get(reverse(CHANGELIST))
    rows = response.context["cl"].result_list
    assert [row.variant_on_hand for row in rows] == [Decimal("12.000")] * 2


# --- Expiry filter --------------------------------------------------------


def test_expired_filter_lists_yesterday_but_not_today() -> None:
    yesterday = _lot(-1)
    today = _lot(0)
    listed = _listed(_client_for(_superuser()), expiry="expired")
    assert yesterday.pk in listed
    assert today.pk not in listed


def test_within_7_days_filter_includes_today_and_day_7_but_not_day_8() -> None:
    today = _lot(0)
    day_7 = _lot(7)
    day_8 = _lot(8)
    listed = _listed(_client_for(_superuser()), expiry="7")
    assert today.pk in listed
    assert day_7.pk in listed
    assert day_8.pk not in listed


def test_within_7_days_filter_leaves_out_expired_lots() -> None:
    yesterday = _lot(-1)
    assert yesterday.pk not in _listed(_client_for(_superuser()), expiry="7")


def test_within_30_days_filter_includes_day_30_but_not_day_31() -> None:
    day_30 = _lot(30)
    day_31 = _lot(31)
    listed = _listed(_client_for(_superuser()), expiry="30")
    assert day_30.pk in listed
    assert day_31.pk not in listed


def test_within_60_days_filter_includes_day_60_but_not_day_61() -> None:
    day_60 = _lot(60)
    day_61 = _lot(61)
    listed = _listed(_client_for(_superuser()), expiry="60")
    assert day_60.pk in listed
    assert day_61.pk not in listed


# --- Search and columns ---------------------------------------------------


def test_search_by_batch_number_finds_only_that_lot() -> None:
    wanted = _lot(10, batch="LOT-AAA")
    other = _lot(10, batch="LOT-BBB")
    listed = _listed(_client_for(_superuser()), q="LOT-AAA")
    assert wanted.pk in listed
    assert other.pk not in listed


def test_days_left_text() -> None:
    expired_3 = _lot(-3)
    expired_1 = _lot(-1)
    today = _lot(0)
    tomorrow = _lot(1)
    in_5 = _lot(5)
    texts = {
        lot.pk: _admin().days_left(ExpiringLot.objects.get(pk=lot.pk))
        for lot in (expired_3, expired_1, today, tomorrow, in_5)
    }
    assert texts == {
        expired_3.pk: "expired 3 days ago",
        expired_1.pk: "expired 1 day ago",
        today.pk: "today",
        tomorrow.pk: "1 day",
        in_5.pk: "5 days",
    }


def test_received_quantity_drops_trailing_zeros() -> None:
    lot = _lot(10, quantity="12")
    assert _admin().received_quantity(ExpiringLot.objects.get(pk=lot.pk)) == "12"


def _changelist_query_count(client: Client) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse(CHANGELIST))
    assert response.status_code == 200
    return len(queries)


def test_changelist_query_count_does_not_grow_with_rows() -> None:
    client = _client_for(_superuser())
    for days in (5, 10):
        _lot(days)
    few_rows = _changelist_query_count(client)
    for days in (15, 20, 25, 30):
        _lot(days)
    many_rows = _changelist_query_count(client)
    assert many_rows == few_rows
