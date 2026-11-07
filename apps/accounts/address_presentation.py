"""Address book wording: one place for every customer-facing sentence (D65).

Views and templates never spell words out themselves. A plain sentence that
needs a value (the limit, a pin code) is built by a small function.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.accounts.services import MAX_ACTIVE_ADDRESSES

LIST_TITLE = _("Your addresses")
LIST_HEADING = _("Your addresses")
ADD_TITLE = _("Add an address")
ADD_HEADING = _("Add an address")
EDIT_TITLE = _("Edit address")
EDIT_HEADING = _("Edit address")

EMPTY_MESSAGE = _("You have not saved any addresses yet.")
ADD_LINK = _("Add an address")
EDIT_LINK = _("Edit")
ARCHIVE_BUTTON = _("Archive")
SET_DEFAULT_BUTTON = _("Make default")
DEFAULT_BADGE = _("Default")
UNSERVED_BADGE = _("Not served")
SAVE_BUTTON = _("Save address")
CANCEL_LINK = _("Cancel")
BACK_TO_LIST = _("Back to your addresses")

SAVED_MESSAGE = _("Address saved.")
UPDATED_MESSAGE = _("Address updated.")
ARCHIVED_MESSAGE = _("Address archived.")
DEFAULT_MESSAGE = _("Default address changed.")

NAME_LABEL = _("Recipient name")
PHONE_LABEL = _("Mobile number")
LINE1_LABEL = _("House, flat or building")
LINE2_LABEL = _("Street or area")
LANDMARK_LABEL = _("Landmark")
CITY_LABEL = _("City")
STATE_LABEL = _("State")
PINCODE_LABEL = _("Pin code")
LABEL_LABEL = _("Label")
MAKE_DEFAULT_LABEL = _("Make this my default address")

PHONE_ERROR = _("Enter a valid Indian mobile number.")
PINCODE_ERROR = _("Enter a six-digit pin code; the first digit cannot be 0.")
NAME_REQUIRED = _("Enter the recipient's name.")
PHONE_REQUIRED = _("Enter the recipient's mobile number.")
LINE1_REQUIRED = _("Enter the house, flat or building.")
CITY_REQUIRED = _("Enter the city.")
STATE_REQUIRED = _("Enter the state.")
PINCODE_REQUIRED = _("Enter the pin code.")


def limit_message() -> str:
    """The sentence shown when the customer already has the maximum addresses."""
    return str(
        _("You can keep at most %(limit)s addresses. Archive one before adding another.")
        % {"limit": MAX_ACTIVE_ADDRESSES}
    )


def unserved_notice(pincode: str) -> str:
    """The notice after saving an address in a pin code the shop does not serve."""
    return str(
        _(
            "We do not deliver to pin code %(pincode)s yet. "
            "You can keep this address, but you cannot order to it."
        )
        % {"pincode": pincode}
    )


def page_text() -> dict[str, str]:
    """Every fixed sentence the address pages print, ready for a template."""
    return {
        "list_title": str(LIST_TITLE),
        "list_heading": str(LIST_HEADING),
        "add_title": str(ADD_TITLE),
        "add_heading": str(ADD_HEADING),
        "edit_title": str(EDIT_TITLE),
        "edit_heading": str(EDIT_HEADING),
        "empty_message": str(EMPTY_MESSAGE),
        "add_link": str(ADD_LINK),
        "edit_link": str(EDIT_LINK),
        "archive_button": str(ARCHIVE_BUTTON),
        "set_default_button": str(SET_DEFAULT_BUTTON),
        "default_badge": str(DEFAULT_BADGE),
        "unserved_badge": str(UNSERVED_BADGE),
        "save_button": str(SAVE_BUTTON),
        "cancel_link": str(CANCEL_LINK),
        "back_link": str(BACK_TO_LIST),
        "limit_message": limit_message(),
    }
