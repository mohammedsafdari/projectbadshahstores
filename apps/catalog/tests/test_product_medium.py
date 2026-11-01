"""Tests for the medium image size (Slice 25)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from apps.catalog.models import ProductImage
from apps.catalog.tests.factories import ProductFactory


def _make_image(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer.read()


@pytest.mark.django_db
def test_medium_created_for_new_image(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    assert pi.medium


@pytest.mark.django_db
def test_medium_is_webp_and_at_most_900_px(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(1200, 900), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    with Image.open(pi.medium) as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= 900


@pytest.mark.django_db
def test_medium_not_enlarged_for_small_image(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(50, 40), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    with Image.open(pi.medium) as thumb:
        assert thumb.size == (50, 40)


@pytest.mark.django_db
def test_unchanged_resave_keeps_medium(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    name = pi.medium.name
    pi.alt_text = "x"
    pi.save()
    pi.refresh_from_db()
    assert pi.medium.name == name


@pytest.mark.django_db
def test_replaced_image_gets_new_medium(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    old = pi.medium.name
    pi.image = SimpleUploadedFile("p2.jpg", _make_image(30, 30), content_type="image/jpeg")
    pi.save()
    pi.refresh_from_db()
    assert pi.medium.name != old


@pytest.mark.django_db
def test_medium_failure_still_saves_original(tmp_path, settings, monkeypatch) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.catalog import images

    monkeypatch.setattr(images, "create_medium", lambda source: None)
    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    assert pi.image
