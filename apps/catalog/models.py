"""Catalog app models: brands, categories, products."""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from apps.core.models import ArchivableModel, TimeStampedModel


class Brand(TimeStampedModel, ArchivableModel):
    """A product brand, e.g. a third-party brand or our own spice brand."""

    name = models.CharField(max_length=120, unique=True)
    # Slugs are romanised ASCII (e.g. "chana-dal"). Hindi text goes in name
    # fields, because Django's slug check rejects Devanagari vowel signs.
    slug = models.SlugField(max_length=140, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Category(TimeStampedModel, ArchivableModel):
    """A product category. Categories form a tree through ``parent``."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    # "self" means this model points at its own table. A top-level category
    # has no parent (null=True for the database, blank=True for forms).
    # PROTECT: a category that still has children cannot be deleted.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Product(TimeStampedModel, ArchivableModel):
    """A sellable item, e.g. an atta brand. Pack sizes live on variants."""

    class DietType(models.TextChoices):
        VEG = "VEG", "Vegetarian"
        NON_VEG = "NON_VEG", "Non-vegetarian"
        NOT_APPLICABLE = "NOT_APPLICABLE", "Not applicable"

    name_en = models.CharField("name (English)", max_length=200)
    name_hi = models.CharField("name (Hindi)", max_length=200, blank=True)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField(blank=True)
    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        related_name="products",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    # True for our own brand (spices). Third-party products keep False.
    is_own_brand = models.BooleanField(default=False)
    diet_type = models.CharField(
        max_length=20,
        choices=DietType,
        default=DietType.NOT_APPLICABLE,
    )
    # Extra words customers may type, e.g. "atta aata wheat flour".
    search_keywords = models.TextField(blank=True)

    class Meta:
        ordering = ["name_en"]

    def __str__(self) -> str:
        return self.name_en


class ProductVariant(TimeStampedModel, ArchivableModel):
    """One sellable pack of a product (size, price, tax). This is the SKU."""

    class Unit(models.TextChoices):
        KG = "KG", "kg"
        G = "G", "g"
        ML = "ML", "ml"
        L = "L", "l"
        PIECE = "PIECE", "piece"
        PACKET = "PACKET", "packet"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="variants",
    )
    # Our own internal code. The barcode is the printed one and may be missing.
    sku_code = models.CharField(max_length=40, unique=True)
    # unique + null: many variants may have no barcode, but a set one is unique.
    barcode = models.CharField(max_length=32, unique=True, null=True, blank=True)
    unit = models.CharField(max_length=10, choices=Unit)
    # Size of one pack in `unit`, e.g. 0.5 with KG. Used for unit pricing.
    pack_size = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    # Verify the HSN format and codes with your CA.
    hsn_code = models.CharField(
        max_length=8,
        validators=[RegexValidator(r"^\d{4,8}$", "HSN code must be 4 to 8 digits.")],
    )
    # Editable data, never hard-coded. Confirm rates with your CA.
    gst_rate_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    # Money is always Decimal in INR, never float. MRP includes GST.
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # Label details for own-brand items (Legal Metrology / FSSAI).
    net_quantity_label = models.CharField(max_length=60, blank=True)
    best_before_label = models.CharField(max_length=120, blank=True)
    ingredients = models.TextField(blank=True)
    allergens = models.TextField(blank=True)
    shelf_life = models.CharField(max_length=60, blank=True)
    manufacturer_details = models.TextField(blank=True)

    class Meta:
        ordering = ["sku_code"]
        # These rules are enforced by the database itself.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mrp__gt=0),
                name="catalog_variant_mrp_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(selling_price__gt=0),
                name="catalog_variant_selling_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(selling_price__lte=models.F("mrp")),
                name="catalog_variant_selling_price_lte_mrp",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_price__isnull=True) | models.Q(cost_price__gte=0),
                name="catalog_variant_cost_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(gst_rate_percent__gte=0) & models.Q(gst_rate_percent__lte=100),
                name="catalog_variant_gst_rate_0_to_100",
            ),
        ]

    def __str__(self) -> str:
        return self.sku_code
