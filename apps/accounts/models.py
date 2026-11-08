"""
Accounts app: custom user model.

Usage is phone-based. The role field carries no authority by itself: only
is_staff, is_superuser and permissions/groups determine admin access. This is
important for the business model: an OWNER who is not staff still cannot log
into the admin, and any role (including MEMBER) can be a staff user when
is_staff is True.
"""

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.models import ArchivableModel, TimeStampedModel
from apps.core.phone import normalise_phone


class UserManager(BaseUserManager["User"]):
    """Manager for the phone-based User model.

    create_user normalises phone and email; customers log in by OTP, so a
    password is optional and defaults to unusable.
    create_superuser REQUIRES a password and promotes the account to admin
    (is_staff, is_superuser, role OWNER).
    get_by_natural_key is what Django's /admin/ login uses: it normalises the
    input so admin login with "98765 43210" works, and it finds staff accounts
    only (D72). Customers use their own password login at /account/login/.
    """

    def _normalise_email(self, email: str | None) -> str | None:
        if not email:
            return None
        email = email.strip().lower()
        if not email:
            return None
        return email

    def create_user(self, phone: str, password: str | None = None, **extra):
        if not phone:
            raise ValueError("The phone number field must be set.")

        user = self.model(phone=normalise_phone(phone), **extra)
        user.email = self._normalise_email(extra.get("email"))
        if password is None:
            user.set_unusable_password()
        else:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        if not password:
            raise ValueError("Superuser must have a password.")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", User.ROLE_OWNER)
        user = self.create_user(phone, password=password, **extra)
        return user

    def get_by_natural_key(self, phone):
        try:
            normalised = normalise_phone(phone)
        except ValueError:
            # No phone in the message: an exception must not print one.
            raise self.model.DoesNotExist("No active staff user found.") from None
        # Staff only: the admin login must never find a customer account, even
        # when a staff account shares the same phone (D72).
        return self.get(is_active=True, phone=normalised, is_staff=True)


class User(AbstractBaseUser, PermissionsMixin):
    """Phone-based user.

    Phone is the username field (USERNAME_FIELD). The role field is descriptive
    only and grants nothing by itself: admin access is decided by is_staff,
    is_superuser, permissions and groups.
    """

    ROLE_OWNER = "OWNER"
    ROLE_MANAGER = "MANAGER"
    ROLE_PACKER = "PACKER"
    ROLE_DELIVERY = "DELIVERY"
    ROLE_MEMBER = "MEMBER"

    ROLE_CHOICES = [
        (ROLE_OWNER, "Owner"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_PACKER, "Packer"),
        (ROLE_DELIVERY, "Delivery"),
        (ROLE_MEMBER, "Member"),
    ]

    phone = models.CharField(
        "phone",
        max_length=16,
        help_text="India mobile number, E.164, e.g. +919876543210.",
    )
    # null=True is intentional: the unique-when-set constraint needs NULL, not "".
    email = models.EmailField("email address", null=True, blank=True)  # noqa: DJ001
    full_name = models.CharField("full name", max_length=255, blank=True)
    # Set when the customer proves the phone or the email with a code (D71).
    phone_verified_at = models.DateTimeField("phone verified at", null=True, blank=True)
    email_verified_at = models.DateTimeField("email verified at", null=True, blank=True)
    role = models.CharField(
        "role",
        max_length=10,
        choices=ROLE_CHOICES,
        default=ROLE_MEMBER,
    )
    is_active = models.BooleanField("active", default=True)
    is_staff = models.BooleanField("staff status", default=False)
    created_at = models.DateTimeField("created at", default=timezone.now)
    updated_at = models.DateTimeField("updated at", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        # Two account spaces share the tables (D72): a customer account
        # (is_staff=False) and a staff account (is_staff=True) may use the same
        # phone and the same email, but within one space each active value is
        # still unique. Inactive rows never block a new active one.
        constraints = [
            models.UniqueConstraint(
                fields=["phone", "is_staff"],
                condition=models.Q(is_active=True),
                name="accounts_user_phone_staff_active_unique",
            ),
            models.UniqueConstraint(
                fields=["email", "is_staff"],
                condition=models.Q(is_active=True, email__isnull=False),
                name="accounts_user_email_staff_active_unique",
            ),
        ]

    def __str__(self):
        # The admin lists and drop-downs may show both rows for one phone, so a
        # staff account says so (D72).
        suffix = " (staff)" if self.is_staff else ""
        return f"{self.phone}{suffix}"

    def save(self, *args, **kwargs):
        # Normalise on every save path, not only through the manager.
        self.phone = normalise_phone(self.phone)
        if self.email is not None:
            self.email = self.email.strip().lower()
            if not self.email:
                self.email = None
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self.phone:
            raise ValidationError({"phone": "The phone number field must be set."})
        try:
            normalise_phone(self.phone)
        except ValueError as exc:
            raise ValidationError({"phone": str(exc)}) from exc

    @property
    def is_ready_for_delivery(self) -> bool:
        """True once the phone, and the email when there is one, are verified (D71)."""
        if self.phone_verified_at is None:
            return False
        # An OTP-only customer has no email address, so there is nothing else to prove.
        if not self.email:
            return True
        return self.email_verified_at is not None


# A pin code is exactly six digits and cannot start with 0 (D57).
PINCODE_VALIDATOR = RegexValidator(
    regex=r"^[1-9][0-9]{5}$",
    message="Enter a six-digit pin code; the first digit cannot be 0.",
)


class Address(TimeStampedModel, ArchivableModel):
    """One saved delivery address of one customer (D57).

    The customer deletes an address by archiving it; the row is never removed,
    so a placed order can keep pointing at the copy it froze (Slice 35).
    """

    class Label(models.TextChoices):
        HOME = "HOME", "Home"
        WORK = "WORK", "Work"
        OTHER = "OTHER", "Other"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    recipient_name = models.CharField(max_length=200)
    # E.164, written by the service through normalise_phone.
    recipient_phone = models.CharField(max_length=16)
    # House, flat or building.
    line1 = models.CharField(max_length=200)
    # Street or area; optional.
    line2 = models.CharField(max_length=200, blank=True, default="")
    landmark = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=100, default="Indore")
    state = models.CharField(max_length=100, default="Madhya Pradesh")
    pincode = models.CharField(max_length=6, validators=[PINCODE_VALIDATOR])
    label = models.CharField(max_length=10, choices=Label, default=Label.HOME)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # A partial unique constraint: of the rows that match the condition
            # (active and default), each user may have at most one. Archived
            # rows are outside the condition, so they never block a new default.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True, is_active=True),
                name="accounts_address_one_active_default_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_name}: {self.line1}, {self.city} {self.pincode}"


class OtpChallenge(models.Model):
    """One pending one-time code (D59, D71).

    Only an HMAC of the code is stored, never the code itself. ``purpose``
    keeps codes for different flows apart; a code for one purpose can never
    confirm another.
    """

    class Purpose(models.TextChoices):
        LOGIN = "LOGIN", "Login"
        PHONE_VERIFY = "PHONE_VERIFY", "Phone verification"
        EMAIL_VERIFY = "EMAIL_VERIFY", "Email verification"
        # Used by the password reset flow in Slice 38f.
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset"

    phone = models.CharField(max_length=16)
    purpose = models.CharField(max_length=20, choices=Purpose, default=Purpose.LOGIN)
    # Hex SHA-256 HMAC of "phone:purpose:code" keyed with SECRET_KEY.
    code_hmac = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            # Rate limits read the newest rows for one phone, so this one
            # index serves both the "too soon" and "too many per hour" checks.
            models.Index(
                fields=["phone", "created_at"],
                name="accounts_otp_phone_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.phone} {self.purpose} {self.created_at}"
