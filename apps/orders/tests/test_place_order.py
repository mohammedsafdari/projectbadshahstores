"""Tests for placing, moving and cancelling orders (D64)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache

from apps.accounts.models import Address, User
from apps.accounts.services import archive_address, create_address
from apps.cart.models import CartItem
from apps.cart.services import add_item, build_cart
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import NumberSeriesCounter, ShopSettings
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.models import StockReservation
from apps.inventory.reservations import ReservationShortfallError
from apps.inventory.services import adjust_stock, receive_stock
from apps.inventory.tests.factories import StockReservationFactory
from apps.orders.models import Order, OrderItem, OrderStatusLog
from apps.orders.services import (
    AddressNotServedError,
    CancelReasonRequiredError,
    CartNotReadyError,
    CheckoutGroupInUseError,
    CodLimitExceededError,
    EmptyCartError,
    InvalidTransitionError,
    OrderAddressNotFoundError,
    OrderNotFoundError,
    PlacedOrder,
    cancel_order_by_customer,
    place_order,
    transition_order,
)
from apps.orders.state import (
    CANCELLED,
    CONFIRMED,
    PACKING,
    PAID,
    PAYMENT_PENDING,
    PLACED,
    REFUND_PENDING,
)
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=IST)
LATE = datetime(2026, 11, 7, 18, 0, tzinfo=IST)
PINCODE = "452012"


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _set_settings(**values: object) -> None:
    row = ShopSettings.load()
    for name, value in values.items():
        setattr(row, name, value)
    # update_fields takes a list of field names; the dict's keys are the names.
    row.save(update_fields=[*values, "updated_at"])


def _address(
    user: User,
    *,
    pincode: str = PINCODE,
    label: str = "HOME",
) -> Address:
    return create_address(
        user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12 Test Road",
        line2="Near the park",
        landmark="Opposite the school",
        city="Indore",
        state="Madhya Pradesh",
        pincode=pincode,
        label=label,
    )


def _serve(pincode: str = PINCODE) -> None:
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode=pincode)


def _variant(
    *,
    stock: str = "10.000",
    price: str = "100.00",
    mrp: str = "120.00",
    loose: bool = False,
    net_label: str = "",
) -> ProductVariant:
    variant = ProductVariantFactory(
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(mrp),
        net_quantity_label=net_label,
    )
    receive_stock(variant=variant, quantity=Decimal(stock))
    return variant


def _fill_cart(user: User, variant: ProductVariant, quantity: str = "2") -> None:
    add_item(DbCartStore(user), variant, Decimal(quantity))


def _place(
    user: User,
    address: Address,
    *,
    group: UUID | None = None,
    now: datetime = NOW,
    **kwargs: object,
) -> PlacedOrder:
    return place_order(
        user=user,
        address_id=address.pk,
        payment_method=Order.PaymentMethod.COD,
        checkout_group=group if group is not None else uuid4(),
        now=now,
        **kwargs,
    )


def test_place_order_creates_one_order_and_confirms_it() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant()
    _fill_cart(user, variant)

    result = _place(user, address)

    assert result.created is True
    assert result.order.status == Order.Status.CONFIRMED
    assert result.order.payment_method == Order.PaymentMethod.COD
    assert Order.objects.count() == 1


def test_place_order_writes_two_status_log_rows() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    result = _place(user, address)

    logs = list(OrderStatusLog.objects.filter(order=result.order))
    assert len(logs) == 2
    assert logs[0].from_status == ""
    assert logs[0].to_status == PLACED
    assert logs[0].actor == user
    assert logs[1].from_status == PLACED
    assert logs[1].to_status == CONFIRMED
    assert logs[1].actor == user


def test_place_order_holds_stock_for_fulfilment_and_adopts_the_group() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant()
    _fill_cart(user, variant)
    group = uuid4()

    result = _place(user, address, group=group)

    assert result.order.checkout_group == group
    holds = list(StockReservation.objects.filter(checkout_group=group))
    assert len(holds) == 1
    hold = holds[0]
    assert hold.user_id == user.pk
    assert hold.variant_id == variant.pk
    assert hold.quantity == Decimal("2.000")
    assert hold.kind == StockReservation.Kind.HELD_FOR_FULFILMENT
    assert hold.status == StockReservation.Status.ACTIVE
    assert hold.expires_at is None


def test_place_order_freezes_the_address() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address).order

    assert order.ship_name == address.recipient_name
    assert order.ship_phone == address.recipient_phone
    assert order.ship_line1 == address.line1
    assert order.ship_line2 == address.line2
    assert order.ship_landmark == address.landmark
    assert order.ship_city == address.city
    assert order.ship_state == address.state
    assert order.ship_pincode == address.pincode
    assert order.ship_label == address.label


def test_place_order_freezes_the_cart_money() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address).order

    assert order.item_total == Decimal("200.00")
    assert order.delivery_fee == Decimal("30.00")
    assert order.grand_total == Decimal("230.00")
    assert order.savings_total == Decimal("40.00")
    assert order.amount_due_on_delivery == Decimal("230.00")


def test_place_order_freezes_each_line() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant()
    _fill_cart(user, variant)

    order = _place(user, address).order

    item = OrderItem.objects.get(order=order)
    assert item.sku_code == variant.sku_code
    assert item.product_name == variant.product.name_en
    assert item.pack_label == "1 packet"
    assert item.unit == variant.unit
    assert item.hsn_code == variant.hsn_code
    assert item.gst_rate_percent == variant.gst_rate_percent
    assert item.mrp == variant.mrp
    assert item.unit_price == variant.selling_price
    assert item.quantity_ordered == Decimal("2.000")
    assert item.quantity_fulfilled == Decimal("0.000")
    assert item.line_total == Decimal("200.00")
    assert item.item_status == OrderItem.ItemStatus.PENDING


def test_place_order_uses_the_variant_net_quantity_label() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant(net_label="500 g")
    _fill_cart(user, variant)

    order = _place(user, address).order

    assert OrderItem.objects.get(order=order).pack_label == "500 g"


def test_place_order_promises_today_before_the_cutoff() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address).order

    assert order.promised_delivery_date == NOW.date()
    assert order.delivery_is_same_day is True


def test_place_order_promises_tomorrow_after_the_cutoff() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address, now=LATE).order

    assert order.promised_delivery_date == LATE.date() + timedelta(days=1)
    assert order.delivery_is_same_day is False


def test_place_order_stores_the_substitution_preference_and_note() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    first = _place(
        user,
        address,
        substitution_preference=Order.SubstitutionPreference.SUBSTITUTE,
        customer_note="Leave at the door",
    ).order

    assert first.substitution_preference == Order.SubstitutionPreference.SUBSTITUTE
    assert first.customer_note == "Leave at the door"

    # The second order keeps the defaults when the caller passes nothing.
    _fill_cart(user, _variant())
    second = _place(user, address).order

    assert second.substitution_preference == Order.SubstitutionPreference.CALL_ME
    assert second.customer_note == ""


def test_place_order_clears_the_cart() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    _place(user, address)

    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_place_order_falls_back_to_the_bs_prefix() -> None:
    _set_settings(order_number_prefix="")
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address).order

    assert order.order_number == "BS/26-27/000001"


def test_place_order_uses_a_configured_prefix_without_spaces() -> None:
    _set_settings(order_number_prefix="  SHOP  ")
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    order = _place(user, address).order

    assert order.order_number == "SHOP/26-27/000001"


def test_place_order_increments_the_number_for_a_second_order() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    first = _place(user, address).order
    _fill_cart(user, _variant())
    second = _place(user, address).order

    assert first.order_number == "BS/26-27/000001"
    assert second.order_number == "BS/26-27/000002"


def test_place_order_returns_the_existing_order_for_the_same_group() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    group = uuid4()

    first = _place(user, address, group=group)
    second = _place(user, address, group=group)

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert Order.objects.count() == 1
    assert OrderStatusLog.objects.count() == 2
    assert StockReservation.objects.count() == 1


def test_place_order_refuses_a_group_used_by_another_customer() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    first = _place(user, address)

    other = UserFactory()
    with pytest.raises(CheckoutGroupInUseError):
        _place(other, address, group=first.order.checkout_group)

    assert Order.objects.count() == 1


def test_place_order_refuses_missing_archived_or_foreign_addresses() -> None:
    owner = UserFactory()
    archived = _address(owner, pincode="452013")
    archive_address(owner, archived.pk)
    foreign_user = UserFactory()
    foreign = _address(foreign_user, pincode="452014")

    cases = [
        (owner, 999999),
        (owner, archived.pk),
        (UserFactory(), foreign.pk),
    ]
    assert cases
    for caller, address_id in cases:
        with pytest.raises(OrderAddressNotFoundError):
            place_order(
                user=caller,
                address_id=address_id,
                payment_method=Order.PaymentMethod.COD,
                checkout_group=uuid4(),
                now=NOW,
            )


def test_place_order_refuses_an_unserved_pin_code() -> None:
    user = UserFactory()
    address = _address(user, pincode="452099")

    with pytest.raises(AddressNotServedError):
        _place(user, address)


def test_place_order_refuses_an_empty_cart() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()

    with pytest.raises(EmptyCartError):
        _place(user, address)

    assert Order.objects.count() == 0


def test_place_order_refuses_a_cart_that_is_not_ready() -> None:
    low_user = UserFactory()
    low_address = _address(low_user)
    _serve()
    _fill_cart(low_user, _variant(), "1")

    problem_user = UserFactory()
    problem_address = _address(problem_user, pincode="452013")
    _serve("452013")
    problem_variant = _variant()
    _fill_cart(problem_user, problem_variant)
    adjust_stock(variant=problem_variant, quantity_change=Decimal("-10"), reason="Test")

    cases = [
        (low_user, low_address, "minimum order value"),
        (problem_user, problem_address, "not available"),
    ]
    assert cases
    for user, address, expected in cases:
        with pytest.raises(CartNotReadyError) as error:
            _place(user, address)
        assert expected in error.value.reason


def test_place_order_refuses_above_the_cod_cap_but_accepts_the_cap() -> None:
    _set_settings(cod_cap=Decimal("200.00"))
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())

    with pytest.raises(CodLimitExceededError):
        _place(user, address)

    _set_settings(cod_cap=Decimal("230.00"))
    order = _place(user, address).order

    assert order.grand_total == Decimal("230.00")


def test_place_order_rolls_back_everything_when_stock_is_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant()
    _fill_cart(user, variant)
    # The summary is built before the stock disappears, so checkout still sees
    # an OK line and reaches reserve_stock, where the shortfall is caught.
    stale_summary = build_cart(DbCartStore(user))
    adjust_stock(variant=variant, quantity_change=Decimal("-9"), reason="Test")
    monkeypatch.setattr("apps.orders.services.build_cart", lambda store: stale_summary)

    with pytest.raises(ReservationShortfallError):
        _place(user, address)

    assert Order.objects.count() == 0
    assert OrderStatusLog.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert NumberSeriesCounter.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_place_order_reserves_each_cart_line_once() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    first = _variant()
    second = _variant()
    _fill_cart(user, first)
    _fill_cart(user, second)

    order = _place(user, address).order

    assert order.items.count() == 2
    assert StockReservation.objects.filter(checkout_group=order.checkout_group).count() == 2


def test_place_order_keeps_a_loose_quantity_in_thousandths() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    variant = _variant(stock="2.000", price="500.00", mrp="600.00", loose=True)
    _fill_cart(user, variant, "0.500")

    order = _place(user, address).order

    item = OrderItem.objects.get(order=order)
    assert item.quantity_ordered == Decimal("0.500")
    assert item.line_total == Decimal("250.00")
    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.quantity == Decimal("0.500")


def test_transition_order_moves_confirmed_to_packing_and_moves_updated_at() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)
    before = placed.order.updated_at

    updated = transition_order(
        placed.order.pk,
        Order.Status.PACKING,
        actor=user,
        note="Packing started",
        now=NOW,
    )

    assert updated.status == Order.Status.PACKING
    assert updated.updated_at > before
    log = OrderStatusLog.objects.filter(order=updated).last()
    assert log is not None
    assert log.from_status == CONFIRMED
    assert log.to_status == PACKING
    assert log.note == "Packing started"


def test_transition_order_refuses_an_unknown_order() -> None:
    with pytest.raises(OrderNotFoundError):
        transition_order(999999, PACKING, actor=UserFactory())


def test_transition_order_refuses_an_illegal_or_repeated_move() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)

    cases = [Order.Status.PLACED, Order.Status.CONFIRMED, "BOGUS"]
    assert cases
    for target in cases:
        with pytest.raises(InvalidTransitionError):
            transition_order(placed.order.pk, target, actor=user, now=NOW)


def test_transition_order_refuses_a_cod_payment_status() -> None:
    user = UserFactory()
    cases = [
        (OrderFactory(user=user, status=PLACED), PAYMENT_PENDING),
        (OrderFactory(user=user, status=PAID), REFUND_PENDING),
    ]
    assert cases
    for order, target in cases:
        with pytest.raises(InvalidTransitionError, match="COD"):
            transition_order(order.pk, target, actor=user, now=NOW)


def test_transition_order_requires_a_reason_to_cancel() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)

    with pytest.raises(CancelReasonRequiredError):
        transition_order(placed.order.pk, CANCELLED, actor=user, reason="   ")

    placed.order.refresh_from_db()
    assert placed.order.status == CONFIRMED
    assert (
        StockReservation.objects.filter(
            checkout_group=placed.order.checkout_group,
            status=StockReservation.Status.ACTIVE,
        ).count()
        == 1
    )


def test_transition_order_cancels_the_order_releases_stock_and_cancels_items() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)
    item = placed.order.items.get()
    transition_order(placed.order.pk, PACKING, actor=user, now=NOW)
    before_item = item.updated_at
    cancel_now = datetime(2026, 11, 7, 13, 0, tzinfo=IST)

    cancelled = transition_order(
        placed.order.pk,
        CANCELLED,
        actor=user,
        reason="Customer called",
        now=cancel_now,
    )

    assert cancelled.status == Order.Status.CANCELLED
    assert cancelled.cancel_reason == "Customer called"
    assert cancelled.cancelled_at == cancel_now
    hold = StockReservation.objects.get(checkout_group=cancelled.checkout_group)
    assert hold.status == StockReservation.Status.RELEASED
    assert hold.released_at == cancel_now
    item.refresh_from_db()
    assert item.item_status == OrderItem.ItemStatus.CANCELLED
    assert item.updated_at > before_item


def test_cancel_reason_is_stripped_and_cut_to_200_characters() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)
    reason = "  " + "x" * 250 + "  "

    cancelled = transition_order(
        placed.order.pk,
        CANCELLED,
        actor=user,
        reason=reason,
        now=NOW,
    )

    assert cancelled.cancel_reason == "x" * 200
    log = OrderStatusLog.objects.filter(order=cancelled, to_status=CANCELLED).get()
    assert len(log.note) <= 200


def test_a_delivered_order_cannot_be_cancelled() -> None:
    order = OrderFactory(status=Order.Status.DELIVERED, delivered_at=NOW)

    with pytest.raises(InvalidTransitionError):
        transition_order(order.pk, CANCELLED, actor=order.user, reason="Too late", now=NOW)

    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED


def test_a_customer_cancels_a_placed_order_and_the_hold_is_released() -> None:
    order = OrderFactory(status=Order.Status.PLACED)
    hold = StockReservationFactory(
        checkout_group=order.checkout_group,
        user=order.user,
    )
    item = OrderItemFactory(order=order)

    cancelled = cancel_order_by_customer(order.user, order.pk, now=NOW)

    assert cancelled.status == Order.Status.CANCELLED
    assert cancelled.cancel_reason == ""
    assert cancelled.cancelled_at == NOW
    hold.refresh_from_db()
    assert hold.status == StockReservation.Status.RELEASED
    assert hold.released_at == NOW
    item.refresh_from_db()
    assert item.item_status == OrderItem.ItemStatus.CANCELLED


def test_a_customer_cancels_a_confirmed_order_and_keeps_the_reason() -> None:
    user = UserFactory()
    address = _address(user)
    _serve()
    _fill_cart(user, _variant())
    placed = _place(user, address)

    cancelled = cancel_order_by_customer(
        user,
        placed.order.pk,
        reason="  changed my mind  ",
        now=NOW,
    )

    assert cancelled.status == Order.Status.CANCELLED
    assert cancelled.cancel_reason == "changed my mind"
    hold = StockReservation.objects.get(checkout_group=cancelled.checkout_group)
    assert hold.status == StockReservation.Status.RELEASED
    assert placed.order.items.get().item_status == OrderItem.ItemStatus.CANCELLED


def test_customer_cancel_refuses_packed_cancelled_and_foreign_orders() -> None:
    user = UserFactory()
    packed = OrderFactory(user=user, status=PACKING)
    cancelled = OrderFactory(
        user=user,
        status=Order.Status.CANCELLED,
        cancelled_at=NOW,
    )
    foreign = OrderFactory()

    cases = [
        (user, packed.pk, InvalidTransitionError),
        (user, cancelled.pk, InvalidTransitionError),
        (user, foreign.pk, OrderNotFoundError),
    ]
    assert cases
    for caller, order_id, expected in cases:
        with pytest.raises(expected):
            cancel_order_by_customer(caller, order_id, now=NOW)
