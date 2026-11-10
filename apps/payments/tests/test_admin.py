"""Tests for the read-only payment admin screens (D77)."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.payments.admin import PaymentAdmin, WebhookEventAdmin
from apps.payments.models import Payment, WebhookEvent
from apps.payments.tests.factories import PaymentFactory, WebhookEventFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_request_factory = RequestFactory()


def _admin_for(model, admin_class, user):
    """Bind the admin class to the real site with request.user set."""
    request = _request_factory.get("/admin/")
    request.user = user
    return admin_class(model, admin.site), request


def _manager():
    return UserFactory(is_staff=True, role=User.ROLE_MANAGER)


def _member():
    return UserFactory(is_staff=True, role=User.ROLE_MEMBER)


def test_a_manager_sees_the_payment_changelist() -> None:
    PaymentFactory()
    client = Client()
    client.force_login(_manager())

    response = client.get(reverse("admin:payments_payment_changelist"))

    assert response.status_code == 200


def test_a_manager_sees_the_webhook_changelist() -> None:
    WebhookEventFactory()
    client = Client()
    client.force_login(_manager())

    response = client.get(reverse("admin:payments_webhookevent_changelist"))

    assert response.status_code == 200


def test_a_member_without_the_capability_has_no_module_access() -> None:
    payment_admin, request = _admin_for(Payment, PaymentAdmin, _member())

    assert payment_admin.has_module_permission(request) is False
    assert payment_admin.has_view_permission(request) is False


def test_a_manager_has_view_permission_but_no_write_permission() -> None:
    payment_admin, request = _admin_for(Payment, PaymentAdmin, _manager())

    assert payment_admin.has_view_permission(request) is True
    assert payment_admin.has_add_permission(request) is False
    assert payment_admin.has_change_permission(request) is False
    assert payment_admin.has_delete_permission(request) is False


def test_the_webhook_admin_is_read_only_too() -> None:
    payment_admin, request = _admin_for(WebhookEvent, WebhookEventAdmin, _manager())

    assert payment_admin.has_view_permission(request) is True
    assert payment_admin.has_add_permission(request) is False
    assert payment_admin.has_change_permission(request) is False
    assert payment_admin.has_delete_permission(request) is False


def test_the_payment_readonly_fields_are_every_model_field() -> None:
    payment_admin, request = _admin_for(Payment, PaymentAdmin, _manager())
    expected = {field.name for field in Payment._meta.fields}

    assert set(payment_admin.get_readonly_fields(request)) == expected


def test_the_webhook_readonly_fields_are_every_model_field() -> None:
    webhook_admin, request = _admin_for(WebhookEvent, WebhookEventAdmin, _manager())
    expected = {field.name for field in WebhookEvent._meta.fields}

    assert set(webhook_admin.get_readonly_fields(request)) == expected


def test_the_payment_change_page_has_no_save_button() -> None:
    payment = PaymentFactory()
    client = Client()
    client.force_login(_manager())

    html = client.get(reverse("admin:payments_payment_change", args=[payment.pk])).content.decode()

    assert 'name="_save"' not in html


def test_the_payment_search_finds_the_order_number() -> None:
    payment = PaymentFactory()
    client = Client()
    client.force_login(_manager())

    response = client.get(
        reverse("admin:payments_payment_changelist"),
        {"q": payment.order.order_number},
    )

    assert response.status_code == 200
    assert payment.order.order_number in response.content.decode()


def test_the_webhook_search_finds_the_event_id() -> None:
    webhook = WebhookEventFactory(event_id="evt_search_me")
    client = Client()
    client.force_login(_manager())

    response = client.get(
        reverse("admin:payments_webhookevent_changelist"),
        {"q": "evt_search_me"},
    )

    assert response.status_code == 200
    assert webhook.event_id in response.content.decode()
