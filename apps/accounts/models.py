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
from django.db import models
from django.utils import timezone

from apps.core.phone import normalise_phone


class UserManager(BaseUserManager):
    """Manager for the phone-based User model.

    create_user normalises phone and email; customers log in by OTP, so a
    password is optional and defaults to unusable.
    create_superuser REQUIRES a password and promotes the account to admin
    (is_staff, is_superuser, role OWNER).
    get_by_natural_key normalises the input so admin login with "98765 43210"
    works.
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
            raise self.model.DoesNotExist(
                f"No active user found for phone {phone!r}"
            )
        return self.get(is_active=True, phone=normalised)


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
    email = models.EmailField("email address", null=True, blank=True)
    full_name = models.CharField("full name", max_length=255, blank=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(is_active=True),
                name="accounts_user_phone_active_unique",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(is_active=True, email__isnull=False),
                name="accounts_user_email_active_unique",
            ),
        ]

    def __str__(self):
        return self.phone

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
