#!/usr/bin/env python3
"""Thin wrapper around scripts/scrape_lovenikki.py.

Scrapes Nikki Calc, applies editorial overrides, and writes:
  data/pikkis-info.min.json
  data/version.json
  data/overrides.csv  (snapshot, when a usable CSV was available)

Override source, in order:
  1. OVERRIDES_CSV_URL (or --overrides), if it returns a real CSV
  2. The last data/overrides.csv snapshot already in the repo
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRAPER = Path(__file__).resolve().parent / "scrape_lovenikki.py"
DATA = ROOT / "data"
SCRAPE_OUT = ROOT / ".scrape"
DEFAULT_OVERRIDES_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "16Pp0G8ZZrgu94Ko2CLktOw3gaxXaGiiyisVtIse_NLk/export?format=csv"
)


def _looks_like_overrides_csv(text: str) -> bool:
    first = (text or "").lstrip("\ufeff").splitlines()
    if not first:
        return False
    return "item_id" in first[0].casefold()


def fetch_url(url: str, timeout: float = 30) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PikkisInfoBot/1.0 (data update; +https://github.com/wdoucette/pikkis-info)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return int(getattr(resp, "status", 200) or 200), raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        try:
            body = raw.decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(e.code), body
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: overrides URL fetch failed: {e}")
        return 0, ""


def resolve_overrides(cli_value: str | None) -> Path | None:
    DATA.mkdir(parents=True, exist_ok=True)
    source = (cli_value or os.environ.get("OVERRIDES_CSV_URL") or DEFAULT_OVERRIDES_URL).strip()
    snapshot = DATA / "overrides.csv"
    if source.startswith("http://") or source.startswith("https://"):
        code, text = fetch_url(source)
        print(f"Overrides HTTP {code} from {source}")
        if 200 <= code < 300 and _looks_like_overrides_csv(text):
            snapshot.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            print(f"Wrote fresh overrides snapshot: {snapshot}")
            return snapshot
        print(
            "WARNING: could not export the Overrides Google Sheet "
            "(it must be shared as 'anyone with the link can view')."
        )
        print("Sheet: https://docs.google.com/spreadsheets/d/16Pp0G8ZZrgu94Ko2CLktOw3gaxXaGiiyisVtIse_NLk/edit")
        if snapshot.is_file() and _looks_like_overrides_csv(snapshot.read_text(encoding="utf-8")):
            print(f"Keeping the last {snapshot} snapshot.")
            return snapshot
        print("No usable overrides snapshot; building without overrides.")
        return None
    path = Path(source).expanduser()
    if path.is_file() and _looks_like_overrides_csv(path.read_text(encoding="utf-8")):
        return path
    print(f"WARNING: overrides file not usable: {path}")
    if snapshot.is_file() and _looks_like_overrides_csv(snapshot.read_text(encoding="utf-8")):
        print(f"Keeping the last {snapshot} snapshot.")
        return snapshot
    return None


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def write_version(bundle_path: Path) -> dict:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    meta = bundle.get("meta") or {}
    items = bundle.get("items") or []
    if not items:
        raise SystemExit("bundle has no items; refusing to write")
    version = {
        "nikkicalc_version": meta.get("nikkicalc_version"),
        "nikkicalc_updated": meta.get("nikkicalc_updated"),
        "item_count": meta.get("item_count") or len(items),
        "generated": meta.get("generated"),
        "bundle": "data/pikkis-info.min.json",
        "overrides": "data/overrides.csv" if (DATA / "overrides.csv").exists() else None,
    }
    (DATA / "version.json").write_text(json.dumps(version, indent=2) + "\n", encoding="utf-8")
    return version


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overrides", default=None, help="Overrides CSV path or URL")
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse files already in .scrape/")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args(argv)

    ov = resolve_overrides(args.overrides)
    py = sys.executable
    scrape_cmd = [
        py, str(SCRAPER), "scrape",
        "--out", str(SCRAPE_OUT),
        "--delay", str(args.delay),
        "--source", "nikkicalc",
    ]
    if args.skip_fetch:
        scrape_cmd.append("--skip-fetch")
    run(scrape_cmd)

    build_cmd = [
        py, str(SCRAPER), "build-site",
        "--out", str(SCRAPE_OUT),
        "--skip-fetch",
        "--delay", str(args.delay),
    ]
    if ov is not None:
        build_cmd.extend(["--overrides", str(ov)])
    run(build_cmd)

    src = SCRAPE_OUT / "bundle" / "pikkis-info.min.json"
    if not src.is_file() or src.stat().st_size == 0:
        raise SystemExit(f"missing compact bundle: {src}")
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / "pikkis-info.min.json"
    dest.write_bytes(src.read_bytes())
    snap_src = SCRAPE_OUT / "bundle" / "overrides.csv"
    if snap_src.is_file():
        (DATA / "overrides.csv").write_bytes(snap_src.read_bytes())
    version = write_version(dest)
    print(
        "Wrote", dest,
        "Nikki Calc", version["nikkicalc_version"],
        "updated", version["nikkicalc_updated"],
        "items", version["item_count"],
    )


if __name__ == "__main__":
    main()
