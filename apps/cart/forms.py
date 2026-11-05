"""Cart forms: strict quantity text for add and update (D61).

The request is untrusted text, so the form checks the whole string before it
becomes a ``Decimal``. The cart rules then check the number itself (step, cap
and availability); the two checks are deliberately separate.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django import forms

from apps.cart.presentation import INVALID_QUANTITY_INPUT

# Optional spaces are stripped first, then only digits and up to three
# decimals are accepted: no sign, comma, exponent or letters (D61).
QUANTITY_PATTERN = re.compile(r"^\d{1,6}(\.\d{1,3})?$")


class CartQuantityForm(forms.Form):
    """A variant id plus the quantity text from a cart form."""

    variant_id = forms.IntegerField(min_value=1)
    quantity = forms.CharField()

    def clean_quantity(self) -> Decimal:
        # form fields strip surrounding spaces by default; strip again so the
        # rule does not depend on that default.
        text = self.cleaned_data["quantity"].strip()
        if not QUANTITY_PATTERN.fullmatch(text):
            raise forms.ValidationError(INVALID_QUANTITY_INPUT)
        return Decimal(text)


class CartRemoveForm(forms.Form):
    """The only field a remove form needs."""

    variant_id = forms.IntegerField(min_value=1)
