# Python recap for a .NET developer

A short tour of the Python and Django idioms used in this repo. Every example is copied
from the current code, so you can open the file and read it in context.

## `from __future__ import annotations`

Put this first when type hints mention Django classes; Python then stores the hints as text.
`apps/catalog/search.py`:
```python
from __future__ import annotations

import operator
from dataclasses import dataclass
```
.NET comparison: like `#nullable enable`, a file-level switch that changes how the compiler reads the file.

## Type hints and mypy

Hints are optional at run time, but `mypy` checks them; `str | None` means "a string or None".
`apps/inventory/services.py`:
```python
def _lock_variant(variant: ProductVariant) -> ProductVariant:
    """Lock and reload a variant inside a transaction (ADR D43, point 2)."""
    return ProductVariant.objects.select_for_update().get(pk=variant.pk)
```
.NET comparison: C# nullable reference types plus a compiler check, but Python does not enforce hints when the code runs.

## `@dataclass(frozen=True)`

A dataclass writes `__init__`, `__repr__` and equality for you; `frozen=True` makes it read-only.
`apps/catalog/search.py`:
```python
@dataclass(frozen=True)
class SearchHit:
    """One matching product and its relevance score (higher is better)."""

    product_id: int
    score: float
```
.NET comparison: a `record` with init-only properties, without writing the constructor.

## Protocol (structural typing)

A `Protocol` describes what a caller needs; any class with a matching method fits, even without inheriting from it.
`apps/catalog/search.py`:
```python
class SearchBackend(Protocol):
    """What the storefront needs from a search engine."""

    def search(self, query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[SearchHit]: ...
```
.NET comparison: an interface, except the class does not have to declare that it implements it; the shape alone is enough.

## `*` unpacking

`[*items, last]` builds a new list from the items plus one more value.
`apps/catalog/search.py`:
```python
    # "aata & atta:*" means every word must match and the last one may be a
    # prefix, so "nes" finds "nestle".
    return " & ".join([*words[:-1], words[-1] + ":*"])
```
.NET comparison: collection expressions such as `[.. words[..^1], last]`.

## `getattr` with a default

`getattr(obj, "name", default)` returns the default instead of raising when the attribute is missing.
`apps/catalog/search.py`:
```python
def get_search_backend() -> SearchBackend:
    """Build the backend named in settings, or the PostgreSQL one by default."""
    # getattr keeps the default when the setting is absent.
    path = getattr(settings, "SEARCH_BACKEND", DEFAULT_SEARCH_BACKEND)
```
.NET comparison: `obj?.Name ?? fallback`, but it also works when the property does not exist at all.

## List comprehensions

A comprehension builds a list in one line; read it from the `for` outwards.
`apps/catalog/search.py`:
```python
        # An annotation is invisible to mypy, so read the score through Any.
        return [SearchHit(product_id=row.pk, score=float(cast(Any, row).score)) for row in ranked]
```
.NET comparison: a LINQ `Select(...).ToList()`.

## `reduce` with `operator.or_`

`reduce` folds a list into one value with a two-argument function; `operator.or_` means `|`.
`apps/catalog/search.py`:
```python
        # reduce folds the list into one condition: q1 | q2 | q3 ...
        qs = qs.filter(reduce(operator.or_, conditions))
```
.NET comparison: `conditions.Aggregate((left, right) => left | right)`.

## Q objects and ORM annotations

`annotate` adds a computed column; `Q` objects build a WHERE clause that can be combined with `|` (OR) before `filter`.
`apps/catalog/search.py`:
```python
        qs = Product.objects.annotate(
            search_rank=SearchRank(vector, search_query, normalization=32),
            name_similarity=TrigramWordSimilarity(text, "name_en"),
            brand_similarity=TrigramWordSimilarity(text, "brand__name"),
            keyword_similarity=TrigramWordSimilarity(text, "search_keywords"),
        )
```
.NET comparison: Entity Framework's `Select` plus a predicate composed with `Expression.OrElse`.

## `transaction.atomic` and `select_for_update`

`with transaction.atomic():` wraps everything inside in one transaction; `select_for_update()` adds `FOR UPDATE` so another transaction waits.
`apps/inventory/services.py`:
```python
    with transaction.atomic():
        locked = _lock_variant(variant)
```
.NET comparison: `using (var scope = new TransactionScope())` plus a row lock hint.

## Decimal for money

Money is `Decimal`, never `float`; `quantize` rounds to fixed places and `ROUND_HALF_UP` decides halves.
`apps/catalog/services.py`:
```python
def round_money(amount: Decimal) -> Decimal:
    """Round to whole paise (two decimals), with halves going up."""
    return amount.quantize(PAISE, rounding=ROUND_HALF_UP)
```
.NET comparison: `decimal.Round(value, 2, MidpointRounding.AwayFromZero)`.

## pytest fixtures and `@pytest.mark.django_db`

A fixture is a function pytest runs for the test and passes in by name; `django_db` gives the test a database that is rolled back.
`apps/catalog/tests/conftest.py`:
```python
@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    # Point MEDIA_ROOT at pytest's tmp_path so tests never touch the real media/ folder.
    settings.MEDIA_ROOT = tmp_path
```
`apps/catalog/tests/test_search.py`:
```python
@pytest.mark.django_db
def test_search_finds_by_name_en() -> None:
    product_id = _make_product("Parle-G Biscuits")
    assert _search_ids("parle") == [product_id]
```
.NET comparison: xUnit fixtures and `IAsyncLifetime`, but a fixture is found by argument name instead of an interface.

## factory_boy

A factory creates and saves a model with defaults; `Sequence` gives each object a unique number so unique fields do not collide.
`apps/catalog/tests/factories.py`:
```python
class BrandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Brand

    # Sequence gives each new object a unique number n.
    name = factory.Sequence(lambda n: f"Brand {n}")
    slug = factory.Sequence(lambda n: f"brand-{n}")
```
.NET comparison: an object mother or test-data builder that inserts the row for you.

## Settings overrides in tests

The `settings` fixture changes one Django setting for one test and restores it afterwards.
`apps/catalog/tests/test_search.py`:
```python
def test_get_search_backend_uses_setting(settings) -> None:
    settings.SEARCH_BACKEND = "apps.catalog.tests.test_search.FakeSearchBackend"
    backend = get_search_backend()
    assert isinstance(backend, FakeSearchBackend)
    assert backend.search("anything") == [SearchHit(product_id=1, score=1.0)]
```
.NET comparison: `builder.ConfigureAppConfiguration` in an integration test, but scoped to a single test.
