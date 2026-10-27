"""Tests for the Stock admin screen (StockItem proxy)."""

from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.admin import StockItemAdmin
from apps.inventory.models import InventoryMovement, StockItem
from apps.inventory.services import get_on_hand
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:inventory_stockitem_changelist"


def _admin() -> StockItemAdmin:
    return StockItemAdmin(StockItem, admin.site)


def _superuser() -> User:
    return User.objects.create_superuser(phone="+919876543210", password="ok")


def _superuser_client() -> Client:
    client = Client()
    client.force_login(_superuser())
    return client


def _request_with_user(user: User) -> object:
    request = RequestFactory().get("/admin/inventory/stockitem/")
    request.user = user
    return request


def _staff_client(*, codenames: tuple[str, ...] = ()) -> Client:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.force_login(user)
    return client


def _variant_with_stock(
    quantity: Decimal | None, *, threshold: Decimal | None = None
) -> ProductVariant:
    """A variant with one purchase of ``quantity`` (no movement when it is None)."""
    variant = ProductVariantFactory(low_stock_threshold=threshold)
    if quantity is not None:
        InventoryMovementFactory(variant=variant, quantity_change=quantity)
    return variant


def _listed_ids(response: object) -> set[int]:
    return {row.pk for row in response.context["cl"].result_list}  # type: ignore[attr-defined]


def _stock_row(variant: ProductVariant) -> StockItem:
    """The variant as the Stock screen loads it, with the ``on_hand`` annotation."""
    request = RequestFactory().get("/admin/inventory/stockitem/")
    return _admin().get_queryset(request).get(pk=variant.pk)


def test_has_add_permission_false_for_superuser() -> None:
    request = _request_with_user(_superuser())
    assert _admin().has_add_permission(request) is False  # type: ignore[arg-type]


def test_has_change_permission_false_for_superuser() -> None:
    request = _request_with_user(_superuser())
    assert _admin().has_change_permission(request) is False  # type: ignore[arg-type]


def test_has_delete_permission_false_for_superuser() -> None:
    request = _request_with_user(_superuser())
    assert _admin().has_delete_permission(request) is False  # type: ignore[arg-type]


def test_changelist_200_for_superuser_contains_variant_sku() -> None:
    variant = _variant_with_stock(Decimal("4"))
    response = _superuser_client().get(reverse(CHANGELIST))
    assert response.status_code == 200
    assert variant.sku_code in response.content.decode("utf-8", "replace")


def test_staff_without_permissions_gets_403_on_changelist() -> None:
    response = _staff_client().get(reverse(CHANGELIST))
    assert response.status_code == 403


def test_staff_with_only_view_permission_can_read_changelist() -> None:
    response = _staff_client(codenames=("view_stockitem",)).get(reverse(CHANGELIST))
    assert response.status_code == 200


def test_on_hand_annotation_matches_get_on_hand() -> None:
    variant = _variant_with_stock(Decimal("10"))
    InventoryMovementFactory(
        variant=variant,
        movement_type=InventoryMovement.MovementType.DAMAGE,
        quantity_change=-Decimal("3.000"),
    )
    response = _superuser_client().get(reverse(CHANGELIST))
    row = next(r for r in response.context["cl"].result_list if r.pk == variant.pk)
    assert row.on_hand == Decimal("7.000")
    assert row.on_hand == get_on_hand(variant)


def test_variant_without_movements_has_zero_on_hand() -> None:
    variant = _variant_with_stock(None)
    response = _superuser_client().get(reverse(CHANGELIST))
    row = next(r for r in response.context["cl"].result_list if r.pk == variant.pk)
    assert row.on_hand == Decimal("0")


def test_on_hand_display_drops_trailing_zeros() -> None:
    stocked = _variant_with_stock(Decimal("7"))
    empty = _variant_with_stock(None)
    assert _admin().on_hand_display(_stock_row(stocked)) == "7"
    assert _admin().on_hand_display(_stock_row(empty)) == "0"


def test_low_filter_lists_stock_equal_to_threshold() -> None:
    variant = _variant_with_stock(Decimal("5"), threshold=Decimal("5"))
    response = _superuser_client().get(reverse(CHANGELIST), data={"stock": "low"})
    assert response.status_code == 200
    assert variant.pk in _listed_ids(response)


def test_low_filter_omits_stock_above_threshold() -> None:
    variant = _variant_with_stock(Decimal("6"), threshold=Decimal("5"))
    response = _superuser_client().get(reverse(CHANGELIST), data={"stock": "low"})
    assert variant.pk not in _listed_ids(response)


def test_low_filter_omits_variant_without_threshold() -> None:
    variant = _variant_with_stock(None, threshold=None)
    response = _superuser_client().get(reverse(CHANGELIST), data={"stock": "low"})
    assert variant.pk not in _listed_ids(response)


def test_is_low_stock_true_when_on_hand_equals_threshold() -> None:
    variant = _variant_with_stock(Decimal("5"), threshold=Decimal("5"))
    assert _admin().is_low_stock(_stock_row(variant)) is True


def test_is_low_stock_false_when_on_hand_above_threshold() -> None:
    variant = _variant_with_stock(Decimal("6"), threshold=Decimal("5"))
    assert _admin().is_low_stock(_stock_row(variant)) is False


def test_is_low_stock_false_when_no_threshold() -> None:
    variant = _variant_with_stock(None, threshold=None)
    assert _admin().is_low_stock(_stock_row(variant)) is False


def test_add_page_403_for_superuser() -> None:
    response = _superuser_client().get(reverse("admin:inventory_stockitem_add"))
    assert response.status_code == 403


def test_post_to_change_page_403_and_price_unchanged() -> None:
    variant = _variant_with_stock(Decimal("1"))
    url = reverse("admin:inventory_stockitem_change", args=[variant.pk])
    response = _superuser_client().post(url, data={"selling_price": "1.00"})
    assert response.status_code == 403
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("95.00")


def _changelist_query_count(client: Client) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse(CHANGELIST))
    assert response.status_code == 200
    return len(queries)


def test_changelist_query_count_does_not_grow_with_rows() -> None:
    client = _superuser_client()
    for _ in range(2):
        _variant_with_stock(Decimal("3"), threshold=Decimal("5"))
    few_rows = _changelist_query_count(client)
    for _ in range(4):
        _variant_with_stock(Decimal("3"), threshold=Decimal("5"))
    many_rows = _changelist_query_count(client)
    assert many_rows == few_rows
