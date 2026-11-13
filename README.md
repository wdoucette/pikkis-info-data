# Pikki's Info data

Data pipeline for the Pikki's Info decompose guide (site code lives in `wdoucette/pikkis-info`).

- `data/pikkis-info.min.json` is the bundle the site loads:
  `https://raw.githubusercontent.com/wdoucette/pikkis-info-data/main/data/pikkis-info.min.json`
  (fallback `https://cdn.jsdelivr.net/gh/wdoucette/pikkis-info-data@main/data/pikkis-info.min.json`).
- `.github/workflows/update-data.yml` runs every Monday 12:00 UTC (and on demand) — it scrapes Nikki Calc with
  `scripts/scrape_lovenikki.py`, applies the Overrides Google Sheet, and commits only when the data changed.
- Hand edits go in the Overrides sheet, not here.

Item data from Nikki Calc (nikkicalc.com). Fan project, not affiliated with Elex, Paper Games, or Nikki's Info.
