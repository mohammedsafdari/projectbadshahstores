"""Tests for placing an order with a coupon code (D82)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.cart.models import CartItem
from apps.cart.services import build_cart
from apps.cart.stores import DbCartStore
from apps.core.models import NumberSeriesCounter
from apps.inventory.models import StockReservation
from apps.inventory.reservations import ReservationShortfallError
from apps.inventory.services import adjust_stock
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import (
    CodLimitExceededError,
    CouponRefusedError,
)
from apps.orders.tests.checkout_helpers import (
    NOW,
    clear_settings,
    credit_account,
    fill_cart,
    set_settings,
)
from apps.orders.tests.coupon_helpers import ZERO, place, ready
from apps.orders.tests.factories import OrderFactory
from apps.promotions.models import Coupon, CouponRedemption
from apps.promotions.rules import MESSAGES, CouponRefusal
from apps.promotions.tests.helpers import make_coupon, make_redemption
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _empty_cache():
    """Leave no cached settings behind for the next test."""
    yield
    clear_settings()


def _refusal(user, address_row, *, code: str = "DIWALI10") -> CouponRefusedError:
    """Place one order that a coupon refuses and return the error."""
    with pytest.raises(CouponRefusedError) as error:
        place(user, address_row, coupon_code=code)
    return error.value


def _message(reason: str) -> str:
    """The one customer sentence of a refusal reason."""
    return str(MESSAGES[reason])


# --- the amounts a working coupon sets ---------------------------------------


def test_a_percent_coupon_sets_the_item_discount() -> None:
    user, address_row, _row = ready()
    make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.item_total == Decimal("200.00")
    assert order.delivery_fee == Decimal("30.00")
    assert order.discount_total == Decimal("20.00")
    assert order.delivery_discount == ZERO
    assert order.grand_total == Decimal("210.00")


def test_a_percent_coupon_sets_the_cash_part() -> None:
    user, address_row, _row = ready()
    make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.amount_due_on_delivery == Decimal("210.00")
    assert order.credit_applied == ZERO
    assert order.online_amount == ZERO


def test_a_percent_coupon_sets_the_khata_part() -> None:
    user, address_row, _row = ready()
    credit_account(user, limit="1000.00")
    make_coupon()

    order = place(
        user,
        address_row,
        method=Order.PaymentMethod.CREDIT,
        coupon_code="DIWALI10",
    ).order

    assert order.credit_applied == Decimal("210.00")
    assert order.amount_due_on_delivery == ZERO


def test_a_fixed_coupon_sets_every_amount() -> None:
    user, address_row, _row = ready()
    make_coupon(kind=Coupon.Kind.FIXED, amount_off=Decimal("50.00"))

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.discount_total == Decimal("50.00")
    assert order.grand_total == Decimal("180.00")
    assert order.amount_due_on_delivery == Decimal("180.00")


def test_a_percent_coupon_stops_at_its_maximum() -> None:
    user, address_row, _row = ready()
    make_coupon(percent_off=Decimal("50.00"), max_discount=Decimal("30.00"))

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.discount_total == Decimal("30.00")
    assert order.grand_total == Decimal("200.00")


def test_a_free_delivery_coupon_takes_the_whole_fee() -> None:
    user, address_row, _row = ready()
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.discount_total == ZERO
    assert order.delivery_discount == Decimal("30.00")
    # The fee column keeps the full cart fee; only the discount column moves.
    assert order.delivery_fee == Decimal("30.00")
    assert order.grand_total == Decimal("200.00")


def test_the_khata_part_is_capped_by_the_available_credit() -> None:
    user, address_row, _row = ready()
    credit_account(user, limit="100.00")
    make_coupon()

    order = place(
        user,
        address_row,
        method=Order.PaymentMethod.CREDIT,
        coupon_code="DIWALI10",
    ).order

    assert order.credit_applied == Decimal("100.00")
    assert order.amount_due_on_delivery == Decimal("110.00")


def test_an_online_order_pays_the_discounted_remainder(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    user, address_row, _row = ready()
    make_coupon()

    order = place(
        user,
        address_row,
        remainder=Order.PaymentMethod.ONLINE,
        coupon_code="DIWALI10",
    ).order

    assert order.online_amount == Decimal("210.00")
    assert order.amount_due_on_delivery == ZERO
    assert order.status == Order.Status.PAYMENT_PENDING


# --- no coupon ---------------------------------------------------------------


def test_no_code_leaves_every_amount_as_before() -> None:
    user, address_row, _row = ready()

    order = place(user, address_row).order

    assert order.discount_total == ZERO
    assert order.delivery_discount == ZERO
    assert order.coupon_code == ""
    assert order.grand_total == Decimal("230.00")
    assert order.amount_due_on_delivery == Decimal("230.00")
    assert CouponRedemption.objects.count() == 0


def test_a_whitespace_code_is_no_coupon() -> None:
    user, address_row, _row = ready()
    make_coupon()

    order = place(user, address_row, coupon_code="   ").order

    assert order.coupon_code == ""
    assert order.grand_total == Decimal("230.00")
    assert CouponRedemption.objects.count() == 0


# --- the redemption row ------------------------------------------------------


def test_the_code_is_stored_in_capitals() -> None:
    user, address_row, _row = ready()
    make_coupon(code="DIWALI-10")

    order = place(user, address_row, coupon_code="diwali-10").order

    assert order.coupon_code == "DIWALI-10"


def test_the_redemption_row_holds_the_item_discount() -> None:
    user, address_row, _row = ready()
    coupon = make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    redemption = CouponRedemption.objects.get(order=order)
    assert redemption.coupon_id == coupon.pk
    assert redemption.user_id == user.pk
    assert redemption.discount_amount == Decimal("20.00")
    assert redemption.delivery_discount_amount == ZERO
    assert redemption.released_at is None


def test_the_redemption_row_holds_the_delivery_discount() -> None:
    user, address_row, _row = ready()
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    order = place(user, address_row, coupon_code="DIWALI10").order

    redemption = CouponRedemption.objects.get(order=order)
    assert redemption.discount_amount == ZERO
    assert redemption.delivery_discount_amount == Decimal("30.00")


def test_the_order_and_the_redemption_name_the_same_code() -> None:
    user, address_row, _row = ready()
    coupon = make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert CouponRedemption.objects.get(order=order).coupon == coupon
    assert order.coupon_code == coupon.code


# --- every refusal reason ----------------------------------------------------


def test_an_unknown_code_is_refused() -> None:
    user, address_row, _row = ready()

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.NOT_FOUND
    assert error.message == _message(CouponRefusal.NOT_FOUND)


def test_a_switched_off_code_is_refused_at_order_time() -> None:
    user, address_row, _row = ready()
    coupon = make_coupon()
    # The coupon was live when the cart was built and is switched off before
    # the order is placed, so the checkout itself must refuse it.
    Coupon.objects.filter(pk=coupon.pk).update(is_active=False)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.INACTIVE
    assert error.message == _message(CouponRefusal.INACTIVE)


def test_a_code_that_has_not_started_is_refused() -> None:
    user, address_row, _row = ready()
    make_coupon(starts_at=NOW + timedelta(days=1), ends_at=None)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.NOT_STARTED


def test_an_expired_code_is_refused() -> None:
    user, address_row, _row = ready()
    make_coupon(ends_at=NOW - timedelta(days=1))

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.EXPIRED


def test_a_cart_below_the_minimum_is_refused_with_the_shortfall() -> None:
    user, address_row, _row = ready()
    make_coupon(min_order_value=Decimal("500.00"))

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.BELOW_MINIMUM
    # The cart is 200.00, so 300.00 more is needed.
    assert "300.00" in error.message


def test_a_first_order_code_is_refused_after_a_delivered_order() -> None:
    user, address_row, _row = ready()
    OrderFactory(user=user, status=Order.Status.DELIVERED, delivered_at=NOW)
    make_coupon(first_order_only=True)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.NOT_FIRST_ORDER


def test_the_total_limit_refuses_the_next_use() -> None:
    user, address_row, _row = ready()
    coupon = make_coupon(total_limit=1)
    make_redemption(coupon, user=UserFactory())

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.TOTAL_LIMIT_REACHED


def test_the_per_customer_limit_refuses_the_same_customer() -> None:
    user, address_row, _row = ready()
    coupon = make_coupon()
    make_redemption(coupon, user=user)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.PER_CUSTOMER_LIMIT_REACHED


def test_a_code_that_saves_nothing_is_refused() -> None:
    user, address_row, _row = ready()
    # Free delivery is given already, so the coupon has nothing left to take.
    set_settings(free_delivery_threshold=ZERO)
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.NO_BENEFIT


def test_a_refusal_writes_no_order_hold_or_redemption() -> None:
    user, address_row, _row = ready()
    make_coupon(min_order_value=Decimal("500.00"))

    _refusal(user, address_row)

    assert Order.objects.filter(user=user).count() == 0
    assert OrderStatusLog.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CouponRedemption.objects.count() == 0
    assert NumberSeriesCounter.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_a_refusal_leaves_the_checkout_group_free() -> None:
    user, address_row, _row = ready()
    make_coupon(min_order_value=Decimal("500.00"))
    group = uuid4()

    with pytest.raises(CouponRefusedError):
        place(user, address_row, group=group, coupon_code="DIWALI10")

    # The same group can still be used once the coupon problem is fixed.
    Coupon.objects.filter(code="DIWALI10").update(min_order_value=ZERO)
    placed = place(user, address_row, group=group, coupon_code="DIWALI10")

    assert placed.created is True


# --- replaying one checkout --------------------------------------------------


def test_a_replay_returns_the_same_order() -> None:
    user, address_row, _row = ready()
    make_coupon()
    group = uuid4()

    first = place(user, address_row, group=group, coupon_code="DIWALI10")
    second = place(user, address_row, group=group, coupon_code="DIWALI10")

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert second.order.grand_total == Decimal("210.00")


def test_a_replay_writes_no_second_redemption() -> None:
    user, address_row, row = ready()
    make_coupon()
    group = uuid4()

    place(user, address_row, group=group, coupon_code="DIWALI10")
    place(user, address_row, group=group, coupon_code="DIWALI10")

    assert CouponRedemption.objects.count() == 1
    assert Order.objects.filter(user=user).count() == 1
    assert row.pk is not None


def test_a_replay_ignores_a_different_code() -> None:
    user, address_row, _row = ready()
    make_coupon()
    group = uuid4()

    first = place(user, address_row, group=group, coupon_code="DIWALI10")
    # The second code does not exist, yet the replay must not check anything.
    second = place(user, address_row, group=group, coupon_code="OTHER10")

    assert second.order.pk == first.order.pk
    assert CouponRedemption.objects.count() == 1


# --- the limits over two orders ----------------------------------------------


def test_the_per_customer_limit_refuses_a_second_order() -> None:
    user, address_row, row = ready()
    make_coupon()
    place(user, address_row, coupon_code="DIWALI10")
    fill_cart(user, row)

    error = _refusal(user, address_row)

    assert error.reason == CouponRefusal.PER_CUSTOMER_LIMIT_REACHED


def test_the_total_limit_refuses_the_next_customer() -> None:
    user, address_row, _row = ready()
    make_coupon(total_limit=1)
    place(user, address_row, coupon_code="DIWALI10")
    other_user, other_address, _other_row = ready()

    with pytest.raises(CouponRefusedError) as error:
        place(other_user, other_address, coupon_code="DIWALI10")

    assert error.value.reason == CouponRefusal.TOTAL_LIMIT_REACHED


# --- zero total and the cash-on-delivery cap ---------------------------------


def test_a_zero_total_order_is_placed_as_cash() -> None:
    user, address_row, _row = ready()
    # Free delivery already, so a 100% coupon leaves nothing to pay.
    set_settings(free_delivery_threshold=ZERO)
    make_coupon(percent_off=Decimal("100.00"))

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.grand_total == ZERO
    assert order.payment_method == Order.PaymentMethod.COD
    assert order.amount_due_on_delivery == ZERO
    assert order.online_amount == ZERO
    assert order.status == Order.Status.CONFIRMED


def test_the_cod_cap_is_checked_on_the_discounted_remainder() -> None:
    user, address_row, _row = ready()
    set_settings(cod_cap=Decimal("200.00"))
    make_coupon(kind=Coupon.Kind.FIXED, amount_off=Decimal("50.00"))

    with pytest.raises(CodLimitExceededError):
        place(user, address_row)

    # 230.00 is above the 200.00 cap; the coupon brings it to 180.00.
    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.grand_total == Decimal("180.00")
    assert order.amount_due_on_delivery == Decimal("180.00")


def test_an_order_still_above_the_cap_is_refused() -> None:
    user, address_row, _row = ready()
    set_settings(cod_cap=Decimal("100.00"))
    make_coupon(kind=Coupon.Kind.FIXED, amount_off=Decimal("50.00"))

    with pytest.raises(CodLimitExceededError):
        place(user, address_row, coupon_code="DIWALI10")

    assert Order.objects.filter(user=user).count() == 0


# --- the shop's other money rules still use the item total --------------------


def test_free_delivery_is_still_judged_on_the_item_total() -> None:
    user, address_row, _row = ready(price="300.00", mrp="400.00")
    # 600.00 clears the 500.00 threshold, so the cart charges no fee at all.
    make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.delivery_fee == ZERO
    assert order.delivery_discount == ZERO
    assert order.discount_total == Decimal("60.00")
    assert order.grand_total == Decimal("540.00")


def test_a_big_coupon_does_not_earn_free_delivery() -> None:
    user, address_row, _row = ready()
    make_coupon(percent_off=Decimal("50.00"))

    order = place(user, address_row, coupon_code="DIWALI10").order

    # The 200.00 item total stays under the 500.00 threshold, so the fee stands.
    assert order.delivery_fee == Decimal("30.00")
    assert order.grand_total == Decimal("130.00")


def test_the_minimum_order_is_judged_before_the_discount() -> None:
    user, address_row, _row = ready()
    set_settings(minimum_order_value=Decimal("200.00"))
    make_coupon(percent_off=Decimal("50.00"))

    order = place(user, address_row, coupon_code="DIWALI10").order

    # The discounted 100.00 is below the minimum; the 200.00 item total is not.
    assert order.grand_total == Decimal("130.00")


def test_the_mrp_savings_are_not_changed() -> None:
    user, address_row, _row = ready()
    make_coupon()

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.savings_total == Decimal("40.00")


def test_a_first_order_coupon_works_for_a_new_customer() -> None:
    user, address_row, _row = ready()
    make_coupon(first_order_only=True)

    order = place(user, address_row, coupon_code="DIWALI10").order

    assert order.discount_total == Decimal("20.00")


# --- a shortfall -------------------------------------------------------------


def test_a_stock_shortage_rolls_the_redemption_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, address_row, row = ready()
    make_coupon()
    # The cart summary is built before the stock disappears, so the checkout
    # still reaches the reservation, where the shortfall is caught.
    stale_summary = build_cart(DbCartStore(user))
    adjust_stock(variant=row, quantity_change=Decimal("-9"), reason="Test")
    monkeypatch.setattr("apps.orders.services.build_cart", lambda store: stale_summary)

    with pytest.raises(ReservationShortfallError):
        place(user, address_row, coupon_code="DIWALI10")

    assert Order.objects.filter(user=user).count() == 0
    assert CouponRedemption.objects.count() == 0
    assert NumberSeriesCounter.objects.count() == 0
