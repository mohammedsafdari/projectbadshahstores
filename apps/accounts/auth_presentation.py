"""Login and sign-up wording: one place for every sentence a customer reads (D72).

Views and templates never spell these words out themselves. `auth_text()` hands
the fixed sentences to the login and sign-up templates as one dictionary.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

LOGIN_TITLE = _("Log in")
LOGIN_HEADING = _("Log in")
PASSWORD_LOGIN_HEADING = _("Log in with your password")
OTP_LOGIN_HEADING = _("Or log in with a code")
OTP_HINT = _("We will send a 6-digit code to your mobile number.")
SEND_CODE_BUTTON = _("Send code")

IDENTIFIER_LABEL = _("Mobile number or email")
PASSWORD_LABEL = _("Password")
PASSWORD_LOGIN_BUTTON = _("Log in")
SIGNUP_LINK = _("New here? Create an account")

LOGIN_ERROR_MESSAGE = _("The phone number, email or password is not right.")
LOGIN_LOCKED_MESSAGE = _("Too many tries. Please wait 15 minutes, or log in with a code instead.")

SIGNUP_TITLE = _("Create an account")
SIGNUP_HEADING = _("Create an account")
FULL_NAME_LABEL = _("Full name")
EMAIL_LABEL = _("Email address")
PASSWORD1_LABEL = _("Password")
PASSWORD2_LABEL = _("Repeat the password")
SIGNUP_BUTTON = _("Create account")
LOGIN_LINK = _("Already have an account? Log in")

SIGNUP_EXISTS_MESSAGE = _("An account with these details already exists. Please log in.")
SIGNUP_DONE_MESSAGE = _(
    "Your account is ready. Please verify your mobile number and email before your first delivery."
)
PASSWORDS_DIFFER_MESSAGE = _("The two passwords are not the same.")
EMAIL_REQUIRED_MESSAGE = _("Enter your email address.")
NAME_REQUIRED_MESSAGE = _("Enter your full name.")
EMAIL_INVALID_MESSAGE = _("Enter a valid email address.")


def auth_text() -> dict[str, str]:
    """Every fixed sentence the login and sign-up pages print."""
    # ``str()`` forces the lazy translation now, so the template only prints text.
    return {
        "login_title": str(LOGIN_TITLE),
        "login_heading": str(LOGIN_HEADING),
        "password_login_heading": str(PASSWORD_LOGIN_HEADING),
        "otp_login_heading": str(OTP_LOGIN_HEADING),
        "otp_hint": str(OTP_HINT),
        "send_code_button": str(SEND_CODE_BUTTON),
        "identifier_label": str(IDENTIFIER_LABEL),
        "password_label": str(PASSWORD_LABEL),
        "password_login_button": str(PASSWORD_LOGIN_BUTTON),
        "signup_link": str(SIGNUP_LINK),
        "signup_title": str(SIGNUP_TITLE),
        "signup_heading": str(SIGNUP_HEADING),
        "full_name_label": str(FULL_NAME_LABEL),
        "email_label": str(EMAIL_LABEL),
        "password1_label": str(PASSWORD1_LABEL),
        "password2_label": str(PASSWORD2_LABEL),
        "signup_button": str(SIGNUP_BUTTON),
        "login_link": str(LOGIN_LINK),
        "login_error_message": str(LOGIN_ERROR_MESSAGE),
        "login_locked_message": str(LOGIN_LOCKED_MESSAGE),
        "signup_exists_message": str(SIGNUP_EXISTS_MESSAGE),
        "signup_done_message": str(SIGNUP_DONE_MESSAGE),
        "passwords_differ_message": str(PASSWORDS_DIFFER_MESSAGE),
        "email_required_message": str(EMAIL_REQUIRED_MESSAGE),
        "name_required_message": str(NAME_REQUIRED_MESSAGE),
        "email_invalid_message": str(EMAIL_INVALID_MESSAGE),
    }
