"""Tests for the "Receive stock" page on the Stock admin screen."""

from datetime import date
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
from apps.inventory.tests.factories import SupplierFactory

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
    return reverse("admin:inventory_stockitem_receive", args=[variant.pk])


def _loose_variant() -> ProductVariant:
    return ProductVariantFactory(is_sold_loose=True, unit=ProductVariant.Unit.KG)


def test_page_200_for_superuser_and_shows_the_variant() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).get(_url(variant))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert variant.sku_code in content
    assert 'name="quantity"' in content


def test_post_without_csrf_token_is_rejected() -> None:
    # The default test client skips the CSRF check, so turn it on here.
    variant = ProductVariantFactory()
    client = Client(enforce_csrf_checks=True)
    client.force_login(_superuser())
    response = client.post(_url(variant), data={"quantity": "5"})
    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_page_form_contains_a_csrf_token() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).get(_url(variant))
    assert "csrfmiddlewaretoken" in response.content.decode("utf-8", "replace")


def test_page_200_for_staff_with_view_and_add_permissions() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(VIEW_STOCK, ADD_MOVEMENT))
    assert client.get(_url(variant)).status_code == 200


def test_page_403_for_staff_with_only_view_permission() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(VIEW_STOCK))
    assert client.get(_url(variant)).status_code == 403


def test_page_403_for_staff_with_only_add_movement_permission() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(ADD_MOVEMENT))
    assert client.get(_url(variant)).status_code == 403


def test_page_404_for_unknown_variant() -> None:
    url = reverse("admin:inventory_stockitem_receive", args=[999999])
    assert _client_for(_superuser()).get(url).status_code == 404


def test_page_redirects_anonymous_user_to_login() -> None:
    variant = ProductVariantFactory()
    response = Client().get(_url(variant))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_post_by_view_only_staff_is_403_and_writes_nothing() -> None:
    variant = ProductVariantFactory()
    client = _client_for(_staff(VIEW_STOCK))
    response = client.post(_url(variant), data={"quantity": "5"})
    assert response.status_code == 403
    assert InventoryMovement.objects.count() == 0


def test_valid_post_receives_stock_and_redirects_to_stock_screen() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(_url(variant), data={"quantity": "10"})
    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)
    assert get_on_hand(variant) == Decimal("10.000")


def test_valid_post_stores_every_field_on_the_movement() -> None:
    variant = ProductVariantFactory()
    supplier = SupplierFactory()
    user = _superuser()
    _client_for(user).post(
        _url(variant),
        data={
            "quantity": "12",
            "supplier": supplier.pk,
            "batch_number": "B-77",
            "expiry_date": "2027-03-31",
            "reason": "Weekly delivery",
        },
    )
    movement = InventoryMovement.objects.get()
    assert movement.movement_type == InventoryMovement.MovementType.PURCHASE_RECEIVED
    assert movement.variant_id == variant.pk
    assert movement.quantity_change == Decimal("12.000")
    assert movement.supplier_id == supplier.pk
    assert movement.batch_number == "B-77"
    assert movement.expiry_date == date(2027, 3, 31)
    assert movement.reason == "Weekly delivery"
    assert movement.created_by_id == user.pk


def test_valid_post_shows_a_success_message() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(_url(variant), data={"quantity": "10"}, follow=True)
    content = response.content.decode("utf-8", "replace")
    assert f"Received 10 of {variant.sku_code}." in content


def test_supplier_batch_expiry_and_note_are_optional() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(_url(variant), data={"quantity": "3"})
    assert response.status_code == 302
    movement = InventoryMovement.objects.get()
    assert movement.supplier is None
    assert movement.expiry_date is None


def test_loose_variant_accepts_a_fractional_quantity() -> None:
    variant = _loose_variant()
    _client_for(_superuser()).post(_url(variant), data={"quantity": "2.500"})
    assert get_on_hand(variant) == Decimal("2.500")


def test_past_expiry_date_is_accepted() -> None:
    # Open question default (D45): a past date is allowed, there is no warning.
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(
        _url(variant), data={"quantity": "4", "expiry_date": "2020-01-01"}
    )
    assert response.status_code == 302
    assert InventoryMovement.objects.get().expiry_date == date(2020, 1, 1)


def test_packet_variant_rejects_a_fractional_quantity() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(_url(variant), data={"quantity": "2.5"})
    assert response.status_code == 200
    assert "Packet variants accept whole numbers only." in response.content.decode()
    assert InventoryMovement.objects.count() == 0


@pytest.mark.parametrize("quantity", ["0", "-5", "abc", ""])
def test_bad_quantity_shows_an_error_and_writes_nothing(quantity: str) -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).post(_url(variant), data={"quantity": quantity})
    assert response.status_code == 200
    assert response.context["form"].errors["quantity"]
    assert InventoryMovement.objects.count() == 0


def test_archived_supplier_is_not_accepted() -> None:
    variant = ProductVariantFactory()
    archived = SupplierFactory(is_active=False)
    response = _client_for(_superuser()).post(
        _url(variant), data={"quantity": "5", "supplier": archived.pk}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["supplier"]
    assert InventoryMovement.objects.count() == 0


def test_stock_screen_shows_receive_link_to_user_who_may_receive() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_superuser()).get(reverse(CHANGELIST))
    assert _url(variant) in response.content.decode("utf-8", "replace")


def test_stock_screen_hides_receive_link_from_view_only_staff() -> None:
    variant = ProductVariantFactory()
    response = _client_for(_staff(VIEW_STOCK)).get(reverse(CHANGELIST))
    assert response.status_code == 200
    assert _url(variant) not in response.content.decode("utf-8", "replace")
