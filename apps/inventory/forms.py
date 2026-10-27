"""Forms for the stock action pages in the admin (ADR D45, point 5).

The forms only collect and shape the input. The rules (sign, whole numbers
for packets, archived suppliers, lengths) live in ``services.py``, which is
the only code that writes stock movements.
"""

from decimal import Decimal

from django import forms

from .models import Supplier
from .services import BATCH_MAX_LENGTH, REASON_MAX_LENGTH


class ReceiveStockForm(forms.Form):
    quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        max_digits=12,
        decimal_places=3,
        help_text="In the variant's own unit. Packets must be whole numbers.",
    )
    # Archived suppliers are left out of the list on purpose.
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.filter(is_active=True),
        required=False,
    )
    batch_number = forms.CharField(max_length=BATCH_MAX_LENGTH, required=False)
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    reason = forms.CharField(max_length=REASON_MAX_LENGTH, required=False, label="Note")
