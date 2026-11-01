"""Tests for category tree validation and CategoryTree (Slice 23)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.catalog.models import Category
from apps.storefront.services import CategoryTree
from tests.factories import UserFactory


@pytest.mark.django_db
def test_self_parent_rejected() -> None:
    cat = Category.objects.create(name="A", slug="a")
    cat.parent = cat
    with pytest.raises(ValidationError):
        cat.full_clean()


@pytest.mark.django_db
def test_two_step_cycle_rejected() -> None:
    a = Category.objects.create(name="A", slug="a")
    b = Category.objects.create(name="B", slug="b", parent=a)
    a.parent = b
    with pytest.raises(ValidationError):
        a.full_clean()


@pytest.mark.django_db
def test_three_step_cycle_rejected() -> None:
    a = Category.objects.create(name="A", slug="a")
    b = Category.objects.create(name="B", slug="b", parent=a)
    c = Category.objects.create(name="C", slug="c", parent=b)
    a.parent = c
    with pytest.raises(ValidationError):
        a.full_clean()


@pytest.mark.django_db
def test_valid_reparent_accepted() -> None:
    a = Category.objects.create(name="A", slug="a")
    b = Category.objects.create(name="B", slug="b")
    b.parent = a
    b.full_clean()
    b.save()
    assert b.parent == a


@pytest.mark.django_db
def test_corrupted_cycle_does_not_hang() -> None:
    a = Category.objects.create(name="A", slug="a")
    b = Category.objects.create(name="B", slug="b", parent=a)
    Category.objects.filter(pk=a.pk).update(parent=b.pk)
    a.refresh_from_db()
    b.refresh_from_db()
    tree = CategoryTree()
    ancestors = tree.ancestors(a)
    assert isinstance(ancestors, list)
    assert len(ancestors) <= 2
    assert a.pk in tree.descendant_ids(a)
    assert a.pk not in tree.visible_ids()
    assert b.pk not in tree.visible_ids()


@pytest.mark.django_db
def test_admin_change_form_shows_error(client: Client) -> None:
    cat = Category.objects.create(name="C", slug="c")
    user = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(user)
    url = f"/admin/catalog/category/{cat.pk}/change/"
    response = client.post(url, {"name": "C", "slug": "c", "parent": cat.pk, "is_active": "on"})
    assert response.status_code == 200
    assert "parent" in response.content.decode().lower()
