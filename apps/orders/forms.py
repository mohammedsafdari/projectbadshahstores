"""The checkout form (D66, D74).

The customer ticks one checkbox to put as much of the order as possible on the
khata; the view turns that into the order's payment method. A missing value
means cash on delivery, so a hand-made POST still works.
"""

from __future__ import annotations

from django import forms

from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_INVALID,
    ADDRESS_REQUIRED,
    CANCEL_REASON_TOO_LONG,
    NOTE_TOO_LONG,
    SUBSTITUTION_INVALID,
)


class CheckoutForm(forms.Form):
    """The address, khata choice, substitution choice and note of one checkout."""

    address = forms.IntegerField(
        min_value=1,
        error_messages={
            "required": ADDRESS_REQUIRED,
            "invalid": ADDRESS_INVALID,
            "min_value": ADDRESS_INVALID,
        },
    )
    # The customer never types an amount: ticking the box puts as much of the
    # order as their available credit covers on the khata (D74).
    use_credit = forms.BooleanField(required=False)
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
