"""The header cart count as a lazy context processor (D61)."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest

from apps.cart.models import CartItem
from apps.cart.stores import read_guest_items


def cart_line_count(request: HttpRequest) -> dict[str, Callable[[], int]]:
    """Return a function, not a number.

    Django templates call a callable context value themselves when the
    template prints it. Returning the function means the count query runs only
    on a page that actually shows the number, not on every request that uses
    ``base.html`` for something else.
    """

    def count() -> int:
        if request.user.is_authenticated:
            # One COUNT query on the saved cart. A customer without a cart
            # gets zero without creating one.
            return CartItem.objects.filter(cart__user=request.user).count()
        # The guest cart is already in the session: no database query.
        return len(read_guest_items(request.session))

    return {"cart_line_count": count}
