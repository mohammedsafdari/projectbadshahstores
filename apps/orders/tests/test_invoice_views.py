"""View tests for the staff invoice page and both PDF downloads (D80)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.orders.invoices import mark_invoice_cancelled
from apps.orders.models import Order
from apps.orders.tests.invoice_helpers import packing_order, sent_out_order
from apps.orders.tests.staff_helpers import NOW, staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _html_url(order: Order) -> str:
    return reverse("staff_invoice", args=[order.order_number])


def _pdf_url(order: Order) -> str:
    return reverse("staff_invoice_pdf", args=[order.order_number])


def _customer_url(order: Order) -> str:
    return reverse("order_invoice_pdf", args=[order.checkout_group])


def test_staff_invoice_page_opens_for_an_owner() -> None:
    order, invoice = sent_out_order()

    response = _client(staff()).get(_html_url(order))

    assert response.status_code == 200
    assert invoice.invoice_number in response.content.decode()


def test_staff_invoice_pdf_opens_for_an_owner() -> None:
    order, _invoice = sent_out_order()

    response = _client(staff()).get(_pdf_url(order))

    assert response.status_code == 200


def test_a_packer_is_refused_the_invoice_page() -> None:
    order, _invoice = sent_out_order()

    response = _client(staff(User.ROLE_PACKER)).get(_html_url(order))

    assert response.status_code == 403


def test_a_packer_is_refused_the_invoice_pdf() -> None:
    order, _invoice = sent_out_order()

    response = _client(staff(User.ROLE_PACKER)).get(_pdf_url(order))

    assert response.status_code == 403
    assert response["Content-Type"] != "application/pdf"


def test_an_anonymous_visitor_is_redirected_from_the_invoice_page() -> None:
    order, _invoice = sent_out_order()

    response = Client().get(_html_url(order))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_an_anonymous_visitor_is_redirected_from_the_invoice_pdf() -> None:
    order, _invoice = sent_out_order()

    response = Client().get(_pdf_url(order))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_an_anonymous_visitor_is_redirected_from_the_customer_invoice_pdf() -> None:
    order, _invoice = sent_out_order()

    response = Client().get(_customer_url(order))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_a_customer_sees_their_own_invoice_pdf() -> None:
    order, invoice = sent_out_order()

    response = _client(order.user).get(_customer_url(order))

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert invoice.invoice_number.encode() in response.content


def test_a_stranger_gets_a_404_for_someone_elses_invoice() -> None:
    order, _invoice = sent_out_order()
    stranger = UserFactory()

    response = _client(stranger).get(_customer_url(order))

    assert response.status_code == 404


def test_the_pdf_content_type_is_pdf() -> None:
    order, _invoice = sent_out_order()

    response = _client(staff()).get(_pdf_url(order))

    assert response["Content-Type"] == "application/pdf"
    # The number holds slashes, so the file name uses dashes.
    assert "attachment" not in response["Content-Disposition"]
    assert 'filename="invoice-INV-26-27-000001.pdf"' in response["Content-Disposition"]


def test_the_pdf_starts_with_the_pdf_header() -> None:
    order, _invoice = sent_out_order()

    response = _client(order.user).get(_customer_url(order))

    assert response.content[:5] == b"%PDF-"


def test_a_cancelled_invoice_is_marked_in_the_pdf_text() -> None:
    order, invoice = sent_out_order()
    mark_invoice_cancelled(order, "The customer refused it", now=NOW)

    response = _client(staff()).get(_pdf_url(order))

    assert b"Cancelled" in response.content
    invoice.refresh_from_db()
    assert invoice.cancelled_at == NOW


def test_the_staff_order_detail_links_the_invoice() -> None:
    order, _invoice = sent_out_order()

    html = (
        _client(staff())
        .get(reverse("staff_order_detail", args=[order.order_number]))
        .content.decode()
    )

    assert _html_url(order) in html
    assert _pdf_url(order) in html


def test_the_customer_order_detail_links_the_invoice() -> None:
    order, _invoice = sent_out_order()

    html = (
        _client(order.user)
        .get(reverse("order_detail", args=[order.checkout_group]))
        .content.decode()
    )

    assert _customer_url(order) in html


def test_the_customer_order_detail_has_no_invoice_link_before_one_exists() -> None:
    order, _variants = packing_order()

    html = (
        _client(order.user)
        .get(reverse("order_detail", args=[order.checkout_group]))
        .content.decode()
    )

    assert "invoice.pdf" not in html


def test_an_order_without_an_invoice_has_no_staff_invoice_page() -> None:
    order, _variants = packing_order()

    response = _client(staff()).get(_html_url(order))

    assert response.status_code == 404
