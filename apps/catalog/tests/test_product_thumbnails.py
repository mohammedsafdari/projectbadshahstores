"""Tests for product image thumbnails (Slice 23b)."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.models import ProductImage
from apps.catalog.tests.factories import ProductFactory


def _make_image(width: int, height: int, mode: str = "RGB") -> SimpleUploadedFile:
    img = Image.new(mode, (width, height), color=(100, 150, 200))
    buffer = io.BytesIO()
    fmt = "PNG" if mode == "RGBA" else "JPEG"
    img.save(buffer, format=fmt)
    buffer.seek(0)
    return SimpleUploadedFile("photo.jpg", buffer.read(), content_type="image/jpeg")


@pytest.mark.django_db
def test_thumbnail_created_for_new_image(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    image = _make_image(20, 20)
    pi = ProductImage.objects.create(product=product, image=image)
    pi.refresh_from_db()
    assert pi.thumbnail
    assert pi.thumbnail.name.endswith(".webp")


@pytest.mark.django_db
def test_thumbnail_webp_and_max_side(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    image = _make_image(800, 600)
    pi = ProductImage.objects.create(product=product, image=image)
    pi.refresh_from_db()
    with Image.open(pi.thumbnail) as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= 400


@pytest.mark.django_db
def test_small_image_not_enlarged(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    image = _make_image(50, 40)
    pi = ProductImage.objects.create(product=product, image=image)
    pi.refresh_from_db()
    with Image.open(pi.thumbnail) as thumb:
        assert thumb.size == (50, 40)


@pytest.mark.django_db
def test_unchanged_resave_keeps_thumbnail(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    image = _make_image(20, 20)
    pi = ProductImage.objects.create(product=product, image=image)
    pi.refresh_from_db()
    name = pi.thumbnail.name
    pi.alt_text = "x"
    pi.save()
    pi.refresh_from_db()
    assert pi.thumbnail.name == name


@pytest.mark.django_db
def test_replaced_image_new_thumbnail(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    pi = ProductImage.objects.create(product=product, image=_make_image(20, 20))
    pi.refresh_from_db()
    old = pi.thumbnail.name
    pi.image = _make_image(30, 30)
    pi.save()
    pi.refresh_from_db()
    assert pi.thumbnail.name != old


@pytest.mark.django_db
def test_thumbnail_failure_still_saves(monkeypatch, tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from apps.catalog import images

    monkeypatch.setattr(images, "create_thumbnail", lambda source: None)
    product = ProductFactory()
    pi = ProductImage.objects.create(product=product, image=_make_image(20, 20))
    pi.refresh_from_db()
    assert pi.image


@pytest.mark.django_db
def test_thumbnail_exception_still_saves(monkeypatch, tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from apps.catalog import images

    def boom(source):
        raise RuntimeError("boom")

    monkeypatch.setattr(images, "create_thumbnail", boom)
    product = ProductFactory()
    pi = ProductImage.objects.create(product=product, image=_make_image(20, 20))
    pi.refresh_from_db()
    assert pi.image


@pytest.mark.django_db
def test_transparent_png_keeps_alpha(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    product = ProductFactory()
    # convert to PNG upload
    img = Image.new("RGBA", (20, 20), color=(100, 150, 200, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    upload = SimpleUploadedFile("photo.png", buf.read(), content_type="image/png")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    with Image.open(pi.thumbnail) as thumb:
        assert "A" in thumb.getbands() or thumb.mode in ("RGBA", "LA")
