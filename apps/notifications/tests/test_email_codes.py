"""Tests for the verification emails (D71).

The console/locmem email backend is used by the ``mailoutbox`` fixture, so no
real message leaves the test.
"""

from __future__ import annotations

import logging

from apps.notifications.email_codes import SUBJECT, email_body, send_email_code

EMAIL = "asha@example.com"
CODE = "123456"


def test_email_code_goes_to_the_address_with_the_code_in_the_body(mailoutbox) -> None:
    send_email_code(EMAIL, CODE)

    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [EMAIL]
    assert CODE in message.body


def test_email_code_subject_has_no_code(mailoutbox) -> None:
    send_email_code(EMAIL, CODE)

    message = mailoutbox[0]
    assert message.subject == SUBJECT
    assert CODE not in message.subject


def test_email_code_body_says_the_validity_and_not_to_share_it() -> None:
    body = email_body(CODE)

    assert CODE in body
    assert "5 minutes" in body
    assert "Do not share" in body


def test_email_code_failure_is_swallowed_and_logged_without_code_or_address(
    monkeypatch, caplog
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("smtp is down")

    monkeypatch.setattr("apps.notifications.email_codes.send_mail", _fail)
    caplog.set_level(logging.INFO)

    # The caller must not see the failure: a broken mail server cannot break
    # the verification page.
    send_email_code(EMAIL, CODE)

    assert "Verification email failed" in caplog.text
    assert CODE not in caplog.text
    assert EMAIL not in caplog.text
