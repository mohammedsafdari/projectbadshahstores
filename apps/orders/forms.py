"""The checkout form (D66, D68).

The payment method is one of the model's payment choices; a missing value means
cash on delivery, so a hand-made POST still works.
"""

from __future__ import annotations

from django import forms

from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_INVALID,
    ADDRESS_REQUIRED,
    CANCEL_REASON_TOO_LONG,
    NOTE_TOO_LONG,
    PAYMENT_METHOD_INVALID,
    SUBSTITUTION_INVALID,
)


class CheckoutForm(forms.Form):
    """The address, payment method, substitution choice and note of one checkout."""

    address = forms.IntegerField(
        min_value=1,
        error_messages={
            "required": ADDRESS_REQUIRED,
            "invalid": ADDRESS_INVALID,
            "min_value": ADDRESS_INVALID,
        },
    )
    payment_method = forms.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
        initial=Order.PaymentMethod.COD,
        error_messages={"invalid_choice": PAYMENT_METHOD_INVALID},
    )
    # required=False lets a hand-made POST omit the radio; the clean method
    # then falls back to CALL_ME, the model default.
    substitution_preference = forms.ChoiceField(
        choices=Order.SubstitutionPreference.choices,
        required=False,
        initial=Order.SubstitutionPreference.CALL_ME,
        error_messages={"invalid_choice": SUBSTITUTION_INVALID},
    )
    customer_note = forms.CharField(
        max_length=300,
        required=False,
        strip=True,
        error_messages={"max_length": NOTE_TOO_LONG},
    )

    def clean_payment_method(self) -> str:
        # A missing choice means cash on delivery, the model default (D68).
        choice = self.cleaned_data["payment_method"]
        return choice or Order.PaymentMethod.COD

    def clean_substitution_preference(self) -> str:
        # A missing choice is the same as the default, rather than an error.
        choice = self.cleaned_data["substitution_preference"]
        return choice or Order.SubstitutionPreference.CALL_ME


class CancelOrderForm(forms.Form):
    """The optional reason on the customer cancel page (D70)."""

    reason = forms.CharField(
        max_length=200,
        required=False,
        strip=True,
        error_messages={"max_length": CANCEL_REASON_TOO_LONG},
    )
