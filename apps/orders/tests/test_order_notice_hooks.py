"""Tests for the order notice hooks in the order service (Slice 38b, D69)."""

from __future__ import annotations

from unittest.mock import call, patch
from uuid import uuid4

import pytest

from apps.notifications.messages import (
    EVENT_CANCELLED,
    EVENT_DELIVERED,
    EVENT_OUT_FOR_DELIVERY,
    EVENT_PLACED,
    EVENT_STAFF_NEW_ORDER,
)
from apps.orders.models import Order
from apps.orders.services import (
    EmptyCartError,
    InvalidTransitionError,
    cancel_order_by_customer,
    transition_order,
)
from apps.orders.state import DELIVERED, OUT_FOR_DELIVERY, PACKING
from apps.orders.tests.checkout_helpers import (
    NOW,
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_cod,
    place_credit,
    reset_settings,
    serve,
    set_settings,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

FAILING_BACKEND = "apps.notifications.tests.recording.FailingSmsBackend"
SHOP_EMAIL = "shop@example.com"


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _ready_user():
    """A customer with a served address and a stocked cart ready to order."""
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, address_row


def _ready_credit_user():
    """A credit customer with a served address and a stocked cart."""
    user = credit_user()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, address_row


def _cod_order():
    """A real COD order placed before a capture block starts."""
    user, address_row = _ready_user()
    return user, place_cod(user, address_row).order


def test_place_cod_order_sends_placed_and_staff_notices_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    user, address_row = _ready_user()

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        placed = place_cod(user, address_row)

    order = placed.order
    assert len(callbacks) == 1
    assert notify.call_args_list == [
        call(order.pk, EVENT_PLACED),
        call(order.pk, EVENT_STAFF_NEW_ORDER),
    ]


def test_place_credit_order_sends_placed_and_staff_notices_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    user, address_row = _ready_credit_user()

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        placed = place_credit(user, address_row)

    order = placed.order
    assert len(callbacks) == 1
    assert notify.call_args_list == [
        call(order.pk, EVENT_PLACED),
        call(order.pk, EVENT_STAFF_NEW_ORDER),
    ]


def test_placement_registers_one_callback_and_sends_nothing_before_the_commit(
    django_capture_on_commit_callbacks,
) -> None:
    user, address_row = _ready_user()

    # The patch stays open while the captured callback is run by hand below.
    with patch("apps.orders.services.notify_order_event") as notify:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            placed = place_cod(user, address_row)
            assert callbacks == []
            notify.assert_not_called()

        assert len(callbacks) == 1
        callbacks[0]()

    assert notify.call_args_list == [
        call(placed.order.pk, EVENT_PLACED),
        call(placed.order.pk, EVENT_STAFF_NEW_ORDER),
    ]


def test_replayed_checkout_sends_no_second_notice(
    django_capture_on_commit_callbacks,
) -> None:
    user, address_row = _ready_user()
    group = uuid4()

    with (
        patch("apps.orders.services.notify_order_event"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        first = place_cod(user, address_row, group=group)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        second = place_cod(user, address_row, group=group)

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert callbacks == []
    notify.assert_not_called()


def test_failed_placement_sends_nothing(django_capture_on_commit_callbacks) -> None:
    user = UserFactory()
    serve()
    address_row = address(user)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(EmptyCartError),
    ):
        place_cod(user, address_row)

    assert callbacks == []
    notify.assert_not_called()


def test_staff_cancel_sends_the_cancelled_notice(
    django_capture_on_commit_callbacks,
) -> None:
    _user, order = _cod_order()
    staff = UserFactory()

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        transition_order(
            order.pk,
            Order.Status.CANCELLED,
            actor=staff,
            reason="Out of stock",
            now=NOW,
        )

    assert len(callbacks) == 1
    notify.assert_called_once_with(order.pk, EVENT_CANCELLED)


def test_customer_cancel_sends_the_cancelled_notice(
    django_capture_on_commit_callbacks,
) -> None:
    user, order = _cod_order()

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        cancel_order_by_customer(user, order.pk, now=NOW)

    assert len(callbacks) == 1
    notify.assert_called_once_with(order.pk, EVENT_CANCELLED)


def test_packing_sends_no_notice(django_capture_on_commit_callbacks) -> None:
    _user, order = _cod_order()
    staff = UserFactory()

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        transition_order(order.pk, PACKING, actor=staff, now=NOW)

    assert callbacks == []
    notify.assert_not_called()


def test_out_for_delivery_sends_its_notice(
    django_capture_on_commit_callbacks,
) -> None:
    _user, order = _cod_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    assert len(callbacks) == 1
    notify.assert_called_once_with(order.pk, EVENT_OUT_FOR_DELIVERY)


def test_delivered_sends_its_notice(django_capture_on_commit_callbacks) -> None:
    _user, order = _cod_order()
    staff = UserFactory()
    transition_order(order.pk, PACKING, actor=staff, now=NOW)
    transition_order(order.pk, OUT_FOR_DELIVERY, actor=staff, now=NOW)

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        transition_order(order.pk, DELIVERED, actor=staff, now=NOW)

    assert len(callbacks) == 1
    notify.assert_called_once_with(order.pk, EVENT_DELIVERED)


def test_invalid_transition_sends_nothing(django_capture_on_commit_callbacks) -> None:
    _user, order = _cod_order()
    staff = UserFactory()
    assert order.status == Order.Status.CONFIRMED

    with (
        patch("apps.orders.services.notify_order_event") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(InvalidTransitionError),
    ):
        transition_order(order.pk, DELIVERED, actor=staff, now=NOW)

    assert callbacks == []
    notify.assert_not_called()


def test_notice_failure_never_breaks_placement(
    settings,
    django_capture_on_commit_callbacks,
) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.ORDER_EMAIL_ENABLED = True
    settings.SMS_BACKEND = FAILING_BACKEND
    set_settings(email=SHOP_EMAIL)
    user, address_row = _ready_user()

    with (
        patch("apps.notifications.order_notices.send_mail", side_effect=RuntimeError),
        django_capture_on_commit_callbacks(execute=True),
    ):
        placed = place_cod(user, address_row)

    order = Order.objects.get(pk=placed.order.pk)
    assert order.status == Order.Status.CONFIRMED
