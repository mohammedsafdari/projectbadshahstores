"""Forms for phone login, code verification, passwords and the address book.

Code login, password login and sign-up keep their text checks here (D59, D65,
D72); the rules that need the database live in services.
"""

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.address_presentation import (
    CITY_LABEL,
    CITY_REQUIRED,
    LABEL_LABEL,
    LANDMARK_LABEL,
    LINE1_LABEL,
    LINE1_REQUIRED,
    LINE2_LABEL,
    MAKE_DEFAULT_LABEL,
    NAME_LABEL,
    NAME_REQUIRED,
    PHONE_ERROR,
    PHONE_LABEL,
    PHONE_REQUIRED,
    PINCODE_ERROR,
    PINCODE_LABEL,
    PINCODE_REQUIRED,
    STATE_LABEL,
    STATE_REQUIRED,
)
from apps.accounts.auth_presentation import (
    CURRENT_PASSWORD_LABEL,
    EMAIL_INVALID_MESSAGE,
    EMAIL_LABEL,
    EMAIL_REQUIRED_MESSAGE,
    FULL_NAME_LABEL,
    IDENTIFIER_LABEL,
    NAME_REQUIRED_MESSAGE,
    NEW_PASSWORD1_LABEL,
    NEW_PASSWORD2_LABEL,
    PASSWORD1_LABEL,
    PASSWORD2_LABEL,
    PASSWORD_LABEL,
    PASSWORD_TOO_LONG_MESSAGE,
    PASSWORDS_DIFFER_MESSAGE,
)
from apps.accounts.models import PINCODE_VALIDATOR, Address, User
from apps.accounts.verification_presentation import CODE_LABEL
from apps.core.phone import normalise_phone

# One style for every text input, so the pages look the same.
INPUT_CLASSES = (
    "mt-1 block w-full rounded border border-gray-300 px-3 py-2 "
    "text-base shadow-sm focus:border-brand-500 focus:outline-none "
    "focus:ring-2 focus:ring-brand-500"
)

# No real password is this long; the cap only stops a huge POST from making the
# hasher work forever (D72, Slice 38h).
PASSWORD_MAX_LENGTH = 1000


class PhoneLoginForm(forms.Form):
    """Collect a mobile number and hand the normalised value to the view."""

    phone = forms.CharField(
        label=PHONE_LABEL,
        max_length=16,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "98765 43210",
                "autofocus": True,
                "class": INPUT_CLASSES,
            }
        ),
    )

    def clean_phone(self) -> str:
        # The form is the only place that sees raw text from a customer, so it
        # normalises once and every later step uses "+91XXXXXXXXXX".
        try:
            return normalise_phone(self.cleaned_data["phone"])
        except ValueError as exc:
            raise forms.ValidationError(PHONE_ERROR) from exc


class PasswordLoginForm(forms.Form):
    """Collect an identifier and a password for the customer password login (D72).

    The identifier is a phone number or an email address. The form does not
    decide which: `apps.accounts.passwords` reads the ``@`` and normalises.
    """

    identifier = forms.CharField(
        label=IDENTIFIER_LABEL,
        max_length=254,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "class": INPUT_CLASSES,
            }
        ),
    )
    password = forms.CharField(
        label=PASSWORD_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": INPUT_CLASSES,
            }
        ),
    )


class SignupForm(forms.Form):
    """Collect the five values a new customer account needs (D72).

    It is a plain Form: the service creates the account and a duplicate is
    reported with one generic sentence, so the form never runs a database
    uniqueness check of its own.
    """

    phone = forms.CharField(
        label=PHONE_LABEL,
        max_length=16,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "98765 43210",
                "class": INPUT_CLASSES,
            }
        ),
    )
    full_name = forms.CharField(
        label=FULL_NAME_LABEL,
        max_length=255,
        strip=True,
        error_messages={"required": NAME_REQUIRED_MESSAGE},
        widget=forms.TextInput(attrs={"autocomplete": "name", "class": INPUT_CLASSES}),
    )
    email = forms.EmailField(
        label=EMAIL_LABEL,
        max_length=254,
        error_messages={
            "required": EMAIL_REQUIRED_MESSAGE,
            "invalid": EMAIL_INVALID_MESSAGE,
        },
        widget=forms.EmailInput(attrs={"autocomplete": "email", "class": INPUT_CLASSES}),
    )
    password1 = forms.CharField(
        label=PASSWORD1_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )
    password2 = forms.CharField(
        label=PASSWORD2_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )

    def clean_phone(self) -> str:
        # Same normalisation as the phone login: one shape for every later step.
        try:
            return normalise_phone(self.cleaned_data["phone"])
        except ValueError as exc:
            raise forms.ValidationError(PHONE_ERROR) from exc

    def clean_email(self) -> str:
        # Email addresses are compared lowercased and without spaces; the model
        # save does the same, but the form checks the cleaned value first.
        return self.cleaned_data["email"].strip().lower()

    def clean(self) -> dict:
        # clean() can be None in django-stubs, so fall back to an empty dict.
        cleaned = super().clean() or {}
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if not password1 or not password2:
            return cleaned
        if password1 != password2:
            self.add_error("password2", PASSWORDS_DIFFER_MESSAGE)
            return cleaned
        # Django's own validators (D72): the unsaved user gives the similarity
        # check its phone, email and name. The messages are shown as they are.
        candidate = User(
            phone=cleaned.get("phone", ""),
            email=cleaned.get("email") or None,
            full_name=cleaned.get("full_name", ""),
        )
        try:
            validate_password(password1, user=candidate)
        except DjangoValidationError as exc:
            self.add_error("password1", exc)
        return cleaned


class ForgotPasswordForm(forms.Form):
    """Collect a phone number or an email address for a reset code (D73)."""

    identifier = forms.CharField(
        label=IDENTIFIER_LABEL,
        max_length=254,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autofocus": True,
                "class": INPUT_CLASSES,
            }
        ),
    )


class ResetPasswordForm(forms.Form):
    """The code and the two new passwords of the reset page (D73).

    ``user`` is optional and only feeds Django's similarity validator; the
    service is what actually changes the password.
    """

    code = forms.CharField(
        label=CODE_LABEL,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "123456",
                "autofocus": True,
                "class": INPUT_CLASSES,
            }
        ),
    )
    new_password1 = forms.CharField(
        label=NEW_PASSWORD1_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )
    new_password2 = forms.CharField(
        label=NEW_PASSWORD2_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )

    def __init__(self, *args, user: User | None = None, **kwargs):
        # The calling view passes the found customer so the similarity check
        # can compare the new password with the phone, email and name.
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].strip()
        if len(code) != 6 or not code.isdigit():
            raise forms.ValidationError(_("Enter the 6-digit code."))
        return code

    def clean(self) -> dict:
        cleaned = super().clean() or {}
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if not password1 or not password2:
            return cleaned
        if password1 != password2:
            self.add_error("new_password2", PASSWORDS_DIFFER_MESSAGE)
            return cleaned
        try:
            validate_password(password1, user=self.user)
        except DjangoValidationError as exc:
            self.add_error("new_password1", exc)
        return cleaned


class ChangePasswordForm(forms.Form):
    """Set or change the password of the logged-in customer (D73).

    The current password is only asked for when the account already has a
    usable one, so an OTP-only customer sets a first password without it.
    """

    current_password = forms.CharField(
        label=CURRENT_PASSWORD_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "class": INPUT_CLASSES}
        ),
    )
    new_password1 = forms.CharField(
        label=NEW_PASSWORD1_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )
    new_password2 = forms.CharField(
        label=NEW_PASSWORD2_LABEL,
        max_length=PASSWORD_MAX_LENGTH,
        error_messages={"max_length": PASSWORD_TOO_LONG_MESSAGE},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "class": INPUT_CLASSES}),
    )

    def __init__(
        self,
        *args,
        user: User | None = None,
        require_current: bool = False,
        **kwargs,
    ):
        self.user = user
        super().__init__(*args, **kwargs)
        # Removing the field, not merely hiding it, keeps the input off the
        # page and out of the POST for an OTP-only customer.
        if not require_current:
            self.fields.pop("current_password", None)

    def clean(self) -> dict:
        cleaned = super().clean() or {}
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if not password1 or not password2:
            return cleaned
        if password1 != password2:
            self.add_error("new_password2", PASSWORDS_DIFFER_MESSAGE)
            return cleaned
        try:
            validate_password(password1, user=self.user)
        except DjangoValidationError as exc:
            self.add_error("new_password1", exc)
        return cleaned


class ChangeEmailForm(forms.Form):
    """Add or change the email address of the logged-in customer (D73).

    The current password is asked for only when the account has a usable one.
    """

    email = forms.EmailField(
        label=EMAIL_LABEL,
        max_length=254,
        error_messages={
            "required": EMAIL_REQUIRED_MESSAGE,
            "invalid": EMAIL_INVALID_MESSAGE,
        },
        widget=forms.EmailInput(attrs={"autocomplete": "email", "class": INPUT_CLASSES}),
    )
    current_password = forms.CharField(
        label=CURRENT_PASSWORD_LABEL,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "class": INPUT_CLASSES}
        ),
    )

    def __init__(self, *args, require_current: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not require_current:
            self.fields.pop("current_password", None)

    def clean_email(self) -> str:
        # Stored lowercased and without spaces, like every other email here.
        return self.cleaned_data["email"].strip().lower()


class OtpVerifyForm(forms.Form):
    """Collect the six digits the customer received by SMS."""

    code = forms.CharField(
        label=_("6-digit code"),
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "123456",
                "autofocus": True,
                "class": INPUT_CLASSES,
            }
        ),
    )

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].strip()
        if len(code) != 6 or not code.isdigit():
            raise forms.ValidationError(_("Enter the 6-digit code from the SMS."))
        return code


class VerificationCodeForm(forms.Form):
    """Collect the six digits of a phone or email verification code (D71).

    A blank value and letters are refused by the same rule the login form uses,
    because a verification code has the same shape.
    """

    code = forms.CharField(
        label=CODE_LABEL,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "123456",
                "autofocus": True,
                "class": INPUT_CLASSES,
            }
        ),
    )

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].strip()
        if len(code) != 6 or not code.isdigit():
            raise forms.ValidationError(_("Enter the 6-digit code."))
        return code


class AddressForm(forms.Form):
    """The text of one saved address (D65).

    It is a plain Form, not a ModelForm: the service, not the form, decides the
    default address and the limit, so the form only strips and checks the text.
    """

    recipient_name = forms.CharField(
        label=NAME_LABEL,
        max_length=200,
        strip=True,
        error_messages={"required": NAME_REQUIRED},
        widget=forms.TextInput(attrs={"autocomplete": "name", "class": INPUT_CLASSES}),
    )
    recipient_phone = forms.CharField(
        label=PHONE_LABEL,
        max_length=16,
        strip=True,
        error_messages={"required": PHONE_REQUIRED},
        widget=forms.TextInput(
            attrs={"autocomplete": "tel", "inputmode": "tel", "class": INPUT_CLASSES}
        ),
    )
    line1 = forms.CharField(
        label=LINE1_LABEL,
        max_length=200,
        strip=True,
        error_messages={"required": LINE1_REQUIRED},
        widget=forms.TextInput(attrs={"autocomplete": "address-line1", "class": INPUT_CLASSES}),
    )
    line2 = forms.CharField(
        label=LINE2_LABEL,
        max_length=200,
        required=False,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "address-line2", "class": INPUT_CLASSES}),
    )
    landmark = forms.CharField(
        label=LANDMARK_LABEL,
        max_length=200,
        required=False,
        strip=True,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    city = forms.CharField(
        label=CITY_LABEL,
        max_length=100,
        initial="Indore",
        strip=True,
        error_messages={"required": CITY_REQUIRED},
        widget=forms.TextInput(attrs={"autocomplete": "address-level2", "class": INPUT_CLASSES}),
    )
    state = forms.CharField(
        label=STATE_LABEL,
        max_length=100,
        initial="Madhya Pradesh",
        strip=True,
        error_messages={"required": STATE_REQUIRED},
        widget=forms.TextInput(attrs={"autocomplete": "address-level1", "class": INPUT_CLASSES}),
    )
    pincode = forms.CharField(
        label=PINCODE_LABEL,
        max_length=6,
        strip=True,
        error_messages={"required": PINCODE_REQUIRED},
        widget=forms.TextInput(
            attrs={"autocomplete": "postal-code", "inputmode": "numeric", "class": INPUT_CLASSES}
        ),
    )
    label = forms.ChoiceField(
        label=LABEL_LABEL,
        choices=Address.Label.choices,
        initial=Address.Label.HOME,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    make_default = forms.BooleanField(
        label=MAKE_DEFAULT_LABEL,
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "mt-1 h-4 w-4 rounded border-gray-300 text-brand-500"}
        ),
    )

    def clean_recipient_phone(self) -> str:
        # The customer types raw text; the service expects the same +91 form
        # the login pages use (D57).
        try:
            return normalise_phone(self.cleaned_data["recipient_phone"])
        except ValueError as exc:
            raise forms.ValidationError(PHONE_ERROR) from exc

    def clean_pincode(self) -> str:
        # The model validator is the one rule for a pin code (D57, D65).
        value = self.cleaned_data["pincode"]
        try:
            PINCODE_VALIDATOR(value)
        except DjangoValidationError as exc:
            raise forms.ValidationError(PINCODE_ERROR) from exc
        return value
