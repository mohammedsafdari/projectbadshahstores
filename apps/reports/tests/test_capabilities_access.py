"""Who may open the reports and who may enter a cost price (D75, D85)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import RoleCapability, User
from apps.reports.tests.helpers import member_client, staff_client
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PAGES = (
    "reports_index",
    "reports_sales",
    "reports_own_brand",
    "reports_gst",
    "reports_cod_credit",
    "reports_stock_valuation",
)

CSV_URLS = (
    "reports_sales_csv",
    "reports_own_brand_csv",
    "reports_gst_csv",
    "reports_cod_credit_csv",
    "reports_stock_valuation_csv",
)


def _user(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _client_for(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.parametrize("name", (*PAGES, "reports_cost_prices"))
def test_anonymous_is_sent_to_the_shop_login(name: str) -> None:
    response = Client().get(reverse(name))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


@pytest.mark.parametrize("name", CSV_URLS)
def test_anonymous_csv_is_sent_to_the_shop_login(name: str) -> None:
    response = Client().get(reverse(name))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


@pytest.mark.parametrize("name", PAGES)
def test_a_customer_is_refused(name: str) -> None:
    assert member_client().get(reverse(name)).status_code == 403


@pytest.mark.parametrize("role", [User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER])
@pytest.mark.parametrize("name", ("reports_index", "reports_sales"))
def test_staff_without_the_capability_is_refused(role: str, name: str) -> None:
    assert _client_for(_user(role)).get(reverse(name)).status_code == 403


@pytest.mark.parametrize("name", PAGES)
def test_the_owner_opens_every_report(name: str) -> None:
    assert staff_client(User.ROLE_OWNER).get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", PAGES)
def test_the_manager_opens_every_report(name: str) -> None:
    assert staff_client(User.ROLE_MANAGER).get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", CSV_URLS)
def test_a_packer_cannot_download_a_csv(name: str) -> None:
    assert staff_client(User.ROLE_PACKER).get(reverse(name)).status_code == 403


def test_owner_and_manager_get_both_new_capabilities_and_other_roles_do_not() -> None:
    for role in (User.ROLE_OWNER, User.ROLE_MANAGER):
        user = _user(role)
        assert has_capability(user, Capability.VIEW_REPORTS) is True
        assert has_capability(user, Capability.MANAGE_COST_PRICES) is True

    for role in (User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER):
        user = _user(role)
        assert has_capability(user, Capability.VIEW_REPORTS) is False
        assert has_capability(user, Capability.MANAGE_COST_PRICES) is False


def test_the_two_new_labels_are_the_agreed_words() -> None:
    assert Capability.VIEW_REPORTS.label == "View reports"
    assert Capability.MANAGE_COST_PRICES.label == "Enter cost prices"


def test_the_cost_page_needs_its_own_capability_not_the_report_one() -> None:
    client = staff_client(User.ROLE_MANAGER)
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.MANAGE_COST_PRICES,
        allowed=False,
    )

    assert client.get(reverse("reports_cost_prices")).status_code == 403
    # The reports themselves still open.
    assert client.get(reverse("reports_sales")).status_code == 200


def test_the_cost_page_opens_for_a_role_granted_only_that_capability() -> None:
    RoleCapability.objects.create(
        role=User.ROLE_PACKER,
        capability=Capability.MANAGE_COST_PRICES,
        allowed=True,
    )
    client = staff_client(User.ROLE_PACKER)

    assert client.get(reverse("reports_cost_prices")).status_code == 200
    # It still does not open the reports.
    assert client.get(reverse("reports_sales")).status_code == 403


def test_the_header_shows_the_reports_link_to_a_manager() -> None:
    html = staff_client(User.ROLE_MANAGER).get(reverse("reports_index")).content.decode()

    assert reverse("reports_index") in html


def test_the_header_hides_the_reports_link_from_a_packer() -> None:
    # The packer reaches a staff page that does not need the reports right.
    html = staff_client(User.ROLE_PACKER).get(reverse("staff_order_list")).content.decode()

    assert reverse("reports_index") not in html


def test_the_index_hides_the_cost_link_without_the_cost_capability() -> None:
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.MANAGE_COST_PRICES,
        allowed=False,
    )

    html = staff_client(User.ROLE_MANAGER).get(reverse("reports_index")).content.decode()

    assert reverse("reports_cost_prices") not in html
    assert reverse("reports_gst") in html


def test_the_index_shows_the_cost_link_with_the_capability() -> None:
    html = staff_client(User.ROLE_MANAGER).get(reverse("reports_index")).content.decode()

    assert reverse("reports_cost_prices") in html


def test_the_index_lists_every_report() -> None:
    html = staff_client(User.ROLE_OWNER).get(reverse("reports_index")).content.decode()

    for name in PAGES:
        assert reverse(name) in html
