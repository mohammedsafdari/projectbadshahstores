"""Forms for phone login, code verification and the address book (D59, D65)."""

from django import forms
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
from apps.accounts.models import PINCODE_VALIDATOR, Address
from apps.accounts.verification_presentation import CODE_LABEL
from apps.core.phone import normalise_phone

# One style for every text input, so the pages look the same.
INPUT_CLASSES = (
    "mt-1 block w-full rounded border border-gray-300 px-3 py-2 "
    "text-base shadow-sm focus:border-brand-500 focus:outline-none "
    "focus:ring-2 focus:ring-brand-500"
)


class PhoneLoginForm(forms.Form):
    """Collect a mobile number and hand the normalised value to the view."""

    phone = forms.CharField(
        label=_("Mobile number"),
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
            raise forms.ValidationError(_("Enter a valid Indian mobile number.")) from exc


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
