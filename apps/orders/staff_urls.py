"""Staff routes under ``/staff/`` (D79 amendment).

Order numbers contain slashes (``BS/26-27/000001``), so every order route uses
the ``path`` converter and the exact stored number. The specific sub-pages are
listed before the detail route so the suffix is never taken as part of a
number.
"""

from django.urls import path

from apps.credit import staff_views as credit_views
from apps.orders import staff_views
from apps.payments import staff_views as payment_views

urlpatterns = [
    path("", staff_views.staff_home, name="staff_home"),
    path("orders/", staff_views.order_list, name="staff_order_list"),
    path("pick-list/", staff_views.pick_list, name="staff_pick_list"),
    path("delivery/", staff_views.delivery_list, name="staff_delivery_list"),
    path("cash/", staff_views.cash_page, name="staff_cash"),
    path("credit/", credit_views.dues_list, name="staff_credit_dues"),
    path(
        "credit/<int:account_id>/payment/",
        credit_views.record_payment_view,
        name="staff_record_credit_payment",
    ),
    path(
        "credit/<int:account_id>/remind/",
        credit_views.send_reminder,
        name="staff_send_credit_reminder",
    ),
    # Order sub-pages first; the detail route below catches what is left.
    path(
        "orders/<path:order_number>/slip/",
        staff_views.packing_slip,
        name="staff_packing_slip",
    ),
    path(
        "orders/<path:order_number>/invoice/",
        staff_views.invoice_page,
        name="staff_invoice",
    ),
    path(
        "orders/<path:order_number>/invoice.pdf",
        staff_views.invoice_pdf,
        name="staff_invoice_pdf",
    ),
    path(
        "orders/<path:order_number>/start-packing/",
        staff_views.start_packing_view,
        name="staff_start_packing",
    ),
    path(
        "orders/<path:order_number>/send-out/",
        staff_views.send_out_view,
        name="staff_send_out",
    ),
    path(
        "orders/<path:order_number>/cancel/",
        staff_views.cancel_view,
        name="staff_cancel",
    ),
    path(
        "orders/<path:order_number>/deliver/",
        staff_views.mark_delivered_view,
        name="staff_mark_delivered",
    ),
    path(
        "orders/<path:order_number>/refund/send/",
        payment_views.send_refund_now,
        name="staff_send_refund",
    ),
    path(
        "orders/<path:order_number>/refund/by-hand/",
        payment_views.mark_refunded_by_hand_view,
        name="staff_mark_refunded_by_hand",
    ),
    path(
        "orders/<path:order_number>/",
        staff_views.order_detail,
        name="staff_order_detail",
    ),
]
