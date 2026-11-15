# Hindi: what is built and what the content slice must do (D92)

The scaffolding is in place (Slice 51, D92). No shop string is translated yet.
This page says what exists and the order the later "Hindi content" slice should
follow.

## What is already in place

- `HINDI_ENABLED` (`.env`, default off) decides whether `"hi"` is in
  `LANGUAGES`. Off, the shop is English only and `/language/` answers 404.
- `apps.core.middleware.ShopLocaleMiddleware` reads the language cookie and the
  `Accept-Language` header exactly as Django does, except that `/admin/` and
  `/staff/` always stay English.
- The header form in `templates/base.html` posts to `/language/`
  (`switch_language`), which delegates to Django's `set_language`.
- `apps/catalog/names.py` gives the visible product name, and the card, the
  detail `<h1>` and the cart line use it.
- `static/css/fonts.css` and `static/fonts/` carry the Devanagari faces for the
  browser, and `apps/orders/invoice_pdf.py` embeds them in the invoice PDF.
- Django's own Hindi catalogue already translates Django's messages (the login
  forms, the validation sentences) as soon as the language is `hi`.

## Why the language lives in a cookie and not in the URL

Every public address stays as it is: `/products/`, `/product/<slug>/`,
`/sitemap.xml`, `/robots.txt`, `/manifest.webmanifest`. A `/hi/...` prefix
would double every route, force a canonical link per language, and change the
PWA's start URL and the sitemap. The cookie keeps one address per page, so a
shared link means one thing. The cost is that a page is not two indexable
pages: the search engine sees the English text.

## The staff pages stay English

The middleware puts the shop's own language back on `/admin/` and `/staff/`
before the page renders, so a packer who chose Hindi on the shop still reads the
staff screens in English. Do not add a language switch to a staff template.

## The content slice, in order

1. Add `gettext` to the Dockerfile (`apt-get install -y gettext`), because
   `makemessages` shells out to it and the image does not have it.
2. Wrap the customer text: `{% translate %}` and `{% blocktranslate %}` in the
   customer templates, `gettext_lazy as _` for the sentences in
   `apps/*/presentation.py`, and `gettext` for the few built at request time.
   Leave the staff templates and the invoice PDF labels in English.
3. `docker compose exec web python manage.py makemessages -l hi` and translate
   `locale/hi/LC_MESSAGES/django.po`.
4. Add `LOCALE_PATHS = [BASE_DIR / "locale"]` to `badshahstore/settings/base.py`
   (this slice deliberately has none) and
   `docker compose exec web python manage.py compilemessages`.
5. Keep `LANGUAGE_CODE = "en-IN"` and `HINDI_ENABLED` off until the `.po` file
   is complete. Then set `HINDI_ENABLED=True` in production's `.env` and restart
   the `web` container.
6. Run `collectstatic` on the deploy (it is part of every deploy already,
   D90): the manifest storage hashes `static/css/fonts.css` and the two font
   files inside it.
7. Check the browser: switch to Hindi, then open a page with a product name, a
   form error and the cart. The invoice PDF needs no work: its English labels
   are already separate from the product name, which keeps its English form on
   an invoice.
