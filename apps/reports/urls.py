"""Staff report routes under ``/staff/reports/`` (D85).

Each report is one HTML page and one CSV download with the same name, so a
person who has just read a page can download exactly what they saw.
"""

from django.urls import path

from apps.reports import views

urlpatterns = [
    path("", views.index, name="reports_index"),
    path("sales/", views.sales_page, name="reports_sales"),
    path("sales.csv", views.sales_csv, name="reports_sales_csv"),
    path("own-brand/", views.own_brand_page, name="reports_own_brand"),
    path("own-brand.csv", views.own_brand_csv, name="reports_own_brand_csv"),
    path("gst/", views.gst_page, name="reports_gst"),
    path("gst.csv", views.gst_csv, name="reports_gst_csv"),
    path("cod-and-credit/", views.cod_credit_page, name="reports_cod_credit"),
    path("cod-and-credit.csv", views.cod_credit_csv, name="reports_cod_credit_csv"),
    path("stock-valuation/", views.stock_valuation_page, name="reports_stock_valuation"),
    path(
        "stock-valuation.csv",
        views.stock_valuation_csv,
        name="reports_stock_valuation_csv",
    ),
    path("cost-prices/", views.cost_prices_page, name="reports_cost_prices"),
]
