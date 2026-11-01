"""Backfill missing thumbnail and medium images (Slice 25)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.catalog import images
from apps.catalog.models import ProductImage


class Command(BaseCommand):
    help = "Create missing thumbnail and medium images for existing photos."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report only, change nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = bool(options["dry_run"])
        made = 0
        skipped = 0
        failed = 0

        for photo in ProductImage.objects.all():
            if not photo.image:
                skipped += 1
                continue
            needs_thumb = not photo.thumbnail
            needs_medium = not photo.medium
            if not (needs_thumb or needs_medium):
                skipped += 1
                continue
            if dry_run:
                made += 1
                continue
            changed = False
            try:
                if needs_thumb:
                    thumb = images.create_thumbnail(photo.image)
                    if thumb is not None:
                        photo.thumbnail.save(f"thumb_{photo.pk}.webp", thumb, save=False)
                        changed = True
                    else:
                        failed += 1
                        continue
                if needs_medium:
                    medium = images.create_medium(photo.image)
                    if medium is not None:
                        photo.medium.save(f"medium_{photo.pk}.webp", medium, save=False)
                        changed = True
                    else:
                        failed += 1
                        continue
                if changed:
                    photo.save(update_fields=["thumbnail", "medium"])
                    made += 1
            except Exception:
                failed += 1

        self.stdout.write(f"Backfill: {made} made, {skipped} skipped, {failed} failed.")
