# Pikki's Info — Grok Build Mode prompt

Build a polished, mobile-friendly web app called **Pikki's Info**: a fan-made
**decompose guide** for the game *Love Nikki: Dress Up Queen*.

## Purpose (read this first)

The primary purpose is the **DECOMPOSE GUIDE**. This app replaces the
now-offline Nikki's Info decompose guide (`ln.nikkis.info/decompose/` and
its wardrobe item pages) with **the same functionality, layout, and
workflow as closely as possible**, but with **up-to-date data** and a new,
fresh **“P” brand** (Pikki's Info) instead of the dated “N”.

This is a **new original implementation**. Do **not** copy Nikki's Info's
source code, logo, typography lockup, or images. Do not reproduce their
favicon or monogram. Use the archived pages and the column list below as a
**functional specification** only:

- Decompose guide (Wayback): `https://web.archive.org/web/*/https://ln.nikkis.info/decompose/`
- Example item page: `https://web.archive.org/web/*/https://ln.nikkis.info/wardrobe/shoes/1`
- Homepage: `https://web.archive.org/web/20260911003029/https://ln.nikkis.info/`

Nikki's Info showed a decomposable-item list, a warning not to decompose
pieces still needed for future crafting, search, category tabs, per-item
**Used to craft** with **Need × N**, required-copy counts, decompose
status, wardrobe ownership, Done tracking, and filters such as Extra
clothes / Still needed / Cannot decompose. Match that workflow.

## Data is live and must not be hardcoded

The app is **data-driven**. At load time it must `fetch` this URL (and must
**not** embed the item catalog in the JS bundle):

```
DATA_URL=https://raw.githubusercontent.com/wdoucette/pikkis-info/main/data/pikkis-info.min.json
```

If that request fails (CORS, 404 before the repo is public, or a transient
error), retry once with the jsDelivr fallback:

```
https://cdn.jsdelivr.net/gh/wdoucette/pikkis-info@main/data/pikkis-info.min.json
```

Show a clear loading state, and a clear error + Retry if both fail. After a
successful load, display in the header (or footer) a persistent **data
version / last updated** line, for example:

`Data: Nikki Calc v0.19b · game data 2026-10-30 · bundle 2026-11-12`

Use `meta.nikkicalc_version`, `meta.nikkicalc_updated`, and `meta.generated`
from the JSON. Convert timestamps to a short date the visitor can read.
This is how the site stays current **without republishing the app**: a
GitHub Actions workflow rebuilds the JSON weekly (Monday 12:00 UTC) and on
manual dispatch, and commits it only when Nikki Calc’s data (or the
overrides sheet) changed (for example `v0.19b` → the next version). Always
fetch `DATA_URL` (cache with revalidation is fine; never ship a stale
hardcoded snapshot as the only source).

Optional: cache the JSON in `localStorage` or IndexedDB so a repeat visit
renders instantly, then re-fetch in the background and refresh if `meta`
changed.

The JSON is several megabytes and contains 34,000+ items. Parse it off the
main thread if needed, index by name / category / number, and do not
render 34,000 DOM nodes at once — virtualize the decompose list or page it.

### JSON shape

Top-level: `{ "v": 1, "app": "Pikki's Info", "meta": {...}, "items": [...] }`.

`meta` includes `source`, `nikkicalc_version`, `nikkicalc_updated`,
`item_count`, `generated`, `counts`, `credit`, and optionally `overrides`.

Each item uses these keys (some are omitted when empty):

| Key | Meaning |
| --- | --- |
| `id` | Numeric item id (Nikki Calc offset). Stable key for ownership and overrides. |
| `n` | Wardrobe number in that category |
| `name` | Item name (middle dot · is common, e.g. Simple Sandals·Black) |
| `cat` | Category / slot (Hair, Dress, Coat, Top, Bottom, Hosiery, Shoes, Accessory, Makeup, …) |
| `r` | Hearts / rarity (1–6, may be missing) |
| `rd` | **Required direct** = sum of recipe + evolution quantities that consume this item |
| `rr` | Required recursive (BOM: 1 kept copy of every craftable + those consumes) |
| `rdc` | Required direct **plus 1 per recolour** of this base |
| `rrc` | Recursive variant of `rdc` |
| `fut` | `1` if Nikki Calc flagged future recipes not yet listed |
| `u` | Used to craft: `[ { "id", "n": name, "q": qty }, ... ]` |
| `cf` | Crafted from (recipe): `[ { "id", "q" }, ... ]` when present |
| `ev` | Evolution: `{ "from": [{ "id", "num" }], "to": [{ "id", "num" }] }` when present |
| `d` | Recolours of this base: `[ { "n": name, "id": dyedItemId }, ... ]` (excludes the base itself) |
| `o` | Obtained-from text (often absent in Nikki Calc; may be filled by an override) |
| `comment` | Editorial comment from the Overrides sheet, when present |
| `used_to_craft_note` | Optional note shown with Used to craft, from an override |
| `cannot_decompose` | `true` when an override marks the item as Cannot decompose |
| `hidden` | `true` to hide the item from the public guide |
| `edited` | Array of compact field names that came from the Overrides sheet (e.g. `["comment"]`, `["rd"]`) |

Credit the data source visibly (footer is fine), using `meta.credit` or:

> Item data from Nikki Calc (nikkicalc.com). Editorial comments and
> corrections come from the Pikki's Info Overrides sheet. Fan
> reconstruction, not affiliated with Elex, Paper, or Nikki's Info.

## Comments and the “edited” marker

- If an item has `comment`, show that text on the **item page** (prominent
  but secondary to Used to craft / Required) and as a comment icon or
  clipped line on the **decompose guide** row (full text on hover or tap).
- If `edited` is a non-empty array, show a visible **edited** marker
  (badge, pill, or labelled asterisk — not just colour) on the item page
  and on the guide row. The tooltip or a short note must list which
  fields were overridden (map keys like `rd` → “Required”, `comment` →
  “Comment”). Do not hide overridden values; show the overridden figure
  and mark it as edited.
- `used_to_craft_note` appears under the Used to craft list when present.
- Honour `cannot_decompose` in Status and the Cannot decompose filter.
- Honour `hidden`: omit hidden items from the public list and search
  (direct URL may show a “hidden” state).

## Required-copy rule (do not “fix” this)

- The main **Required** / **Required in wardrobe** figure is **`rd`**
  (recipes + evolution only). A secondary figure **`rr`** may be shown as
  “including crafting tree” / recursive.
- **Recolours do not belong in the main required count.** Show `rdc` (and
  optionally `rrc`) in a separate column or subtitle such as “incl.
  recolours”. In Love Nikki, making a recolour uses up one
  copy of the base item, so keeping the base plus every recolour needs one
  extra copy per recolour; that is what `rdc` / `rrc` add. Nikki Calc stores
  customisations as a name list plus a dyed-item id, not as a recipe, so
  they are kept out of `rd` / `rr`. The original Nikki's
  Info “Copies needed” number also did not consistently add +1 per dye
  (example: Simple Sandals required 9 for crafts/evolution, even though a
  Black→Blue customisation exists).
- Recommended display: `Required: {rd}` and, when `rdc > rd`,
  `Incl. recolours: {rdc}`.
- If `rd` (or `rdc` / `rr` / `rrc`) is in `edited`, still use the
  overridden number; mark it as edited rather than recomputing over it.

## Brand and visual design

- Name: **Pikki's Info**. Short mark: a distinctive **P** monogram
  (not an N, not Nikki's Info’s logo).
- Fresh, clean, slightly playful wardrobe-magazine feel. Good contrast,
  readable typography, comfortable on a phone. Light theme default, dark
  mode optional.
- Do not use Nikki's Info’s colours/logo as a 1:1 skin. The *information
  architecture* should feel familiar to someone who used that guide; the
  chrome should look like a new site.
- Footer disclaimer: fan-made; Love Nikki belongs to its rights holders;
  data from Nikki Calc; editorial overrides from the Pikki's Info
  Overrides sheet; not affiliated with Elex / Paper / Nikki's Info.

## Screens and workflow

### 1. Decompose guide (this is the home page)

Make `/` the decompose guide, not a marketing landing page. A short
title + one-paragraph warning is enough, in the spirit of Nikki's Info’s
warning: do not decompose clothes you still need for crafting or evolution;
check Used to craft / Required before selling or decomposing.

Layout (desktop: a filter/sidebar or stacked toolbar + a dense list;
mobile: stacked filters, then the list):

**Toolbar**

- Search box (name, and also category + number, e.g. `Shoes 1` or
  `Simple Sandals`). Debounced.
- Category tabs / pills: All, then the wardrobe slots present in the
  data (Hair, Dress, Coat, Top, Bottom, Hosiery, Shoes, Accessory, Makeup,
  and any others). Accessory may be grouped; if the data splits accessory
  subtypes, tabs can follow the data.
- Status / decompose filters, named like the original guide and Paige’s
  sheet (they must work together with search and category):
  - **All**
  - **Extra clothes** — owned, and not still needed for anything unowned
  - **Still needed** — owned, but waiting on at least one unowned product,
    or held back by a quest/design-material style gate
  - **Cannot decompose** — items with `cannot_decompose: true`. If none
    are flagged, the filter can show an empty state rather than inventing
    a list.
  - **Not owned**
  - **Left to do** / hide Done — items the user has not marked Done
  - Optional: **Has leftover copies** (Owned count or a user-entered
    copy count above Required), **Future recipes** (`fut === 1`),
    **Has comment**, **Edited**
- A live count: “Showing X of Y items”.

**List / table columns** (show the important ones on mobile, full table
on desktop; each item name links to its item page):

Functional columns inspired by Paige’s Items tab and the archived guide:

- Item (name, link) plus the **edited** marker and comment indicator
  when those fields are set
- Category
- No. (`n`)
- Hearts / rarity (hearts or star glyphs if `r` is present)
- Obtained from (if `o` is non-empty; otherwise omit or show “—”)
- **Owned** — editable (Yes/No at minimum; a numeric copy count is
  better: 0 = not owned). Stored locally.
- **Status** — derived (not user-typed). See rules below.
- **Waiting on** — count (and/or names) of items this piece is still
  needed for that the user does not own.
- **Held back by** — derived tags (Design material, Customizable,
  Evolvable, Quest material when detectable). “Quest material” only if
  the data actually indicates it; do not invent.
- **Needed for / Used to craft** — names of products, each with
  **Need × N** (use `u[].q`). If the item also evolves into something
  (`ev.to`), include that evolution here or in a sibling line.
- **Required** — `rd` (main). Optionally show `rr` as a secondary
  number. Separate **Incl. recolours** (`rdc`) when different. Mark
  overridden counts as edited.
- **Comment** — `comment` when present
- Gold min / Gold max / Casket energy / Ancient Accessory / Accessory %
  / Star Coin % — **only if those fields appear in the data**. The
  current Nikki Calc bundle does **not** include decompose-payout
  numbers; do not invent them. Leave room in the layout so they can
  appear later if the dataset grows.
- **Done** — checkbox, stored locally.
- Link to the item page.

Sort defaults: category, then number, then name. Allow clicking headers
to sort. Keep the list fast (virtualize or paginate, e.g. 100 rows with
easy “load more”).

### 2. Item pages (`/item/...` or `/wardrobe/{category}/{no}`)

One page per item, deep-linkable. Prefer a URL patterned after the
familiar wardrobe path, e.g. `/wardrobe/shoes/1`, falling back to
`/item/{id}` when category/number is missing. Include a unique-name
fallback because some names collide across slots.

Sections, in a clear readable stack (only show a section if it has
data):

- Title, category, number, hearts/rarity, **edited** marker when
  `edited` is set
- **Comment** — the editorial `comment`, if any
- Short attribute line if you later have attributes; skip if absent
- **Obtained from** (`o`)
- **Crafted from** — recipe `cf`, each ingredient linking to its item
  page, with quantity
- **Evolution** — `ev.from` / `ev.to` with quantities; link through
- **Customisation / dyes** — list `d`, each linking to the recolour
  item when `d[].id` exists in the catalog
- **Used to craft** — the signature Nikki's Info block: every product
  this item is an ingredient for, with **Need × N**, linking through.
  This is the most important section after the required counts. Show
  `used_to_craft_note` under the list when present.
- **Required in wardrobe** — `rd` (and `rr` as “full tree”). Separate
  **Incl. recolours** using `rdc` / `rrc`. Short tooltip explaining the
  rule above. Mark overridden figures.
- **Owned** and **Done** controls (same store as the guide)
- Derived **Status**, **Waiting on**, **Held back by**
- If `fut === 1`, a visible note that some future recipes may be missing
- If `cannot_decompose`, a clear Cannot decompose label
- Back link to the decompose guide, preserving the last filters if
  possible

### 3. Search

Global search from the header, available on every page. Results jump to
item pages or filter the decompose list. Search must work across all
34k items (client-side index is fine once the JSON is loaded). Skip
`hidden` items.

### 4. Wardrobe / ownership

Store **Owned** and **Done** in `localStorage` (and only those; Status
etc. are computed). Key by a stable id: prefer `id`, with a fallback key
of `category + n + name` so a future data refresh can still match.

**Import / export CSV** compatible with Paige’s sheet, so someone can
bring an existing wardrobe:

- Required columns: `Item`, `Category`, `No.`, `Owned`, `Done`
- Accept a fuller Paige export that also has `f`, `Hearts`, `Obtained
  from`, `Status`, `Waiting on`, `Held back by`, `Needed for`, `Copies
  needed`, gold/casket columns, `Link`, etc. Ignore unknown columns.
- Match rows by Category + No. when both are present, else by Item name
  (warn on ambiguous names).
- `Owned`: treat `Yes` / `Y` / `true` / a positive integer as owned.
  A numeric value should be stored as the copy count.
- `Done`: treat `Yes` / `Y` / `true` / `1` as done.
- Export should write at least `Item,Category,No.,Owned,Done` and may
  include derived Status / Waiting on / Required (`rd`) for convenience.
- Provide Export and Import buttons on the decompose guide (and a
  “Clear local wardrobe” with a confirm).

## Status derivation (Paige’s Items tab logic)

Do **not** store Status. Compute it from Owned + recipes + the catalog:

- If `cannot_decompose` → **Cannot decompose** (even if owned)
- If not owned → **Not owned**
- Else if Waiting on > 0 (this item is still required for at least one
  unowned product it is used to craft or evolve into), or Held back by
  includes a real quest-material / design-material gate → **Still needed**
- Else → **Extra clothes**

**Waiting on**: among `u` (and `ev.to` products), count items the user
does not own. Optionally also mention unowned recolours in a lesser
line, but do not let unowned dyes alone flip Status to Still needed
(recolours are not in the main required count).

**Held back by** (tags, when true in the data):

- Customizable — `d` is non-empty
- Evolvable — `ev` is present
- Design material — used as a craft ingredient (`u` non-empty) or has a
  recipe (`cf`), as appropriate
- Quest material — only if obtained-from / flags say so

## Interaction quality

- Fast, keyboard-friendly search (focus `/` or the search box).
- Filters persist in the URL (`?q=&cat=&status=&done=`) so links are
  shareable (they should not include the user’s wardrobe).
- Owned/Done updates must recompute Status / Waiting on immediately
  without a full reload.
- Empty states for “no items match these filters”.
- Accessible labels on checkboxes and the search box. The edited marker
  and comment icon need accessible text, not colour alone.
- No accounts, no backend, no analytics requirement. Pure static front
  end plus `fetch(DATA_URL)` plus `localStorage`.

## What not to do

- Do not hardcode the item list, recipes, or required counts in the
  source. Always load `DATA_URL`.
- Do not copy Nikki's Info’s code, logo, images, or distinctive wordmark.
- Do not invent decompose gold / casket / drop-rate numbers that are not
  in the JSON.
- Do not fold recolours into the main Required number.
- Do not ignore `comment`, `edited`, `cannot_decompose`, or `hidden`.
- Do not require a sign-in.
- Do not scrape or call nikkicalc.com (or the Overrides Google Sheet)
  from the browser app; the JSON on GitHub is the only runtime data
  source.

## Acceptance checks

- Home page *is* the decompose guide, with warning, search, category
  tabs, status filters, Done, and Used to craft Need × N.
- Opening an item (e.g. Shoes #1, Simple Sandals·Black, id 19031) shows
  Used to craft with quantities, required counts, the sample editorial
  **comment**, and an **edited** marker (this item is in the current
  dataset as an example override).
- Importing a CSV with `Item,Category,No.,Owned,Done` updates Status
  filters (Extra clothes / Still needed / Not owned).
- Header/footer shows Nikki Calc version and last-updated dates from
  `meta` after the fetch.
- Killing network *after* first load can still show a cached catalog if
  you implemented cache, but a first visit without `DATA_URL` shows an
  error, not a fake built-in catalog.
- The wordmark is Pikki's Info with a P mark, not an N.
