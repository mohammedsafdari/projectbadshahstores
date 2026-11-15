# Lighthouse on the staging server

The page timings of the shop are measured on **staging**, not on the development PC: a laptop with a
hot-reloading dev server and a local database says nothing about the server a customer reaches. The
first run after Slice 53 is the **baseline**: there is no target yet, and a target will be chosen
from that baseline rather than the other way round (D90, D93).

## When

- After Slice 53 has put the production stack (gunicorn, nginx, the built CSS, `collectstatic`) on
  the Oracle staging server, with the real catalogue loaded where possible.
- Again at Slice 55 on the real server, and after any change that touches the page weight (a new
  script, a new image size, a new font).
- The date and the commit of every run go in the table below, so two runs are comparable.

## The procedure

1. Open **Chrome DevTools** (F12) on the staging address over HTTPS.
2. Open a **private window** (Ctrl+Shift+N) and sign in only if the page needs it. A private window
   starts with an empty cache, which is what a first-time visitor has.
3. Open the **Lighthouse** panel, choose **Mobile**, and leave every category ticked (Performance,
   Accessibility, Best Practices, SEO).
4. Tick **Clear storage** before the run, so the service worker's caches (D91) are emptied first and
   the run measures the network rather than the worker.
5. Press **Analyze page load** and wait for the report.
6. Write the four category scores and the three timings from the report — **LCP** (Largest
   Contentful Paint), **CLS** (Cumulative Layout Shift) and **TBT** (Total Blocking Time) — in the
   row for that page.
7. Run the same page twice if the first score looks unusual; record the second run and say so.

## The pages

The five pages a customer meets first, each in its own row:

| Page | Address on staging | Why it is measured |
| --- | --- | --- |
| Home | `/` | Banners, the featured grid and the real introduction |
| A category | `/category/<slug>/` | The filter panel and a page of product cards |
| A product | `/product/<slug>/` | The heaviest page: several photos and variants |
| Search results | `/search/?q=…` | The filters, the sort order and the result cards |
| The cart | `/cart/` | A form-heavy page with quantities and totals |

Other pages worth a run when they change: `/checkout/` (a signed-in customer with a full cart),
`/staff/orders/` (the busiest staff screen) and `/search/suggest/?q=…` (the fragment the header
asks for on every keystroke).

## The results

One row per page per run. Copy the row rather than editing it in place, so the history stays.

| Date | Commit | Page | Performance | Accessibility | Best Practices | SEO | LCP | CLS | TBT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | Home | | | | | | | |
| | | Category | | | | | | | |
| | | Product | | | | | | | |
| | | Search results | | | | | | | |
| | | Cart | | | | | | | |

## Notes

- **The first run is the baseline.** No score in this file is a promise: a target is written here
  only after the baseline exists, and then it is one number per page and per category.
- A score below 100 in **Best Practices** is often a third-party cookie or a console error from the
  payment page, not the shop's own code; read the report's own details before changing anything.
- Lighthouse measures one page in one simulated phone profile on one connection. It is a way to
  compare two runs of the same page, not a measure of how fast the shop feels to a customer in
  Indore.
- The largest paint cost is no longer a third-party script: the Tailwind CDN was removed in Slice 52
  (D93), so the stylesheet is the shop's own file, hashed and cached for a year (D90).
- The service worker stores the offline page and static files (D91). A run with **Clear storage**
  ticked therefore also empties those caches; an installed app on a real phone will usually be
  faster, not slower.
