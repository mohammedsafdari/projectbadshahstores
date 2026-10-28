"""Tests for the "Count stock" page on the Stock screen."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import InventoryMovement
from apps.inventory.services import get_on_hand
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:inventory_stockitem_changelist"
VIEW_STOCK = "view_stockitem"
ADD_MOVEMENT = "add_inventorymovement"


def _client_for(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _superuser() -> User:
    return User.objects.create_superuser(phone="+919876543210", password="ok")


def _staff(*codenames: str) -> User:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def _url(variant: ProductVariant) -> str:
    return reverse("admin:inventory_stockitem_count", args=[variant.pk])


def _stocked_variant(quantity: str = "10") -> ProductVariant:
    variant = ProductVariantFactory()
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def _last_movement() -> InventoryMovement:
    return InventoryMovement.objects.order_by("-id").first()  # type: ignore[return-value]


def _count(variant: ProductVariant, counted: str, reason: str = "Monthly count") -> object:
    data = {"counted_quantity": counted, "reason": reason}
    return _client_for(_superuser()).post(_url(variant), data=data, follow=True)


# --- Access ---------------------------------------------------------------


def test_page_200_for_superuser_and_shows_the_variant() -> None:
    variant = _stocked_variant()
    response = _client_for(_superuser()).get(_url(variant))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert variant.sku_code in content
    assert 'name="counted_quantity"' in content


def test_page_200_for_staff_with_view_and_add_permissions() -> None:
    variant = _stocked_variant()
    client = _client_for(_staff(VIEW_STOCK, ADD_MOVEMENT))
    assert client.get(_url(variant)).status_code == 200


def test_page_403_for_staff_with_only_view_permission() -> None:
    variant = _stocked_variant()
    assert _client_for(_staff(VIEW_STOCK)).get(_url(variant)).status_code == 403


def test_page_403_for_staff_with_only_add_movement_permission() -> None:
    variant = _stocked_variant()
    assert _client_for(_staff(ADD_MOVEMENT)).get(_url(variant)).status_code == 403


def test_page_404_for_unknown_variant() -> None:
    url = reverse("admin:inventory_stockitem_count", args=[999999])
    assert _client_for(_superuser()).get(url).status_code == 404


def test_page_redirects_anonymous_user_to_login() -> None:
    response = Client().get(_url(_stocked_variant()))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_post_by_view_only_staff_is_403_and_writes_nothing() -> None:
    variant = _stocked_variant()
    data = {"counted_quantity": "3", "reason": "test"}
    response = _client_for(_staff(VIEW_STOCK)).post(_url(variant), data=data)
    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 1


def test_post_without_csrf_token_is_rejected() -> None:
    variant = _stocked_variant()
    client = Client(enforce_csrf_checks=True)
    client.force_login(_superuser())
    data = {"counted_quantity": "3", "reason": "test"}
    assert client.post(_url(variant), data=data).status_code == 403
    assert InventoryMovement.objects.count() == 1


# --- Counting -------------------------------------------------------------


def test_count_below_on_hand_writes_a_negative_adjustment() -> None:
    variant = _stocked_variant("10")
    user = _superuser()
    response = _client_for(user).post(
        _url(variant), data={"counted_quantity": "7", "reason": "Monthly count"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)
    movement = _last_movement()
    assert movement.movement_type == InventoryMovement.MovementType.ADJUSTMENT
    assert movement.quantity_change == Decimal("-3.000")
    assert movement.reason == "Monthly count"
    assert movement.created_by_id == user.pk
    assert get_on_hand(variant) == Decimal("7.000")


def test_count_above_on_hand_writes_a_positive_adjustment() -> None:
    variant = _stocked_variant("10")
    _count(variant, "12")
    assert _last_movement().quantity_change == Decimal("2.000")
    assert get_on_hand(variant) == Decimal("12.000")


def test_count_equal_to_on_hand_writes_nothing() -> None:
    variant = _stocked_variant("10")
    _count(variant, "10")
    assert InventoryMovement.objects.count() == 1
    assert get_on_hand(variant) == Decimal("10.000")


def test_count_of_zero_empties_the_stock() -> None:
    variant = _stocked_variant("10")
    _count(variant, "0")
    assert get_on_hand(variant) == Decimal("0.000")


def test_count_of_a_variant_with_no_movements_adds_the_counted_stock() -> None:
    variant = ProductVariantFactory()
    _count(variant, "5")
    assert _last_movement().quantity_change == Decimal("5.000")
    assert get_on_hand(variant) == Decimal("5.000")


def test_count_loose_variant_accepts_a_fractional_quantity() -> None:
    variant = ProductVariantFactory(is_sold_loose=True, unit=ProductVariant.Unit.KG)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("4"))
    _count(variant, "3.750")
    assert get_on_hand(variant) == Decimal("3.750")


# --- Messages -------------------------------------------------------------


def test_count_with_a_difference_shows_a_message_with_the_change() -> None:
    variant = _stocked_variant("10")
    response = _count(variant, "7")
    content = response.content.decode()  # type: ignore[attr-defined]
    assert f"Counted {variant.sku_code}: on-hand is now 7 (-3)." in content


def test_count_that_increases_stock_shows_a_plus_sign() -> None:
    variant = _stocked_variant("10")
    response = _count(variant, "12")
    content = response.content.decode()  # type: ignore[attr-defined]
    assert f"Counted {variant.sku_code}: on-hand is now 12 (+2)." in content


def test_count_that_matches_shows_a_message_saying_nothing_changed() -> None:
    variant = _stocked_variant("10")
    response = _count(variant, "10")
    content = response.content.decode()  # type: ignore[attr-defined]
    assert f"Counted {variant.sku_code}: the count matches on-hand, nothing changed." in content


# --- Errors ---------------------------------------------------------------


def test_count_without_a_reason_shows_an_error_and_writes_nothing() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url(variant), data={"counted_quantity": "7", "reason": ""}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["reason"]
    assert get_on_hand(variant) == Decimal("10.000")


@pytest.mark.parametrize("counted", ["-1", "abc", ""])
def test_count_with_a_bad_quantity_shows_an_error_and_writes_nothing(counted: str) -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url(variant), data={"counted_quantity": counted, "reason": "Bad"}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["counted_quantity"]
    assert InventoryMovement.objects.count() == 1


def test_count_packet_variant_rejects_a_fractional_quantity() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url(variant), data={"counted_quantity": "6.5", "reason": "Half"}
    )
    assert response.status_code == 200
    assert "Packet variants accept whole numbers only." in response.content.decode()
    assert get_on_hand(variant) == Decimal("10.000")


# --- Link -----------------------------------------------------------------


def test_stock_screen_shows_the_count_link_to_a_user_who_may_write_stock() -> None:
    variant = _stocked_variant()
    response = _client_for(_superuser()).get(reverse(CHANGELIST))
    assert _url(variant) in response.content.decode("utf-8", "replace")


def test_stock_screen_hides_the_count_link_from_view_only_staff() -> None:
    variant = _stocked_variant()
    response = _client_for(_staff(VIEW_STOCK)).get(reverse(CHANGELIST))
    assert _url(variant) not in response.content.decode("utf-8", "replace")
