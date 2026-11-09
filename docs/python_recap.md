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

## `transaction.on_commit` with a nested function

`transaction.on_commit(fn)` runs `fn` only after the database transaction commits, so a
rolled-back order never sends a message. Use a nested `def` when the callback needs several
values: a `lambda` looks its variables up when it runs, so a lambda made inside a loop would see
the loop's last value. `apps/orders/services.py`:
```python
def _register_notices(order_id: int, events: tuple[str, ...]) -> None:
    """Send these order events after the current transaction commits (D69)."""

    # Do not use a bare lambda inside a loop here: a lambda looks up its
    # variables when it runs, so every callback would send the loop's last
    # event. A nested function binds the whole tuple now, which avoids that.
    def send_notices() -> None:
        for event in events:
            notify_order_event(order_id, event)

    transaction.on_commit(send_notices)
```
.NET comparison: `TransactionScope.TransactionCompleted` plus a local function, instead of a
closure that captures a loop variable by reference.

## `functools.cache` for a value built once

`@functools.cache` remembers the first result of a function that takes no arguments, so an
expensive value is made once per process and reused. Here it keeps one real password hash so an
unknown login still pays the hashing cost. `apps/accounts/passwords.py`:
```python
@functools.cache
def _dummy_password_hash() -> str:
    """A real encoded password, made once, for identifiers that find nobody."""

    # functools.cache remembers the first result, so the hash is made on the
    # first unknown login and reused afterwards.
    return make_password("dummy-password-for-timing")
```
.NET comparison: a `static readonly Lazy<string>` field, but with no field declaration.

## `cache.add` then `cache.incr` for a counter that expires

`cache.add(key, 0, seconds)` writes the key only when it is missing, and `cache.incr(key)` adds
one. Together they start a fixed window at the first failure: later failures do not extend it.
`apps/accounts/passwords.py`:
```python
def _record_failure(key: str) -> None:
    """Count one wrong try and start the fifteen-minute window if needed."""
    # add() only sets the key when it is missing, so the lock starts at the
    # first failure and the later failures do not extend it.
    cache.add(key, 0, LOGIN_LOCK_SECONDS)
    cache.incr(key)
```
.NET comparison: `IMemoryCache.GetOrCreate` with an absolute expiration, plus an atomic increment.

## `Paginator.get_page` and a bad page number

`Paginator(...).get_page(value)` always returns a real page: a missing, non-numeric, zero or
too-large page number is clamped to the first or last page instead of raising. That is why the
order list needs no `try/except`. `apps/orders/views.py`:
```python
    # get_page turns a bad or too-large page number into a valid page by itself.
    page_obj = Paginator(list_user_order_rows(user), ORDER_LIST_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
```
.NET comparison: `Skip`/`Take` with the page number clamped by hand; here the clamp is built in.

## `TextChoices` and its `.values` / `.label`

`TextChoices` is a small enum whose members are strings, so they can be stored or compared
directly. `.values` is the list of stored strings and `Capability(value).label` reads the
human label back. `apps/accounts/capabilities.py`:
```python
class Capability(TextChoices):
    VIEW_ORDERS = "VIEW_ORDERS", "View orders"
    STOCK_WRITE = "STOCK_WRITE", "Receive, adjust, count or damage stock"
```
and the matrix uses `Capability(capability).label` for a row heading.
.NET comparison: an `enum` with `[Description]` attributes, plus a lookup dictionary built for you.

## `frozenset` for a set that never changes

A `frozenset` is an immutable set: it has no `add`, so nobody can change a module-level default
by accident, and `in` is a constant-time lookup. `apps/accounts/capabilities.py`:
```python
DEFAULTS: dict[str, frozenset[str]] = {
    ROLE_OWNER: frozenset(Capability.values),
    ROLE_PACKER: frozenset({Capability.VIEW_ORDERS, Capability.START_PACKING}),
}
```
.NET comparison: `IReadOnlySet<T>` built once, instead of a mutable `HashSet<T>` field.

## A dictionary keyed by a tuple, with `.get(key, fallback)`

A tuple is hashable, so `(role, capability)` can be a dictionary key. `dict.get(key, fallback)`
returns the stored value or the fallback when the key is absent, which is exactly what "a missing
row means use the default" needs. `apps/accounts/admin.py`:
```python
stored = {(row.role, row.capability): row.allowed for row in RoleCapability.objects.all()}
allowed = stored.get((role, capability), default_allowed(role, capability))
```
.NET comparison: a `Dictionary<(string, string), bool>` plus `TryGetValue` and a default.

## `Mapping` as a read-only dictionary parameter

`Mapping[str, bool]` promises only that the argument can be read like a dictionary, so the caller
may pass a plain `dict` but the function cannot change it. `apps/accounts/capability_services.py`:
```python
def set_role_capabilities(
    actor: User | None,
    role: str,
    changes: Mapping[str, bool],
) -> list[str]:
```
.NET comparison: an `IReadOnlyDictionary<string, bool>` parameter instead of `Dictionary`.

## `cast(Any, super())` inside a plain mixin

A mixin has no base class of its own, so `super().has_view_permission(...)` confuses mypy even
though the class is combined with `ModelAdmin` at run time. `cast(Any, ...)` tells mypy "trust me,
this object has that method". `apps/catalog/admin.py`:
```python
base = cast(Any, super())
return has_capability(request.user, Capability.EXPORT_CSV) or base.has_view_permission(request, obj)
```
.NET comparison: a cast to a base interface where the compiler cannot see the inheritance.

Slice 39b: `right in Capability.values` checks whether a string is one of a `TextChoices` class's values,
so one tuple can hold either a capability or a Django permission name (`apps/inventory/admin.py`, `_allowed`).
.NET comparison: `Enum.IsDefined` before choosing which check to run.
