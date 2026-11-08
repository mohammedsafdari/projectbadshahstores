# Slice 38b: Close the 38a review gaps and hook notices into orders

Slice id: **38b**. Repeat "Slice 38b" at the top of your final message.

## Read first
1. `AGENTS.md`, `docs/AGENT_BRIEF.md`, `docs/STATUS.md`.
2. `docs/decisions.md`, the D64, D68 and D69 entries only.
3. `apps/notifications/order_notices.py`, `apps/notifications/messages.py`, `apps/notifications/tests/test_order_notices.py`, `apps/notifications/tests/recording.py`.
4. `apps/orders/services.py` (read all of it), `apps/orders/state.py`.
5. `apps/orders/tests/checkout_helpers.py` (the helpers `reset_settings`, `clear_settings`, `set_settings`, `serve`, `address`, `variant`, `fill_cart`, `credit_user`, `place_cod`, `place_credit`, `NOW`) and one existing test file such as `apps/orders/tests/test_cancel_credit.py` for style.

## Goal
Part 1: fix the gaps the 38a review found in `notify_order_event` and its tests. Part 2: make orders actually send the notices from Slice 38a: when an order is placed, cancelled, sent out for delivery or delivered, `apps/orders/services.py` registers the notice to be sent **after the database commit**. No model change, no migration, no new setting.

## Fixed decisions (do not change)
- **After commit only.** A hook uses `transaction.on_commit(...)`, so a rolled-back order never sends a message. Each operation registers exactly one commit callback, and that callback runs its events in order.
- Events per operation:
  - `place_order`, only when a new order is created (the replay path `created=False` returns earlier and must send nothing): `EVENT_PLACED` then `EVENT_STAFF_NEW_ORDER`.
  - Any move to `CANCELLED` (staff `transition_order` and `cancel_order_by_customer`): `EVENT_CANCELLED`.
  - Move to `OUT_FOR_DELIVERY`: `EVENT_OUT_FOR_DELIVERY`. Move to `DELIVERED`: `EVENT_DELIVERED`.
  - Every other move (`CONFIRMED`, `PACKING`, and the internal PLACED to CONFIRMED move inside `place_order`) sends nothing.
- The hook goes in `_apply_transition` (so both cancel paths and `transition_order` share it) and at the end of `place_order`, just before the `PlacedOrder` return and after `clear_cart(store)`. A failed or rolled-back call never reaches the hook.
- The hook must not change the lock order, the saves or any returned value. `notify_order_event` is imported into `apps/orders/services.py` by name (`from apps.notifications.order_notices import notify_order_event`), so tests can patch `apps.orders.services.notify_order_event`.
- `notify_order_event` never raises (D69). After Part 1 this includes loading the order and building the notice.
- A staff cancellation also sends the customer's cancel message (default; it goes in `docs/open_questions.md`).
- The customer cancel page, the order list and the WhatsApp button are NOT part of this slice.

## Step 0: PLAN (write it first, in your reply, at most 12 lines)
List what you will change and the helper you will add in `apps/orders/services.py`. Say what you did about the import cycle (`apps/orders/services.py` imports `apps/notifications/order_notices.py`, which imports `apps.orders.models` only; check that no cycle is created). Then continue straight to the work items.

## Work items

1. **Fix `notify_order_event` (`apps/notifications/order_notices.py`).**
   - Move the order lookup and `build_notice(order)` inside a `try/except Exception`, so a database error or a bad field cannot escape. On failure log `Order notice failed: event=%s order_id=%s channel=load` with `logger.exception` and return. Keep the existing warning for a missing order or an unknown event exactly as it is. Update the docstring in one sentence.
   - `badshahstore/settings/base.py`: remove the duplicated comment line above `ORDER_EMAIL_ENABLED` (keep one comment above the pair). No other change in that file.

2. **Tests for Part 1.** Add these 7 tests to `apps/notifications/tests/test_order_notices.py` (same fixtures as the existing tests there; do not change existing tests):
   - `test_staff_new_order_sends_no_email_when_order_email_is_disabled`
   - `test_customer_without_an_email_address_causes_no_error_log` (user with `email=None`, email switch on; assert nothing is sent and `caplog` has no record at ERROR level)
   - `test_unknown_event_sends_nothing_and_logs_a_warning` (both switches on and the recording SMS backend; assert no SMS, no email, and a warning that names the event and the order id)
   - `test_blank_account_name_falls_back_to_the_ship_name` (user with `full_name=""`; the email body starts with `Hello` followed by the order's `ship_name`)
   - `test_staff_email_failure_is_swallowed_and_logged` (patch `apps.notifications.order_notices.send_mail` with `side_effect=RuntimeError`; assert no raise and a log record containing `channel=staff_email`)
   - `test_staff_new_order_does_not_call_send_mail_when_shop_email_is_blank` (patch `send_mail`; assert it was not called)
   - `test_failure_while_loading_the_order_is_swallowed_and_logged` (patch `apps.notifications.order_notices.build_notice` with `side_effect=RuntimeError`; assert no raise and a log record containing `channel=load`)

3. **Hooks in `apps/orders/services.py`.**
   - Add a small private function that takes an order id and a tuple of events and registers `transaction.on_commit` with a callback that calls `notify_order_event` for each event in order. Use `functools.partial` or a nested function rather than a bare `lambda` in a loop, and add a short comment for a Python beginner explaining why (a lambda inside a loop would see only the last event).
   - A small module-level mapping from status to event (`CANCELLED`, `OUT_FOR_DELIVERY`, `DELIVERED` only) next to the imports; import the event constants from `apps.notifications.messages`.
   - Call it at the end of `_apply_transition` (after the cancel side effects) and at the end of `place_order` as described above.
   - Update the module docstring of `apps/orders/services.py` with one sentence: messages are registered with `on_commit` and never block an order (D69).

4. **Tests for Part 2.** New file `apps/orders/tests/test_order_notice_hooks.py`, 12 tests, `pytestmark = pytest.mark.django_db`, the same autouse settings fixture as `test_cancel_credit.py`. Patch `apps.orders.services.notify_order_event` with `unittest.mock.patch` and use the pytest-django fixture `django_capture_on_commit_callbacks` (`with django_capture_on_commit_callbacks(execute=True) as callbacks:`). Build orders with the helpers in `checkout_helpers.py` (real placement through `place_cod` and `place_credit`, `transition_order` for staff moves). Use exactly these names:
   - `test_place_cod_order_sends_placed_and_staff_notices_after_commit` (calls, in order: `(order.pk, EVENT_PLACED)` then `(order.pk, EVENT_STAFF_NEW_ORDER)`)
   - `test_place_credit_order_sends_placed_and_staff_notices_after_commit`
   - `test_placement_registers_one_callback_and_sends_nothing_before_the_commit` (`execute=False`: exactly one callback, mock not called; then run the callback and check the two calls)
   - `test_replayed_checkout_sends_no_second_notice` (same checkout group twice; the second call registers no callback)
   - `test_failed_placement_sends_nothing` (empty cart raises `EmptyCartError`; no callbacks, mock not called)
   - `test_staff_cancel_sends_the_cancelled_notice` (`transition_order` to `CANCELLED` with a reason)
   - `test_customer_cancel_sends_the_cancelled_notice` (`cancel_order_by_customer`)
   - `test_packing_sends_no_notice`
   - `test_out_for_delivery_sends_its_notice`
   - `test_delivered_sends_its_notice`
   - `test_invalid_transition_sends_nothing` (for example CONFIRMED to DELIVERED raises `InvalidTransitionError`; no callbacks)
   - `test_notice_failure_never_breaks_placement` (do not patch `notify_order_event`; turn both switches on with `settings`, point `SMS_BACKEND` at `FailingSmsBackend` from `apps/notifications/tests/recording.py`, patch `apps.notifications.order_notices.send_mail` to raise; place a COD order with callbacks executed; the order exists, is `CONFIRMED`, and nothing raised)

   Guard-test rules from the brief: this slice adds no `save(update_fields=...)`, no `select_for_update()` and no audit call, so those three rules do not apply.

5. **Docs (last work item).**
   - `docs/decisions.md`, D69: append a short "Slice 38b amendment" paragraph: the hooks, the `on_commit` rule, the event table (which status moves send what), the staff-cancel default, and that `notify_order_event` now also swallows load errors. Do not rewrite the rest of D69.
   - `docs/STATUS.md`: add the 38b log row and the review row text "38a review: 17 of 23 deliberate breakages caught; the six survivors led to the 38b tests"; set the promised count to 1970 + 19 = at least 1989 (not yet run).
   - `docs/open_questions.md`: add `Should the customer get the cancel message when staff cancel an order? — owner; ask again before Phase 5; default in use: yes (D69)`.

## Do not
- Do not change any other app, any model, any migration or any other setting.
- Do not change existing tests, existing return values or the lock order in `apps/orders/services.py`.
- Do not run any command (no pytest, no ruff, no makemigrations, no git). Do not read or edit `.env`.

## Final message
Show the diff summary (files changed, test names and count), list any "Open questions" you chose a default for, then **STOP**. Do nothing after that.

STOP.
