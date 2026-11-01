"""Tests for the backfill_image_sizes command (Slice 25)."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
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
def test_backfill_creates_missing_medium_and_thumbnail(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    # clear sizes to simulate old row
    pi.thumbnail = None
    pi.medium = None
    pi.save(update_fields=["thumbnail", "medium"])
    call_command("backfill_image_sizes")
    pi.refresh_from_db()
    assert pi.thumbnail
    assert pi.medium


@pytest.mark.django_db
def test_backfill_leaves_existing_sizes_alone(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.refresh_from_db()
    thumb_name = pi.thumbnail.name
    medium_name = pi.medium.name
    call_command("backfill_image_sizes")
    pi.refresh_from_db()
    assert pi.thumbnail.name == thumb_name
    assert pi.medium.name == medium_name


@pytest.mark.django_db
def test_backfill_dry_run_changes_nothing(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.thumbnail = None
    pi.medium = None
    pi.save(update_fields=["thumbnail", "medium"])
    call_command("backfill_image_sizes", "--dry-run")
    pi.refresh_from_db()
    assert not pi.thumbnail
    assert not pi.medium


@pytest.mark.django_db
def test_backfill_skips_broken_file_and_continues(tmp_path, settings) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", b"not an image", content_type="image/jpeg")
    pi = ProductImage.objects.create(product=product, image=upload)
    pi.thumbnail = None
    pi.medium = None
    pi.save(update_fields=["thumbnail", "medium"])
    # should not raise
    call_command("backfill_image_sizes")
    pi.refresh_from_db()
    assert not pi.thumbnail


@pytest.mark.django_db
def test_backfill_summary_counts_made_skipped_failed(tmp_path, settings, capsys) -> None:
    settings.MEDIA_ROOT = tmp_path
    from django.core.files.uploadedfile import SimpleUploadedFile

    product = ProductFactory()
    upload = SimpleUploadedFile("p.jpg", _make_image(20, 20), content_type="image/jpeg")
    ProductImage.objects.create(product=product, image=upload)
    from io import StringIO

    buf = StringIO()
    call_command("backfill_image_sizes", stdout=buf)
    text = buf.getvalue()
    assert "made" in text
    assert "skipped" in text
    assert "failed" in text
