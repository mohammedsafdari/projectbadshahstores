"""Tests for the "Adjust stock" and "Record damage" pages on the Stock screen."""

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

# Both pages share the same permission rules, so those tests run for each.
both_pages = pytest.mark.parametrize("page", ["adjust", "damage"])


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


def _url(page: str, variant: ProductVariant) -> str:
    return reverse(f"admin:inventory_stockitem_{page}", args=[variant.pk])


def _stocked_variant(quantity: str = "10") -> ProductVariant:
    variant = ProductVariantFactory()
    InventoryMovementFactory(variant=variant, quantity_change=Decimal(quantity))
    return variant


def _last_movement() -> InventoryMovement:
    return InventoryMovement.objects.order_by("-id").first()  # type: ignore[return-value]


# --- Shared rules ---------------------------------------------------------


@both_pages
def test_page_200_for_superuser_and_shows_the_variant(page: str) -> None:
    variant = _stocked_variant()
    response = _client_for(_superuser()).get(_url(page, variant))
    assert response.status_code == 200
    assert variant.sku_code in response.content.decode("utf-8", "replace")


@both_pages
def test_page_200_for_staff_with_view_and_add_permissions(page: str) -> None:
    variant = _stocked_variant()
    client = _client_for(_staff(VIEW_STOCK, ADD_MOVEMENT))
    assert client.get(_url(page, variant)).status_code == 200


@both_pages
def test_page_403_for_staff_with_only_view_permission(page: str) -> None:
    variant = _stocked_variant()
    assert _client_for(_staff(VIEW_STOCK)).get(_url(page, variant)).status_code == 403


@both_pages
def test_page_403_for_staff_with_only_add_movement_permission(page: str) -> None:
    variant = _stocked_variant()
    assert _client_for(_staff(ADD_MOVEMENT)).get(_url(page, variant)).status_code == 403


@both_pages
def test_page_404_for_unknown_variant(page: str) -> None:
    url = reverse(f"admin:inventory_stockitem_{page}", args=[999999])
    assert _client_for(_superuser()).get(url).status_code == 404


@both_pages
def test_page_redirects_anonymous_user_to_login(page: str) -> None:
    variant = _stocked_variant()
    response = Client().get(_url(page, variant))
    assert response.status_code == 302
    assert "login" in response["Location"]


@both_pages
def test_post_by_view_only_staff_is_403_and_writes_nothing(page: str) -> None:
    variant = _stocked_variant()
    before = InventoryMovement.objects.count()
    data = {"quantity_change": "1", "quantity": "1", "reason": "test"}
    response = _client_for(_staff(VIEW_STOCK)).post(_url(page, variant), data=data)
    assert response.status_code == 403
    assert InventoryMovement.objects.count() == before


@both_pages
def test_post_without_csrf_token_is_rejected(page: str) -> None:
    variant = _stocked_variant()
    before = InventoryMovement.objects.count()
    client = Client(enforce_csrf_checks=True)
    client.force_login(_superuser())
    data = {"quantity_change": "1", "quantity": "1", "reason": "test"}
    assert client.post(_url(page, variant), data=data).status_code == 403
    assert InventoryMovement.objects.count() == before


# --- Adjust ---------------------------------------------------------------


def test_adjust_up_writes_an_adjustment_and_redirects() -> None:
    variant = _stocked_variant("10")
    user = _superuser()
    response = _client_for(user).post(
        _url("adjust", variant), data={"quantity_change": "5", "reason": "Found a box"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)
    movement = _last_movement()
    assert movement.movement_type == InventoryMovement.MovementType.ADJUSTMENT
    assert movement.quantity_change == Decimal("5.000")
    assert movement.reason == "Found a box"
    assert movement.created_by_id == user.pk
    assert get_on_hand(variant) == Decimal("15.000")


def test_adjust_down_with_a_negative_number() -> None:
    variant = _stocked_variant("10")
    _client_for(_superuser()).post(
        _url("adjust", variant), data={"quantity_change": "-3", "reason": "Miscount"}
    )
    assert get_on_hand(variant) == Decimal("7.000")


def test_adjust_shows_a_success_message() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("adjust", variant),
        data={"quantity_change": "-3", "reason": "Miscount"},
        follow=True,
    )
    assert f"Adjusted {variant.sku_code} by -3." in response.content.decode()


def test_adjust_without_a_reason_shows_an_error_and_writes_nothing() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("adjust", variant), data={"quantity_change": "2", "reason": ""}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["reason"]
    assert get_on_hand(variant) == Decimal("10.000")


def test_adjust_by_zero_shows_the_service_error_and_writes_nothing() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("adjust", variant), data={"quantity_change": "0", "reason": "Nothing"}
    )
    assert response.status_code == 200
    assert "An adjustment must change stock." in response.content.decode()
    assert InventoryMovement.objects.count() == 1


def test_adjust_packet_variant_rejects_a_fractional_change() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("adjust", variant), data={"quantity_change": "1.5", "reason": "Half"}
    )
    assert response.status_code == 200
    assert "Packet variants accept whole numbers only." in response.content.decode()
    assert get_on_hand(variant) == Decimal("10.000")


def test_adjust_loose_variant_accepts_a_fractional_change() -> None:
    variant = ProductVariantFactory(is_sold_loose=True, unit=ProductVariant.Unit.KG)
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("4"))
    _client_for(_superuser()).post(
        _url("adjust", variant), data={"quantity_change": "-0.250", "reason": "Spill"}
    )
    assert get_on_hand(variant) == Decimal("3.750")


# --- Record damage --------------------------------------------------------


def test_damage_writes_a_negative_damage_movement_and_redirects() -> None:
    variant = _stocked_variant("10")
    user = _superuser()
    response = _client_for(user).post(
        _url("damage", variant), data={"quantity": "4", "reason": "Leaking packs"}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)
    movement = _last_movement()
    assert movement.movement_type == InventoryMovement.MovementType.DAMAGE
    assert movement.quantity_change == Decimal("-4.000")
    assert movement.reason == "Leaking packs"
    assert movement.created_by_id == user.pk
    assert get_on_hand(variant) == Decimal("6.000")


def test_damage_shows_a_success_message() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant),
        data={"quantity": "4", "reason": "Leaking packs"},
        follow=True,
    )
    content = response.content.decode()
    assert f"Recorded damage of 4 for {variant.sku_code}." in content


def test_damage_of_all_the_stock_is_allowed() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": "10", "reason": "Flood"}
    )
    assert response.status_code == 302
    assert get_on_hand(variant) == Decimal("0.000")


def test_damage_more_than_on_hand_shows_an_error_and_writes_nothing() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": "11", "reason": "Too many"}
    )
    assert response.status_code == 200
    assert "exceeds on-hand" in response.content.decode()
    assert InventoryMovement.objects.count() == 1
    assert get_on_hand(variant) == Decimal("10.000")


def test_damage_with_no_stock_shows_an_error_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": "1", "reason": "None here"}
    )
    assert response.status_code == 200
    assert "exceeds on-hand" in response.content.decode()
    assert InventoryMovement.objects.count() == 0


def test_damage_without_a_reason_shows_an_error_and_writes_nothing() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": "1", "reason": ""}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["reason"]
    assert InventoryMovement.objects.count() == 1


@pytest.mark.parametrize("quantity", ["0", "-2", "abc", ""])
def test_damage_with_a_bad_quantity_shows_an_error_and_writes_nothing(quantity: str) -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": quantity, "reason": "Bad"}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["quantity"]
    assert InventoryMovement.objects.count() == 1


def test_damage_packet_variant_rejects_a_fractional_quantity() -> None:
    variant = _stocked_variant("10")
    response = _client_for(_superuser()).post(
        _url("damage", variant), data={"quantity": "1.5", "reason": "Half"}
    )
    assert response.status_code == 200
    assert "Packet variants accept whole numbers only." in response.content.decode()
    assert get_on_hand(variant) == Decimal("10.000")


# --- Links on the Stock screen --------------------------------------------


@both_pages
def test_stock_screen_shows_the_link_to_a_user_who_may_write_stock(page: str) -> None:
    variant = _stocked_variant()
    response = _client_for(_superuser()).get(reverse(CHANGELIST))
    assert _url(page, variant) in response.content.decode("utf-8", "replace")


@both_pages
def test_stock_screen_hides_the_link_from_view_only_staff(page: str) -> None:
    variant = _stocked_variant()
    response = _client_for(_staff(VIEW_STOCK)).get(reverse(CHANGELIST))
    assert response.status_code == 200
    assert _url(page, variant) not in response.content.decode("utf-8", "replace")
