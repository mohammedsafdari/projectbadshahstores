"""Promotions app — Coupon, CouponRedemption, Banner, FeaturedProduct."""

from django.apps import AppConfig


class PromotionsConfig(AppConfig):
    name = "apps.promotions"
    label = "promotions"
    default_auto_field = "django.db.models.BigAutoField"
