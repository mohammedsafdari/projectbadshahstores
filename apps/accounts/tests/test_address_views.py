"""Tests for the address book pages (Slice 36a, D65)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from apps.accounts.address_presentation import (
    NAME_REQUIRED,
    PHONE_ERROR,
    PINCODE_ERROR,
    UNSERVED_BADGE,
    limit_message,
    unserved_notice,
)
from apps.accounts.models import Address, User
from apps.accounts.services import MAX_ACTIVE_ADDRESSES, archive_address, create_address
from apps.cart.tests.test_views_cart import _csrf_token_from, _form_with_action
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
OTHER_PHONE = "+919876543211"


def _user(phone: str = PHONE) -> User:
    return User.objects.create_user(phone=phone, password="ok")


def _address(user: User, **changes: Any) -> Address:
    data: dict[str, Any] = {
        "recipient_name": "Asha Sharma",
        "recipient_phone": "9876543210",
        "line1": "12, Saifee Nagar",
        "pincode": "452012",
    }
    data.update(changes)
    return create_address(user, **data)


def _form_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "recipient_name": "Asha Sharma",
        "recipient_phone": "9876543210",
        "line1": "12, Saifee Nagar",
        "line2": "",
        "landmark": "",
        "city": "Indore",
        "state": "Madhya Pradesh",
        "pincode": "452012",
        "label": Address.Label.HOME,
    }
    data.update(changes)
    return data


def _card(html: str, pk: int) -> str:
    """The complete <article> block for one address card."""
    pattern = rf'<article[^>]*data-address-id="{pk}"[^>]*>.*?</article>'
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No address card for {pk}."
    return match.group(0)


def _input_value(html: str, name: str) -> str:
    """Read the value of the specific <input> with this name."""
    tag_match = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*>', html)
    assert tag_match is not None, f"No input named {name}."
    value_match = re.search(r'value="([^"]*)"', tag_match.group(0))
    assert value_match is not None, f"The {name} input has no value."
    return value_match.group(1)


def _alert_text(html: str) -> str:
    match = re.search(r'<p role="alert"[^>]*>(.*?)</p>', html, re.DOTALL)
    assert match is not None, "No alert on the page."
    return match.group(1)


def _message_texts(html: str) -> list[str]:
    return re.findall(r'<li class="message[^"]*">(.*?)</li>', html, re.DOTALL)


def _assert_login_redirect(response: Any, path: str) -> None:
    assert response.status_code == 302
    parsed = urlparse(response["Location"])
    assert parsed.path == reverse("account_login")
    assert parse_qs(parsed.query)["next"] == [path]


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------
def test_address_list_redirects_a_guest_to_login_with_next() -> None:
    response = Client().get(reverse("address_list"))

    _assert_login_redirect(response, reverse("address_list"))


def test_address_list_shows_only_my_active_addresses() -> None:
    user = _user()
    other = _user(OTHER_PHONE)
    kept = _address(user, recipient_name="Kept Address")
    archived = _address(user, recipient_name="Archived Address", line1="2, Saifee Nagar")
    archive_address(user, archived.pk)
    other_address = _address(other, recipient_name="Other Address", line1="3, Vijay Nagar")
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_list")).content.decode()

    assert "Kept Address" in _card(html, kept.pk)
    assert f'data-address-id="{archived.pk}"' not in html
    assert f'data-address-id="{other_address.pk}"' not in html


def test_address_list_marks_the_default_address() -> None:
    user = _user()
    first = _address(user)
    second = _address(user, line1="99, New Palasia")
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_list")).content.decode()

    assert 'data-default-badge="1"' in _card(html, first.pk)
    assert "data-default-badge" not in _card(html, second.pk)


def test_address_list_marks_an_unserved_pin_code() -> None:
    user = _user()
    address = _address(user, pincode="452099")
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_list")).content.decode()
    card = _card(html, address.pk)

    assert 'data-unserved-badge="1"' in card
    assert str(UNSERVED_BADGE) in card


def test_address_list_does_not_mark_a_served_pin_code() -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode="452012")
    user = _user()
    address = _address(user, pincode="452012")
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_list")).content.decode()

    assert "data-unserved-badge" not in _card(html, address.pk)


def test_address_list_hides_the_add_link_at_the_limit() -> None:
    user = _user()
    for number in range(MAX_ACTIVE_ADDRESSES):
        _address(user, line1=f"{number}, Saifee Nagar")
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_list")).content.decode()

    assert f'href="{reverse("address_add")}"' not in html
    assert str(limit_message()) in _alert_text(html)


def test_address_list_is_never_cached() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.get(reverse("address_list"))

    assert "no-store" in response["Cache-Control"]


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------
def test_add_page_redirects_a_guest_to_login() -> None:
    response = Client().get(reverse("address_add"))

    _assert_login_redirect(response, reverse("address_add"))


def test_add_page_shows_the_form_with_indore_defaults() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_add")).content.decode()

    assert _input_value(html, "city") == "Indore"
    assert _input_value(html, "state") == "Madhya Pradesh"


def test_add_saves_an_address_and_redirects_to_the_list() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data())

    assert response.status_code == 302
    assert response["Location"] == reverse("address_list")
    address = Address.objects.get(user=user)
    assert address.recipient_name == "Asha Sharma"
    assert address.recipient_phone == "+919876543210"
    assert address.pincode == "452012"


def test_add_first_address_becomes_the_default() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    client.post(reverse("address_add"), _form_data())

    address = Address.objects.get(user=user)
    assert address.is_default is True


def test_add_with_make_default_moves_the_default() -> None:
    user = _user()
    first = _address(user)
    client = Client()
    client.force_login(user)

    client.post(
        reverse("address_add"),
        _form_data(line1="99, New Palasia", make_default="on"),
    )

    first.refresh_from_db()
    second = Address.objects.get(user=user, line1="99, New Palasia")
    assert first.is_default is False
    assert second.is_default is True


def test_add_with_an_invalid_pin_code_shows_the_error_and_saves_nothing() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data(pincode="45201"))

    assert response.status_code == 200
    form_html = _form_with_action(response.content.decode(), reverse("address_add"))
    assert str(PINCODE_ERROR) in form_html
    assert not Address.objects.filter(user=user).exists()


def test_add_with_a_blank_name_shows_the_error() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data(recipient_name="   "))

    assert response.status_code == 200
    form_html = _form_with_action(response.content.decode(), reverse("address_add"))
    assert escape(str(NAME_REQUIRED)) in form_html
    assert not Address.objects.filter(user=user).exists()


def test_add_with_an_invalid_phone_shows_the_error() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data(recipient_phone="12345"))

    assert response.status_code == 200
    form_html = _form_with_action(response.content.decode(), reverse("address_add"))
    assert str(PHONE_ERROR) in form_html
    assert not Address.objects.filter(user=user).exists()


def test_add_in_an_unserved_pin_code_saves_and_shows_the_notice() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("address_add"),
        _form_data(pincode="452099"),
        follow=True,
    )

    assert response.status_code == 200
    address = Address.objects.get(user=user)
    assert address.pincode == "452099"
    assert any(
        str(unserved_notice("452099")) in text for text in _message_texts(response.content.decode())
    )


def test_add_at_the_limit_is_refused_with_a_message() -> None:
    user = _user()
    for number in range(MAX_ACTIVE_ADDRESSES):
        _address(user, line1=f"{number}, Saifee Nagar")
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data())

    assert response.status_code == 200
    assert Address.objects.filter(user=user).count() == MAX_ACTIVE_ADDRESSES
    assert str(limit_message()) in _alert_text(response.content.decode())


def test_add_returns_to_a_safe_next_url() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(reverse("address_add"), _form_data(next="/cart/"))

    assert response.status_code == 302
    assert response["Location"] == "/cart/"


def test_add_ignores_an_unsafe_next_url() -> None:
    user = _user()
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("address_add"),
        _form_data(next="https://evil.example/phish"),
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("address_list")


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def test_edit_shows_the_saved_values() -> None:
    user = _user()
    address = _address(user)
    client = Client()
    client.force_login(user)

    html = client.get(reverse("address_edit", args=[address.pk])).content.decode()

    assert _input_value(html, "recipient_name") == "Asha Sharma"
    assert _input_value(html, "recipient_phone") == "+919876543210"
    assert _input_value(html, "line1") == "12, Saifee Nagar"
    assert _input_value(html, "pincode") == "452012"


def test_edit_saves_changes() -> None:
    user = _user()
    address = _address(user)
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("address_edit", args=[address.pk]),
        _form_data(recipient_name="Mohan Sharma", line1="99, New Palasia"),
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("address_list")
    address.refresh_from_db()
    assert address.recipient_name == "Mohan Sharma"
    assert address.line1 == "99, New Palasia"


def test_edit_another_users_address_is_404() -> None:
    owner = _user()
    other = _user(OTHER_PHONE)
    address = _address(owner)
    client = Client()
    client.force_login(other)

    response = client.get(reverse("address_edit", args=[address.pk]))

    assert response.status_code == 404


def test_edit_an_archived_address_is_404() -> None:
    user = _user()
    address = _address(user)
    archive_address(user, address.pk)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("address_edit", args=[address.pk]))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------
def test_archive_is_post_only() -> None:
    user = _user()
    address = _address(user)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("address_archive", args=[address.pk]))

    assert response.status_code == 405


def test_archive_hides_the_address_from_the_list() -> None:
    user = _user()
    address = _address(user)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    url = reverse("address_archive", args=[address.pk])
    page = client.get(reverse("address_list")).content.decode()
    token = _csrf_token_from(_form_with_action(page, url))

    response = client.post(url, {"csrfmiddlewaretoken": token}, follow=True)

    assert response.status_code == 200
    address.refresh_from_db()
    assert address.is_active is False
    assert f'data-address-id="{address.pk}"' not in response.content.decode()


def test_archive_another_users_address_is_404() -> None:
    owner = _user()
    other = _user(OTHER_PHONE)
    address = _address(owner)
    client = Client()
    client.force_login(other)

    response = client.post(reverse("address_archive", args=[address.pk]))

    assert response.status_code == 404
    address.refresh_from_db()
    assert address.is_active is True


# ---------------------------------------------------------------------------
# Setting the default
# ---------------------------------------------------------------------------
def test_set_default_is_post_only() -> None:
    user = _user()
    address = _address(user)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("address_set_default", args=[address.pk]))

    assert response.status_code == 405


def test_set_default_moves_the_default() -> None:
    user = _user()
    first = _address(user)
    second = _address(user, line1="99, New Palasia")
    client = Client()
    client.force_login(user)

    client.post(reverse("address_set_default", args=[second.pk]))

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_default is False
    assert second.is_default is True


def test_set_default_another_users_address_is_404() -> None:
    owner = _user()
    other = _user(OTHER_PHONE)
    address = _address(owner)
    client = Client()
    client.force_login(other)

    response = client.post(reverse("address_set_default", args=[address.pk]))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CSRF, guests and the header
# ---------------------------------------------------------------------------
def test_mutations_need_a_csrf_token() -> None:
    user = _user()
    first = _address(user)
    second = _address(user, line1="99, New Palasia")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    archive_response = client.post(reverse("address_archive", args=[first.pk]))
    default_response = client.post(reverse("address_set_default", args=[second.pk]))

    assert archive_response.status_code == 403
    assert default_response.status_code == 403
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_active is True
    assert second.is_default is False


def test_guest_post_to_archive_redirects_to_login() -> None:
    user = _user()
    address = _address(user)
    path = reverse("address_archive", args=[address.pk])

    response = Client().post(path)

    _assert_login_redirect(response, path)
    address.refresh_from_db()
    assert address.is_active is True


def test_header_links_to_addresses_when_logged_in() -> None:
    user = _user()
    client = Client()
    link = f'href="{reverse("address_list")}"'

    guest_page = client.get(reverse("home")).content.decode()
    assert link not in guest_page

    client.force_login(user)
    logged_in_page = client.get(reverse("home")).content.decode()
    assert link in logged_in_page
