"""View tests for payment start, return, status and the webhook (D77)."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.orders.models import Order
from apps.payments.fake_gateway import FakeGateway
from apps.payments.gateway import PAYMENT_CAPTURED, PAYMENT_FAILED
from apps.payments.models import Payment, WebhookEvent
from apps.payments.tests.factories import (
    CapturedPaymentFactory,
    FailedPaymentFactory,
    PaymentFactory,
)
from apps.payments.tests.helpers import (
    clear_settings,
    event_for,
    factory_online_order,
    online_order,
    reset_settings,
    start_payment,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider for every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()
    clear_settings()


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _start_url(order: Order) -> str:
    return reverse("payment_start", args=[order.order_number])


def _return_url(order: Order) -> str:
    return reverse("payment_return", args=[order.order_number])


def _status_url(order: Order) -> str:
    return reverse("payment_status", args=[order.order_number])


def _webhook_body(payment: Payment, *, kind: str = PAYMENT_CAPTURED) -> dict[str, object]:
    return {
        "event": kind,
        "event_id": f"evt_{payment.pk}",
        "order_id": payment.gateway_order_id,
        "payment_id": f"pay_{payment.pk}",
        "amount": str(payment.amount),
        "method": "upi",
    }


def _post_webhook(
    client: Client,
    body: dict[str, object],
    *,
    signature: str = "sig",
    provider: str = "fake",
):
    return client.post(
        reverse("payment_webhook", args=[provider]),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE=signature,
    )


def test_payment_start_requires_login() -> None:
    order = factory_online_order()

    response = Client().post(_start_url(order))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('account_login')}?next=")


def test_payment_start_is_post_only() -> None:
    order = factory_online_order()

    response = _client(order.user).get(_start_url(order))

    assert response.status_code == 405


def test_payment_start_hides_another_customers_order() -> None:
    order = factory_online_order()
    response = _client(UserFactory()).post(_start_url(order))

    assert response.status_code == 404


def test_payment_start_shows_the_provider_page() -> None:
    order = factory_online_order()

    response = _client(order.user).post(_start_url(order))
    html = response.content.decode()

    assert response.status_code == 200
    assert order.order_number in html
    assert "development gateway" in html.lower()
    assert f"data-gateway-order-id>{payment_gateway_id(order)}" in html


def payment_gateway_id(order: Order) -> str:
    """The one stored gateway id for this order."""
    return Payment.objects.get(order=order).gateway_order_id


def test_payment_start_reports_an_already_paid_order() -> None:
    order = factory_online_order()
    CapturedPaymentFactory(order=order)

    response = _client(order.user).post(_start_url(order), follow=True)

    assert response.status_code == 200
    assert "already paid online" in response.content.decode()


def test_payment_start_reports_a_missing_gateway(settings) -> None:
    settings.PAYMENT_PROVIDER = ""
    order = factory_online_order()

    response = _client(order.user).post(_start_url(order), follow=True)

    assert response.status_code == 200
    assert "Online payment is not available" in response.content.decode()
    assert Payment.objects.filter(order=order).count() == 0


def test_payment_start_reuses_the_attempt_on_a_double_post() -> None:
    order = factory_online_order()
    client = _client(order.user)

    first = client.post(_start_url(order))
    second = client.post(_start_url(order))

    assert first.status_code == 200
    assert second.status_code == 200
    assert Payment.objects.filter(order=order).count() == 1


def test_payment_start_refuses_an_order_past_packing() -> None:
    order = factory_online_order()
    order.status = Order.Status.PACKING
    order.save(update_fields=["status", "updated_at"])

    response = _client(order.user).post(_start_url(order), follow=True)

    assert "Online payment is not available" in response.content.decode()
    assert Payment.objects.filter(order=order).count() == 0


def test_payment_return_shows_a_pending_payment() -> None:
    order = factory_online_order()

    response = _client(order.user).get(_return_url(order))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-payment-state="pending"' in html
    assert "checking your payment" in html.lower()


def test_payment_return_shows_a_captured_payment() -> None:
    order = factory_online_order()
    CapturedPaymentFactory(order=order)

    response = _client(order.user).get(_return_url(order))
    html = response.content.decode()

    assert 'data-payment-state="captured"' in html
    assert "received your payment" in html.lower()


def test_payment_return_shows_a_failed_payment_with_try_again() -> None:
    order = factory_online_order(status=Order.Status.PAYMENT_FAILED)
    FailedPaymentFactory(order=order)

    response = _client(order.user).get(_return_url(order))
    html = response.content.decode()

    assert 'data-payment-state="failed"' in html
    assert "try again" in html.lower()
    assert "update cart" in html.lower()


def test_payment_return_adds_nothing_without_gateway_facts() -> None:
    order = factory_online_order()
    PaymentFactory(order=order)

    _client(order.user).get(_return_url(order))

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_PENDING


def test_payment_return_applies_a_captured_payment_on_page_load() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    FakeGateway.seed_payments(
        payment.gateway_order_id,
        [event_for(payment, event_id="evt_fetch")],
    )

    response = _client(order.user).get(_return_url(order))

    payment.refresh_from_db()
    order.refresh_from_db()
    assert 'data-payment-state="captured"' in response.content.decode()
    assert payment.status == Payment.Status.CAPTURED
    assert order.status == Order.Status.CONFIRMED
    assert WebhookEvent.objects.count() == 0


def test_payment_return_hides_another_customers_order() -> None:
    order = factory_online_order()

    response = _client(UserFactory()).get(_return_url(order))

    assert response.status_code == 404


def test_payment_return_requires_login() -> None:
    order = factory_online_order()

    response = Client().get(_return_url(order))

    assert response.status_code == 302


def test_the_status_fragment_is_a_small_pending_block() -> None:
    order = factory_online_order()

    response = _client(order.user).get(_status_url(order))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'data-payment-state="pending"' in html
    assert "<html" not in html


def test_the_status_fragment_marks_a_failure() -> None:
    order = factory_online_order(status=Order.Status.PAYMENT_FAILED)
    FailedPaymentFactory(order=order)

    response = _client(order.user).get(_status_url(order))

    assert 'data-payment-state="failed"' in response.content.decode()


def test_the_webhook_is_csrf_exempt() -> None:
    from apps.payments import views

    assert views.payment_webhook.csrf_exempt is True


def test_the_webhook_is_post_only() -> None:
    response = Client().get(reverse("payment_webhook", args=["fake"]))

    assert response.status_code == 405


def test_the_webhook_is_404_for_another_provider() -> None:
    response = _post_webhook(Client(), {"event": PAYMENT_CAPTURED}, provider="razorpay")

    assert response.status_code == 404
    assert WebhookEvent.objects.count() == 0


def test_the_webhook_captures_a_payment() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment

    response = _post_webhook(Client(), _webhook_body(payment))

    payment.refresh_from_db()
    order.refresh_from_db()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert payment.status == Payment.Status.CAPTURED
    assert order.status == Order.Status.CONFIRMED


def test_the_webhook_marks_a_failed_payment() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    body = _webhook_body(payment, kind=PAYMENT_FAILED)
    body["error"] = "card declined"

    response = _post_webhook(Client(), body)

    payment.refresh_from_db()
    order.refresh_from_db()
    assert response.status_code == 200
    assert payment.status == Payment.Status.FAILED
    assert payment.failure_reason == "card declined"
    assert order.status == Order.Status.PAYMENT_FAILED


def test_the_webhook_refuses_a_missing_signature() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment

    response = _post_webhook(Client(), _webhook_body(payment), signature="")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid webhook."}


def test_the_webhook_ignores_a_valid_event_it_does_not_handle() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment

    response = _post_webhook(
        Client(),
        {**_webhook_body(payment), "event": "payment.refunded"},
    )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert WebhookEvent.objects.count() == 0
    assert payment.status == Payment.Status.CREATED
    assert order.status == Order.Status.PAYMENT_PENDING


def test_a_duplicate_webhook_applies_only_once() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    body = _webhook_body(payment)

    first = _post_webhook(Client(), body)
    second = _post_webhook(Client(), body)

    order.refresh_from_db()
    assert first.json() == {"status": "ok"}
    assert second.json() == {"status": "ok"}
    assert WebhookEvent.objects.count() == 1
    assert order.status == Order.Status.CONFIRMED


def test_the_webhook_refuses_when_no_gateway_is_configured(settings) -> None:
    settings.PAYMENT_PROVIDER = ""

    response = _post_webhook(Client(), {"event": PAYMENT_CAPTURED})

    assert response.status_code == 404


def test_the_webhook_records_an_amount_mismatch() -> None:
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    body = _webhook_body(payment)
    body["amount"] = "1.00"

    response = _post_webhook(Client(), body)

    webhook = WebhookEvent.objects.get()
    assert response.status_code == 200
    assert webhook.error == "Amount does not match the payment"
    assert payment.status == Payment.Status.CREATED


def test_the_webhook_accepts_an_unknown_order_without_creating_a_payment() -> None:
    response = _post_webhook(
        Client(),
        {
            "event": PAYMENT_CAPTURED,
            "event_id": "evt_unknown",
            "order_id": "nope",
            "payment_id": "pay_1",
            "amount": "1.00",
        },
    )

    assert response.status_code == 200
    assert Payment.objects.count() == 0
    assert WebhookEvent.objects.get().error == "Payment not found"
