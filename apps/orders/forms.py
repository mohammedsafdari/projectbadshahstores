"""The checkout form (D66).

The payment method is deliberately not a field: this slice only offers cash on
delivery, so the view passes that constant to the order service.
"""

from __future__ import annotations

from django import forms

from apps.orders.models import Order
from apps.orders.presentation import (
    ADDRESS_INVALID,
    ADDRESS_REQUIRED,
    NOTE_TOO_LONG,
    SUBSTITUTION_INVALID,
)


class CheckoutForm(forms.Form):
    """The address, substitution choice and note of one checkout."""

    address = forms.IntegerField(
        min_value=1,
        error_messages={
            "required": ADDRESS_REQUIRED,
            "invalid": ADDRESS_INVALID,
            "min_value": ADDRESS_INVALID,
        },
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

    def clean_substitution_preference(self) -> str:
        # A missing choice is the same as the default, rather than an error.
        choice = self.cleaned_data["substitution_preference"]
        return choice or Order.SubstitutionPreference.CALL_ME
