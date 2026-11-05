"""Forms for phone login and code verification (D59)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.phone import normalise_phone

# One style for both text inputs, so the two pages look the same.
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
