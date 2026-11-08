"""Admin registration for the custom User model."""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm as AuthUserChangeForm
from django.contrib.auth.forms import UserCreationForm as AuthUserCreationForm

from apps.accounts.models import User
from apps.core.phone import normalise_phone


class UserChangeForm(AuthUserChangeForm):
    """Change form that shows the password read-only with a change-password link."""

    class Meta:
        model = User
        fields = "__all__"

    def clean_phone(self):
        return self._clean_phone(self.cleaned_data.get("phone"))

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return email
        if not email.strip():
            return None
        return email.strip().lower()

    def clean(self):
        # The uniqueness check runs here, not inside clean_phone, because the
        # account space (is_staff) is only reliable once every field is clean.
        # Two accounts with one phone may exist when one is staff and one is a
        # customer (D72).
        cleaned = super().clean()
        self._check_duplicates(cleaned)
        return cleaned

    def _clean_phone(self, raw_phone):
        if raw_phone is None:
            return raw_phone
        try:
            normalised = normalise_phone(raw_phone)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return normalised

    def _check_duplicates(self, cleaned):
        # Only the same account space is compared: a staff account and a
        # customer account may share a phone and an email (D72).
        is_staff = bool(cleaned.get("is_staff", self.instance.is_staff))
        phone = cleaned.get("phone")
        email = cleaned.get("email")
        if phone:
            query = User.objects.filter(phone=phone, is_active=True, is_staff=is_staff)
            if self.instance and self.instance.pk:
                query = query.exclude(pk=self.instance.pk)
            if query.exists():
                self.add_error("phone", "A user with this phone number already exists.")
        if email:
            query = User.objects.filter(email=email, is_active=True, is_staff=is_staff)
            if self.instance and self.instance.pk:
                query = query.exclude(pk=self.instance.pk)
            if query.exists():
                self.add_error("email", "A user with this email address already exists.")


class UserAddForm(AuthUserCreationForm):
    """Add form that provides password1/password2 and all add_fieldsets fields."""

    class Meta:
        model = User
        fields = (
            "phone",
            "full_name",
            "email",
            "role",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
        )

    def clean_phone(self):
        return self._clean_phone(self.cleaned_data.get("phone"))

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return email
        email = email.strip().lower()
        if not email:
            return None
        return email

    def clean(self):
        # Same reason as UserChangeForm: a staff account may share a phone or
        # an email with a customer account, so the duplicate check must know
        # the account space (D72).
        # clean() can be None in django-stubs, so fall back to an empty dict.
        cleaned = super().clean() or {}
        is_staff = bool(cleaned.get("is_staff"))
        phone = cleaned.get("phone")
        email = cleaned.get("email")
        if phone and User.objects.filter(phone=phone, is_active=True, is_staff=is_staff).exists():
            self.add_error("phone", "A user with this phone number already exists.")
        if email and User.objects.filter(email=email, is_active=True, is_staff=is_staff).exists():
            self.add_error("email", "A user with this email address already exists.")
        return cleaned

    def _clean_phone(self, raw_phone):
        try:
            normalised = normalise_phone(raw_phone)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return normalised


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserAddForm

    list_display = ("phone", "full_name", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
    search_fields = ("phone", "email", "full_name")
    ordering = ("phone",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Personal info", {"fields": ("full_name", "email", "role")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {"fields": ("created_at", "updated_at", "phone_verified_at", "email_verified_at")},
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "password1",
                    "password2",
                    "full_name",
                    "email",
                    "role",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                ),
            },
        ),
    )

    readonly_fields = ("created_at", "updated_at", "phone_verified_at", "email_verified_at")

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return self.readonly_fields
        return (*self.readonly_fields, "is_superuser", "groups", "user_permissions")

    def get_fieldsets(self, request, obj=None):
        # On the add page, non-superusers must not be able to create a
        # superuser, so remove is_superuser and user_permissions from the add
        # form for them. Django admin renders add_fieldsets for obj=None.
        if not request.user.is_superuser and obj is None:
            add_fieldsets = []
            for label, options in self.add_fieldsets:
                fields = tuple(
                    f for f in options["fields"] if f not in ("is_superuser", "user_permissions")
                )
                add_fieldsets.append((label, {**options, "fields": fields}))
            return add_fieldsets
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        # A changed contact detail must be verified again (D71). The verified
        # times are read-only, so the form can never set them directly.
        if "phone" in form.changed_data:
            obj.phone_verified_at = None
        if "email" in form.changed_data:
            obj.email_verified_at = None
        super().save_model(request, obj, form, change)
