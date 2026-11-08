"""Login and sign-up wording: one place for every sentence a customer reads (D72).

Views and templates never spell these words out themselves. `auth_text()` hands
the fixed sentences to the login and sign-up templates as one dictionary.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.accounts.verification_presentation import CODE_LABEL

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

# Forgot-password and account pages (D73). The exact sentences are fixed so the
# views and templates can never drift apart.
FORGOT_TITLE = _("Forgot your password")
FORGOT_HEADING = _("Forgot your password?")
FORGOT_HELP = _("Enter your mobile number or your email address. We will send you a code.")
FORGOT_BUTTON = _("Send me a code")
FORGOT_LINK = _("Forgot your password?")
FORGOT_SENT_MESSAGE = _(
    "If we have an account for these details, we have sent you a code. It is valid for 5 minutes."
)
RESET_TITLE = _("Set a new password")
RESET_HEADING = _("Set a new password")
RESET_BUTTON = _("Save the new password")
NEW_PASSWORD1_LABEL = _("New password")
NEW_PASSWORD2_LABEL = _("Repeat the new password")
RESET_CODE_ERROR_MESSAGE = _("That code did not work. Ask for a new one.")
RESET_NEED_REQUEST_MESSAGE = _("Please ask for a code first.")
PASSWORD_CHANGED_MESSAGE = _("Your password is saved. Please log in.")
EMAIL_REMOVED_MESSAGE = _(
    "We removed the email address that was on this account, because its mobile number "
    "had not been verified. You can add your email again under My account."
)

ACCOUNT_TITLE = _("My account")
ACCOUNT_HEADING = _("My account")
MY_ACCOUNT_LINK = _("My account")
ACCOUNT_NAME_FALLBACK = _("your account")
PASSWORD_PAGE_HEADING_CHANGE = _("Change your password")
PASSWORD_PAGE_HEADING_SET = _("Set a password")
CURRENT_PASSWORD_LABEL = _("Current password")
PASSWORD_SAVED_MESSAGE = _("Your password is saved.")
CURRENT_PASSWORD_WRONG_MESSAGE = _("The current password is not right.")
EMAIL_PAGE_HEADING_CHANGE = _("Change your email address")
EMAIL_PAGE_HEADING_ADD = _("Add your email address")
EMAIL_SAVED_MESSAGE = _("Your email address is saved. Please verify it.")
EMAIL_BUTTON = _("Save email address")
EMAIL_UNCHANGED_MESSAGE = _("That is already your email address.")
EMAIL_UNUSABLE_MESSAGE = _("This email address cannot be used.")
VERIFIED_MARK = _("Verified")
NOT_VERIFIED_MARK = _("Not verified yet")
SET_PASSWORD_LINK = _("Set a password")
CHANGE_PASSWORD_LINK = _("Change password")
ADD_EMAIL_LINK = _("Add an email address")
CHANGE_EMAIL_LINK = _("Change email address")


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
        "forgot_title": str(FORGOT_TITLE),
        "forgot_heading": str(FORGOT_HEADING),
        "forgot_help": str(FORGOT_HELP),
        "forgot_button": str(FORGOT_BUTTON),
        "forgot_link": str(FORGOT_LINK),
        "reset_title": str(RESET_TITLE),
        "reset_heading": str(RESET_HEADING),
        "reset_button": str(RESET_BUTTON),
        "code_label": str(CODE_LABEL),
        "new_password1_label": str(NEW_PASSWORD1_LABEL),
        "new_password2_label": str(NEW_PASSWORD2_LABEL),
        "account_title": str(ACCOUNT_TITLE),
        "account_heading": str(ACCOUNT_HEADING),
        "my_account_link": str(MY_ACCOUNT_LINK),
        "password_page_heading_change": str(PASSWORD_PAGE_HEADING_CHANGE),
        "password_page_heading_set": str(PASSWORD_PAGE_HEADING_SET),
        "current_password_label": str(CURRENT_PASSWORD_LABEL),
        "email_page_heading_change": str(EMAIL_PAGE_HEADING_CHANGE),
        "email_page_heading_add": str(EMAIL_PAGE_HEADING_ADD),
        "email_button": str(EMAIL_BUTTON),
        "verified_mark": str(VERIFIED_MARK),
        "not_verified_mark": str(NOT_VERIFIED_MARK),
        "set_password_link": str(SET_PASSWORD_LINK),
        "change_password_link": str(CHANGE_PASSWORD_LINK),
        "add_email_link": str(ADD_EMAIL_LINK),
        "change_email_link": str(CHANGE_EMAIL_LINK),
    }
