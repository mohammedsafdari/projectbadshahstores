"""Tests for the History links from the Stock screen to the movement history."""

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.tests.factories import InventoryMovementFactory

pytestmark = pytest.mark.django_db

STOCK_LIST = "admin:inventory_stockitem_changelist"
MOVEMENTS = "admin:inventory_inventorymovement_changelist"
VIEW_STOCK = "view_stockitem"
VIEW_MOVEMENT = "view_inventorymovement"
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


def _history_href(variant: ProductVariant) -> str:
    return f"{reverse(MOVEMENTS)}?variant__id__exact={variant.pk}"


def test_stock_screen_shows_history_link_to_a_user_who_may_read_movements() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).get(reverse(STOCK_LIST))
    assert _history_href(variant) in response.content.decode("utf-8", "replace")


def test_stock_screen_shows_history_link_without_any_write_permission() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(VIEW_STOCK, VIEW_MOVEMENT))
    response = client.get(reverse(STOCK_LIST))
    assert _history_href(variant) in response.content.decode("utf-8", "replace")


def test_stock_screen_hides_history_link_without_movement_permission() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(VIEW_STOCK, ADD_MOVEMENT))
    response = client.get(reverse(STOCK_LIST))
    assert _history_href(variant) not in response.content.decode("utf-8", "replace")


def test_history_link_lists_only_that_variants_movements() -> None:
    wanted = InventoryMovementFactory().variant
    other = InventoryMovementFactory().variant
    response = _client_for(_superuser()).get(_history_href(wanted))
    assert response.status_code == 200
    listed = {row.variant_id for row in response.context["cl"].result_list}
    assert listed == {wanted.pk}
    assert other.pk not in listed


def test_history_link_works_for_staff_with_only_the_view_permission() -> None:
    variant = InventoryMovementFactory().variant
    response = _client_for(_staff(VIEW_MOVEMENT)).get(_history_href(variant))
    assert response.status_code == 200
    assert len(response.context["cl"].result_list) == 1


@pytest.mark.parametrize("page", ["receive", "adjust", "count", "damage"])
def test_action_pages_show_the_history_link_to_a_user_who_may_read_movements(
    page: str,
) -> None:
    variant = ProductVariantFactory()
    url = reverse(f"admin:inventory_stockitem_{page}", args=[variant.pk])
    response = _client_for(_superuser()).get(url)
    assert _history_href(variant) in response.content.decode("utf-8", "replace")


@pytest.mark.parametrize("page", ["receive", "adjust", "count", "damage"])
def test_action_pages_hide_the_history_link_without_movement_permission(page: str) -> None:
    variant = ProductVariantFactory()
    url = reverse(f"admin:inventory_stockitem_{page}", args=[variant.pk])
    response = _client_for(_staff(VIEW_STOCK, ADD_MOVEMENT)).get(url)
    assert response.status_code == 200
    assert _history_href(variant) not in response.content.decode("utf-8", "replace")
