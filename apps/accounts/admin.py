"""Admin registration for the custom User model."""

from django import forms
from django.contrib import admin
from django.contrib.admin import ModelAdmin
from django.contrib.auth import get_user_model
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

    def _clean_phone(self, raw_phone):
        if raw_phone is None:
            return raw_phone
        try:
            normalised = normalise_phone(raw_phone)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        User = get_user_model()
        query = User.objects.filter(phone=normalised, is_active=True)
        if self.instance and self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError(
                "A user with this phone number already exists."
            )
        return normalised


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
        User = get_user_model()
        if User.objects.filter(email=email, is_active=True).exists():
            raise forms.ValidationError(
                "A user with this email address already exists."
            )
        return email

    def _clean_phone(self, raw_phone):
        try:
            normalised = normalise_phone(raw_phone)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        User = get_user_model()
        if User.objects.filter(phone=normalised, is_active=True).exists():
            raise forms.ValidationError(
                "A user with this phone number already exists."
            )
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
        ("Important dates", {"fields": ("created_at", "updated_at")}),
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

    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return self.readonly_fields
        return self.readonly_fields + (
            "is_superuser",
            "groups",
            "user_permissions",
        )

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
