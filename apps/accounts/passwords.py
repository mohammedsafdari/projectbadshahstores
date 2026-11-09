"""Password accounts: sign-up, lookup and the password login throttle (D72).

The rules stay here, without request objects: the views shape the input and the
templates print the words. Only customer accounts are touched; staff accounts
belong to /admin/ and never share a row with a shop account.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import IntegrityError, transaction

from apps.accounts.auth_presentation import SIGNUP_EXISTS_MESSAGE
from apps.accounts.models import User
from apps.core.phone import normalise_phone

# Five wrong tries lock the account for fifteen minutes (D72). The counter lives
# in the cache, not the database, so a cache flush clears the lock.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

REASON_OK = "ok"
REASON_WRONG = "wrong"
REASON_LOCKED = "locked"

# The one sentence a duplicate sign-up shows lives in the wording file, so the
# service and the view can never drift apart (D72).

# The keys below are private to this module, so the cache cannot collide with
# another feature that counts something.
CACHE_KEY_PREFIX = "password-login"


class AccountExistsError(ValueError):
    """A customer account already uses this phone or this email (D72)."""


@dataclass(frozen=True)
class PasswordLoginResult:
    """What happened, so the view can choose the message and the redirect."""

    # frozen=True makes the result read-only, like a small immutable record.
    user: User | None
    reason: str


def _customers():
    """The customer account space: never staff and never a superuser (D72)."""
    return User.objects.filter(is_staff=False, is_superuser=False)


def _raise_if_account_exists(phone: str, email: str) -> None:
    """Refuse a phone that any customer uses, or an email an active one uses.

    A deactivated customer still blocks the phone, so deactivation cannot be
    bypassed by signing up again; a deactivated email is free.
    """
    if _customers().filter(phone=phone).exists():
        raise AccountExistsError(str(SIGNUP_EXISTS_MESSAGE))
    if _customers().filter(email=email, is_active=True).exists():
        raise AccountExistsError(str(SIGNUP_EXISTS_MESSAGE))


# The lone ``*`` makes every argument keyword-only, so a caller cannot pass the
# phone and the email in the wrong order.
def register_customer(*, phone: str, email: str, full_name: str, password: str) -> User:
    """Create a verified-by-nobody customer account and return it (D72).

    The two contacts stay unverified: the pages from Slice 38e verify them and
    the delivery gate (D71) enforces it before the first delivery.
    """
    normalised = normalise_phone(phone)
    cleaned_email = email.strip().lower()
    _raise_if_account_exists(normalised, cleaned_email)
    try:
        # The unique constraints are the final guard. A race between two
        # sign-ups must become the same one message, not an IntegrityError 500.
        # The except sits outside the with block: a caught error inside it
        # leaves the transaction unusable (Slice 20d).
        with transaction.atomic():
            return User.objects.create_user(
                phone=normalised,
                password=password,
                email=cleaned_email,
                full_name=full_name,
            )
    except IntegrityError as exc:
        raise AccountExistsError(str(SIGNUP_EXISTS_MESSAGE)) from exc


def find_customer(identifier: str) -> User | None:
    """Find an active customer by phone or email, or return None (D72).

    An ``@`` in the text means an email address; anything else is read as a
    phone number. Staff accounts and inactive accounts are never returned.
    """
    text = identifier.strip()
    if not text:
        return None
    if "@" in text:
        email = text.lower()
        return _customers().filter(email=email, is_active=True).first()
    try:
        phone = normalise_phone(text)
    except ValueError:
        return None
    return _customers().filter(phone=phone, is_active=True).first()


def _cache_key(identifier: str, user: User | None) -> str:
    """One counter per account, or per hashed identifier text (D72).

    The phone and the email of one account share a counter, so switching the
    identifier cannot reset the five tries. For an unknown identifier the text
    is hashed, so the cache never stores a phone number or an email address and
    the key shape does not reveal who has an account.
    """
    if user is not None:
        return _user_cache_key(user)
    digest = hashlib.sha256(identifier.strip().lower().encode()).hexdigest()
    return f"{CACHE_KEY_PREFIX}:text:{digest}"


def _user_cache_key(user: User) -> str:
    """The one counter key shared by a customer's phone and email (D72)."""
    return f"{CACHE_KEY_PREFIX}:user:{user.pk}"


@functools.cache
def _dummy_password_hash() -> str:
    """A real encoded password, made once, for identifiers that find nobody.

    Checking a password against it costs the same as a real check, so response
    time does not say whether the account exists (D72).
    """
    # functools.cache remembers the first result, so the hash is made on the
    # first unknown login and reused afterwards.
    return make_password("dummy-password-for-timing")


def _record_failure(key: str) -> None:
    """Count one wrong try and start the fifteen-minute window if needed."""
    # add() only sets the key when it is missing, so the lock starts at the
    # first failure and the later failures do not extend it.
    cache.add(key, 0, LOGIN_LOCK_SECONDS)
    cache.incr(key)


def clear_login_failures(user: User) -> None:
    """Forget this customer's wrong-password count (D73).

    A successful reset or password change proves the owner of the account, so
    the old failures must not keep the account locked.
    """
    cache.delete(_user_cache_key(user))


def login_locked(user: User) -> bool:
    """Whether this customer has used up the five wrong tries (D72)."""
    return cache.get(_user_cache_key(user), 0) >= LOGIN_MAX_FAILURES


def record_login_failure(user: User) -> None:
    """Count one wrong current-password try on an account page (D73)."""
    _record_failure(_user_cache_key(user))


def authenticate_customer_password(identifier: str, password: str) -> PasswordLoginResult:
    """Check a customer password, or refuse with one of three reasons (D72).

    A wrong password, an unknown identifier, an account without a usable
    password (an OTP-only customer) and a deactivated account all come back as
    ``REASON_WRONG``, so the page never says which one it was.
    """
    user = find_customer(identifier)
    key = _cache_key(identifier, user)
    if cache.get(key, 0) >= LOGIN_MAX_FAILURES:
        # While locked, even the right password is refused.
        return PasswordLoginResult(user=None, reason=REASON_LOCKED)
    if user is None:
        # Hash anyway, so this answer takes as long as a real wrong password.
        check_password(password, _dummy_password_hash())
        _record_failure(key)
        return PasswordLoginResult(user=None, reason=REASON_WRONG)
    if not user.has_usable_password():
        # An OTP-only account has no hash to compare, so use the dummy one:
        # this answer must cost the same as a wrong password (D72, Slice 38h).
        check_password(password, _dummy_password_hash())
        _record_failure(key)
        return PasswordLoginResult(user=None, reason=REASON_WRONG)
    if not user.check_password(password):
        _record_failure(key)
        return PasswordLoginResult(user=None, reason=REASON_WRONG)
    # A correct password clears the count; nothing else is reset.
    cache.delete(key)
    return PasswordLoginResult(user=user, reason=REASON_OK)
