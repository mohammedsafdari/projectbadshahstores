# SMS DLT templates

Every SMS this shop sends is one registered DLT template on MSG91 (D25, D88). The
variable list for each event comes from `SMS_VARIABLE_ORDER` in
`apps/notifications/messages.py`, so this file and the code cannot disagree: if a
variable is added there, it is added here too.

How to read a row:

- **Event** is the value the code passes around (`EVENT_PLACED` and so on). The
  manual credit reminder is `CREDIT_PAYMENT_REMINDER`; a login code is `OTP`.
- **Template** is the text to register, with `{#var1#}`, `{#var2#}` and so on where a
  variable goes. The wording is built from the current `sms_text` strings.
- **Variables** lists VAR1, VAR2, ... in the order they appear.
- **Template id** is the key to set in `MSG91_TEMPLATE_IDS`, for example
  `MSG91_TEMPLATE_IDS=OTP=abc123,ORDER_PLACED=def456`.
- **Length**: each variable value is cut to 30 characters, the usual portal limit.
  Confirm the real limit with MSG91 before the first send.

The brand name `Badshah Stores` is a constant in the code, not a variable, because the
registered template carries the exact wording.

## ORDER_PLACED — order placed

Template: `Badshah Stores: Order {#var1#} is placed. Total Rs {#var2#}. Delivery: {#var3#}.`

Variables: `order_number`, `grand_total`, `promised_delivery_date`

Template id key: `ORDER_PLACED`

The current free text has three wordings, because a cash order names the amount due at
the door, a khata order names the total and a split order names both parts. One
registered template cannot carry three sentences; this template names the order total,
which every customer can check. **Confirm with MSG91 whether the split order needs its
own template** (that would need a second env key).

## ORDER_CANCELLED — order cancelled

Template: `Badshah Stores: Order {#var1#} is cancelled. Call us if you have questions.`

Variables: `order_number`

Template id key: `ORDER_CANCELLED`

## ORDER_OUT_FOR_DELIVERY — out for delivery

Template: `Badshah Stores: Order {#var1#} is out for delivery. Please keep Rs {#var2#} ready.`

Variables: `order_number`, `amount_due_on_delivery`

Template id key: `ORDER_OUT_FOR_DELIVERY`

A khata order's current text names no amount, because the khata is already paid. One
registered template cannot carry both sentences; this one carries the cash wording and a
khata order shows `0.00` in VAR2. **Confirm with MSG91 whether a second template is
needed for a khata order.**

## ORDER_DELIVERED — order delivered

Template: `Badshah Stores: Order {#var1#} is delivered. Thank you for shopping with us.`

Variables: `order_number`

Template id key: `ORDER_DELIVERED`

## ORDER_PAYMENT_FAILED — online payment failed

Template: `Badshah Stores: Order {#var1#} was not placed because the payment did not go through. Your cart is saved.`

Variables: `order_number`

Template id key: `ORDER_PAYMENT_FAILED`

The cart link is not a variable: a URL does not survive a 30-character variable limit,
and the customer can open the cart from the app. The free text still prints the link.

## ORDER_REFUND_PROCESSED — refund sent

Template: `Badshah Stores: Order {#var1#} is cancelled. Rs {#var2#} has been refunded to you.`

Variables: `order_number`, `refund_amount`

Template id key: `ORDER_REFUND_PROCESSED`

When the shop kept a cancellation fee the free text adds a sentence about it. The
registered template names the refund only; **confirm whether the fee sentence needs its
own template.**

## CREDIT_PAYMENT_REMINDER — manual credit reminder

Template: `Badshah Stores: Your credit account has Rs {#var1#} pending. Please pay at the shop.`

Variables: `credit_balance`

Template id key: `CREDIT_PAYMENT_REMINDER`

A staff member presses the button on the dues page; nothing here is automatic, and a
failed reminder is never repeated.

## OTP — login or verification code

Template: `{#var1#} is your Badshah Stores verification code. Do not share it with anyone.`

Variables: `OTP` (the six-digit code)

Template id key: `OTP`

The OTP template uses the variable name `OTP`, not `VAR1`, because that is what the
backend sends (`OTP_VARIABLE_KEY` in `apps/notifications/msg91.py`). Ask MSG91 which
name the registered template expects.

## Before the first real send

1. Register the DLT entity, the header and these templates with MSG91, and put their ids
   in `MSG91_TEMPLATE_IDS` (owner task).
2. Set `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_IDS` and, if needed, `MSG91_TIMEOUT_SECONDS`.
3. Point `SMS_BACKEND` at `apps.notifications.msg91.Msg91SmsBackend` and turn
   `ORDER_SMS_ENABLED` on.
4. Check the URL, the header names and the JSON keys in the constants block at the top of
   `apps/notifications/msg91.py` against MSG91's current Flow API documentation.
