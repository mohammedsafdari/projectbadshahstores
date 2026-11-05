"""Tests for the customer address book (Slice 29f, ADR D57).

The model tests cover the defaults, the pin code validator and the partial
unique constraint. The service tests cover the per-user access rule, the
5-address limit, the default promotion rules and the two timestamps. The last
test starts a thread, because ``create_address`` locks the user row and must
wait for a transaction that holds it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from apps.accounts.models import Address, User
from apps.accounts.services import (
    MAX_ACTIVE_ADDRESSES,
    AddressLimitReached,
    AddressNotFound,
    archive_address,
    create_address,
    get_address,
    list_addresses,
    set_default_address,
    update_address,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user(phone: str = "+919876543210") -> User:
    return User.objects.create_user(phone=phone, password="ok")


def _base(**changes: Any) -> dict[str, Any]:
    """The four required address values, with any override applied."""
    data: dict[str, Any] = {
        "recipient_name": "Asha Sharma",
        "recipient_phone": "9876543210",
        "line1": "12, Saifee Nagar",
        "pincode": "452012",
    }
    data.update(changes)
    return data


def _save(user: User, **changes: Any) -> Address:
    """A saved address made with the model directly, for the constraint tests."""
    return Address.objects.create(user=user, **_base(**changes))


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
def test_address_defaults_city_and_state() -> None:
    address = _save(_user())

    assert address.city == "Indore"
    assert address.state == "Madhya Pradesh"
    assert address.label == Address.Label.HOME
    assert address.is_default is False
    assert address.is_active is True
    assert address.line2 == ""
    assert address.landmark == ""


def test_pincode_with_a_leading_zero_is_refused() -> None:
    address = Address(user=_user(), **_base(pincode="052012"))

    with pytest.raises(ValidationError):
        address.full_clean()


def test_pincode_with_five_digits_is_refused() -> None:
    address = Address(user=_user(), **_base(pincode="45201"))

    with pytest.raises(ValidationError):
        address.full_clean()


def test_devanagari_text_is_accepted() -> None:
    address = Address(
        user=_user(),
        recipient_name="मोहन शर्मा",
        recipient_phone="9876543210",
        line1="१२, सैफी नगर",
        city="इंदौर",
        state="मध्य प्रदेश",
        pincode="452012",
    )

    address.full_clean()
    address.save()
    address.refresh_from_db()

    assert address.recipient_name == "मोहन शर्मा"
    assert address.city == "इंदौर"
    assert address.state == "मध्य प्रदेश"


def test_two_active_defaults_for_one_user_are_refused() -> None:
    user = _user()
    _save(user, is_default=True)

    # One pytest.raises per test: the transaction is unusable after the error,
    # so the inner atomic() block keeps the test itself usable.
    with pytest.raises(IntegrityError), transaction.atomic():
        _save(user, is_default=True)

    assert Address.objects.filter(user=user, is_default=True).count() == 1


def test_an_archived_default_does_not_block_a_new_default() -> None:
    user = _user()
    _save(user, is_default=True, is_active=False)

    second = _save(user, is_default=True)

    assert second.pk is not None


def test_two_users_can_each_have_a_default() -> None:
    first_user = _user("+919876543210")
    second_user = _user("+919876543211")

    first = _save(first_user, is_default=True)
    second = _save(second_user, is_default=True)

    assert first.is_default is True
    assert second.is_default is True


# ---------------------------------------------------------------------------
# The service: creating and the default
# ---------------------------------------------------------------------------
def test_first_address_becomes_default() -> None:
    address = create_address(_user(), **_base())

    assert address.is_default is True
    assert Address.objects.get(pk=address.pk).is_default is True


def test_second_address_is_not_default() -> None:
    user = _user()
    first = create_address(user, **_base())
    second = create_address(user, **_base(line1="99, New Palasia"))

    assert first.is_default is True
    assert second.is_default is False


def test_set_default_switches_the_default() -> None:
    user = _user()
    first = create_address(user, **_base())
    second = create_address(user, **_base(line1="99, New Palasia"))

    set_default_address(user, second.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_default is False
    assert second.is_default is True


def test_list_puts_the_default_first_even_when_it_is_the_newest() -> None:
    user = _user()
    first = create_address(user, **_base())
    second = create_address(user, **_base(line1="99, New Palasia"))
    set_default_address(user, second.pk)

    result = list_addresses(user)

    # Ordering by pk alone would return the older first address, so this fails
    # if the default rule is dropped from list_addresses.
    assert [address.pk for address in result] == [second.pk, first.pk]


def test_archiving_the_default_promotes_the_oldest_remaining() -> None:
    user = _user()
    first = create_address(user, **_base())
    second = create_address(user, **_base(line1="99, New Palasia"))
    third = create_address(user, **_base(line1="7, Vijay Nagar"))

    archive_address(user, first.pk)

    second.refresh_from_db()
    third.refresh_from_db()
    assert second.is_default is True
    assert third.is_default is False
    assert list_addresses(user)[0].pk == second.pk


def test_archiving_the_last_address_leaves_no_default() -> None:
    user = _user()
    only = create_address(user, **_base())

    archive_address(user, only.pk)

    assert Address.objects.filter(user=user, is_active=True).count() == 0
    assert Address.objects.filter(user=user, is_default=True).count() == 0


# ---------------------------------------------------------------------------
# The service: the limit
# ---------------------------------------------------------------------------
def test_sixth_active_address_is_refused() -> None:
    user = _user()
    for number in range(MAX_ACTIVE_ADDRESSES):
        create_address(user, **_base(line1=f"{number}, Saifee Nagar"))

    with pytest.raises(AddressLimitReached):
        create_address(user, **_base(line1="6, Saifee Nagar"))

    assert Address.objects.filter(user=user, is_active=True).count() == MAX_ACTIVE_ADDRESSES


def test_archived_addresses_do_not_count_toward_the_limit() -> None:
    user = _user()
    addresses = [
        create_address(user, **_base(line1=f"{number}, Saifee Nagar"))
        for number in range(MAX_ACTIVE_ADDRESSES)
    ]
    archive_address(user, addresses[-1].pk)

    sixth = create_address(user, **_base(line1="6, Saifee Nagar"))

    assert Address.objects.filter(user=user, is_active=True).count() == MAX_ACTIVE_ADDRESSES
    assert sixth.is_active is True


# ---------------------------------------------------------------------------
# The service: the phone
# ---------------------------------------------------------------------------
def test_recipient_phone_is_normalised() -> None:
    address = create_address(_user(), **_base(recipient_phone="98765 43210"))

    assert address.recipient_phone == "+919876543210"


def test_a_bad_phone_is_refused() -> None:
    with pytest.raises(ValueError):
        create_address(_user(), **_base(recipient_phone="12345"))


# ---------------------------------------------------------------------------
# The service: per-user access
# ---------------------------------------------------------------------------
def test_get_address_returns_the_users_own_address() -> None:
    user = _user()
    address = create_address(user, **_base())

    found = get_address(user, address.pk)

    assert found.pk == address.pk


def test_get_address_of_another_user_is_not_found() -> None:
    owner = _user("+919876543210")
    other = _user("+919876543211")
    address = create_address(owner, **_base())

    with pytest.raises(AddressNotFound):
        get_address(other, address.pk)


def test_archiving_another_users_address_is_not_found_and_changes_nothing() -> None:
    owner = _user("+919876543210")
    other = _user("+919876543211")
    address = create_address(owner, **_base())

    with pytest.raises(AddressNotFound):
        archive_address(other, address.pk)

    address.refresh_from_db()
    assert address.is_active is True


def test_updating_another_users_address_is_not_found() -> None:
    owner = _user("+919876543210")
    other = _user("+919876543211")
    address = create_address(owner, **_base())

    with pytest.raises(AddressNotFound):
        update_address(other, address.pk, line1="Changed by someone else")

    address.refresh_from_db()
    assert address.line1 == "12, Saifee Nagar"


def test_list_returns_only_the_users_active_addresses_in_order() -> None:
    user = _user("+919876543210")
    other = _user("+919876543211")
    first = create_address(user, **_base())
    second = create_address(user, **_base(line1="99, New Palasia"))
    archived = create_address(user, **_base(line1="7, Vijay Nagar"))
    create_address(other, **_base(line1="Elsewhere"))
    archive_address(user, archived.pk)

    result = list_addresses(user)

    assert [address.pk for address in result] == [first.pk, second.pk]


# ---------------------------------------------------------------------------
# The service: timestamps
# ---------------------------------------------------------------------------
def test_update_moves_updated_at() -> None:
    user = _user()
    address = create_address(user, **_base())
    address.refresh_from_db()  # read the timestamp the database really stores
    before = address.updated_at

    update_address(user, address.pk, line1="99, New Palasia")

    address.refresh_from_db()
    assert address.line1 == "99, New Palasia"
    assert address.updated_at > before


def test_archive_moves_updated_at() -> None:
    user = _user()
    address = create_address(user, **_base())
    address.refresh_from_db()
    before = address.updated_at

    archive_address(user, address.pk)

    address.refresh_from_db()
    assert address.is_active is False
    assert address.updated_at > before


# ---------------------------------------------------------------------------
# The lock: creating an address must wait for a transaction holding the user row
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_create_waits_for_a_lock_on_the_user() -> None:
    user = _user()
    user_pk = user.pk

    locked = threading.Event()
    created: list[Address] = []
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        """Lock the user row and insert the first (default) address."""
        try:
            with transaction.atomic():
                User.objects.select_for_update().get(pk=user_pk)
                Address.objects.create(user_id=user_pk, is_default=True, **_base())
                locked.set()  # tell the other thread the lock is held
                time.sleep(0.5)  # hold it for a moment, so the other thread waits
            # Leaving the ``with`` block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()  # this thread's own database connection

    def create_while_locked() -> None:
        """Create a second address; it must wait, then see the first default."""
        try:
            assert locked.wait(timeout=10)
            creator = User.objects.get(pk=user_pk)
            created.append(create_address(creator, **_base(recipient_name="Second")))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_lock)
    creator = threading.Thread(target=create_while_locked)
    holder.start()
    creator.start()
    holder.join(timeout=30)
    creator.join(timeout=30)

    assert not errors, errors
    assert len(created) == 1
    # Without select_for_update() the second thread would see no default, set
    # its own address as default and hit the unique constraint at commit. With
    # the lock it sees the committed default and leaves its own address alone.
    assert created[0].is_default is False
    assert Address.objects.filter(user_id=user_pk).count() == 2
    assert Address.objects.filter(user_id=user_pk, is_default=True).count() == 1
