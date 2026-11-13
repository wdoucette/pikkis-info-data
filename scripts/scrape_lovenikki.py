#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_lovenikki.py
===================
Standalone Love Nikki data scraper and "Pikki's Info" bundle builder.

Primary source: Nikki Calc (https://nikkicalc.com/) public JSON under /data/,
discovered from the site's own JS (version string, e.g. v0.19b). This is an
outfit calculator (updated 2026-10-30 in v0.19b) that also ships structured
item records with recipe, usedIn (id + quantity), evolution, customisation,
and a futureRecipe flag. robots.txt allows User-agent * / but sets
Content-Signal: search=yes, ai-train=no, use=reference. This script is for
local reference / fan reconstruction only; be polite (default 1 req/s).

Fallback source: Love Nikki Wiki MediaWiki API
  https://lovenikki.miraheze.org/w/api.php
  Category:Wardrobe Item (~19,500 pages), CC BY-SA 4.0.

Game-rule required counts
-------------------------
Direct (required_direct):
  Sum of quantities of this item consumed to produce other items:
    * usedIn[].num  (crafting recipes that list this item as an ingredient)
    * evolvesToCount for each evolution that consumes this item
  Customisation is tracked separately: producing a non-self recolour is
  assumed to consume 1 copy of the source (Love Nikki dye rule). That +1
  per recolour is added in required_direct_custom, not in required_direct.

  The wiki "Used to craft" table for Simple Sandals-Black sums to 34
  (8 recipes). Nikki Calc lists those same 8 at the same quantities AND
  additional later recipes (25 total, direct sum 115, plus futureRecipe).
  The script treats Nikki Calc as authoritative when present, and reports
  the wiki-subset sum for validation.

Recursive (required_recursive):
  Assume we want one kept copy of every item in the dataset (keep=1).
  times_crafted[C] = 1 + sum over consumers D of (qty(C used in D) * times_crafted[D])
  required_from_recipes[X] = sum over products C of (qty(X used in C) * times_crafted[C])
  required_recursive[X] = required_from_recipes[X] + 1   (the keep copy of X itself)
  Evolution consumes are included as edges. Customisation edges are optional
  (same +1 rule) via required_recursive_custom. Cycles are broken (value 0
  on the back-edge) and flagged.

CLI examples
------------
  python scrape_lovenikki.py scrape --out ./nikki_data --limit 100
  python scrape_lovenikki.py scrape --out ./nikki_data --skip-fetch
  python scrape_lovenikki.py build-site --out ./nikki_data

Do not run a full scrape until you are ready (see --limit). Never publishes
anything; Phase II writes a local preview, a compact JSON bundle, and a
Grok Build Mode prompt for you to paste at grok.me.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.0"
UA = (
    f"NikkiRebuild/{VERSION} (local Love Nikki reference rebuild; "
    "stdlib urllib; polite 1 req/s; not for model training)"
)
WIKI_API = "https://lovenikki.miraheze.org/w/api.php"
WIKI_CATEGORY = "Category:Wardrobe Item"
NIKKICALC_ORIGIN = "https://nikkicalc.com"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"

# Always fetched during a limited test so Simple Sandals validation works.
SEED_PERMALINKS = [
    "1simpl", "2simpl",
    "103grace", "348bunny", "3046final", "1235ampli",
    "1290napon", "1222redhe", "523peach", "350cloud",
    "100ocean", "101ocean", "102ocean",
]
WIKI_SANDALS_SUBSET = {
    "Graceful Orchid": 4,
    "Bunny High Heels": 7,
    "Final Redemption": 6,
    "Amplitude Modulation": 4,
    "Nap on the Eaves": 4,
    "Red Heart Boots": 4,
    "Peach Blossom Shoes": 3,
    "Cloud Furry Boots": 2,
}  # sum 34
WIKI_SEED_TITLES = [
    "Simple Sandals-Black", "Simple Sandals-Blue", "Graceful Orchid",
    "Bunny High Heels", "Final Redemption", "Amplitude Modulation",
    "Nap on the Eaves", "Red Heart Boots", "Peach Blossom Shoes",
    "Cloud Furry Boots", "Oceanic Pearl (Necklace)",
    "Oceanic Pearl-Rare", "Oceanic Pearl-Epic",
]

SLOT_NAMES = {
    0: "Hair", 1: "Hair", 2: "Dress", 3: "Dress", 4: "Dress",
    5: "Coat", 6: "Coat", 7: "Top", 8: "Top", 9: "Top",
    10: "Bottom", 11: "Bottom", 12: "Bottom", 13: "Leglet", 14: "Hosiery",
    15: "Shoes", 16: "Shoes", 17: "Makeup",
    18: "Hair ornament", 19: "Veil", 20: "Earring", 21: "Scarf",
    22: "Necklace", 23: "Necklace", 24: "Right hand ornament",
    25: "Left hand ornament", 26: "Gloves", 27: "Right handheld",
    28: "Left handheld", 29: "Waist", 30: "Veil", 31: "Face accessory",
    32: "Brooch", 33: "Tattoo", 34: "Wings", 35: "Tail",
    36: "Foreground", 37: "Background", 38: "Right hand ornament",
    39: "Left hand ornament", 40: "Dress", 41: "Wings", 42: "Hairpin",
    43: "Ear", 44: "Gloves", 45: "Gloves", 46: "Tail", 47: "Coat",
    48: "Head ornament", 49: "Skin", 50: "Ground", 51: "Coat",
    52: "Gloves", 53: "Dress", 54: "Background", 55: "Dress",
    56: "Bottom", 57: "Both handheld", 60: "Spirit", 61: "Dress",
    62: "Coat", 63: "Top", 64: "Coat", 65: "Both handheld",
    66: "Gloves", 67: "Gloves", 68: "Brooch",
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class HttpError(RuntimeError):
    def __init__(self, url: str, status: Optional[int], msg: str):
        super().__init__(f"HTTP {status} {url}: {msg}")
        self.url, self.status = url, status


def http_get(url: str, delay: float, timeout: float = 60, retries: int = 4) -> bytes:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        if delay and attempt == 0:
            time.sleep(delay)
        elif delay and attempt:
            time.sleep(min(delay * (2 ** attempt), 30))
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = HttpError(url, e.code, e.reason)
            if e.code in (404, 410, 400, 401, 403):
                raise last
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if retry_after:
                try:
                    time.sleep(min(float(retry_after), 60))
                except ValueError:
                    pass
        except Exception as e:  # noqa: BLE001 — network
            last = e
    raise last  # type: ignore[misc]


def http_get_json(url: str, delay: float, **kw: Any) -> Any:
    raw = http_get(url, delay, **kw)
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Nikki Calc
# ---------------------------------------------------------------------------

def discover_nikkicalc_version(delay: float) -> Tuple[str, int, int]:
    """Return (version, batch_size, update_unix) from the items JS bundle."""
    items_html = http_get(f"{NIKKICALC_ORIGIN}/items/", delay).decode("utf-8", "replace")
    m = re.search(r'src="(/?(?:\./)?js/items-[A-Z0-9]+\.js)"', items_html)
    if not m:
        # fallback: try known recent version
        return "v0.19b", 500, 0
    js_path = m.group(1).lstrip(".").replace("//", "/")
    if not js_path.startswith("/"):
        js_path = "/" + js_path
    items_js = http_get(f"{NIKKICALC_ORIGIN}{js_path}", delay).decode("utf-8", "replace")
    # items-*.js imports d as J, e as b, i as H from chunks/WFL3YU36.js
    chunk_m = re.search(r'from\s*"(\./chunks/WFL3YU36\.js)"', items_js)
    chunk_path = "/js/chunks/WFL3YU36.js"
    if chunk_m:
        # resolve relative to /js/
        chunk_path = "/js/" + chunk_m.group(1).lstrip("./")
    chunk = http_get(f"{NIKKICALC_ORIGIN}{chunk_path}", delay).decode("utf-8", "replace")
    ver = re.search(r'var z="(v[0-9][^"]+)"', chunk)
    batch = re.search(r",ae=(\d+)", chunk)
    ts = re.search(r'var se="(\d+)"', chunk)
    if not ver:
        # looser
        ver = re.search(r'"(v\d+\.\d+[a-zA-Z]?)"', chunk)
    version = ver.group(1) if ver else "v0.19b"
    batch_size = int(batch.group(1)) if batch else 500
    update_unix = int(ts.group(1)) if ts else 0
    return version, batch_size, update_unix


def nikkicalc_paths(raw_dir: Path) -> Dict[str, Path]:
    return {
        "meta": raw_dir / "meta.json",
        "key_to_file": raw_dir / "key-to-file.json",
        "items_index": raw_dir / "items-index.json",
        "suits": raw_dir / "suits.json",
        "suit_permalinks": raw_dir / "suit-permalinks.json",
        "batches": raw_dir / "batches",
    }


def fetch_nikkicalc(out: Path, delay: float, limit: Optional[int], resume: bool = True) -> Dict[str, Any]:
    raw_dir = out / "nikkicalc"
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = nikkicalc_paths(raw_dir)
    paths["batches"].mkdir(exist_ok=True)

    meta_path = paths["meta"]
    if resume and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        version = meta["version"]
        batch_size = meta["batch_size"]
        update_unix = meta.get("update_unix") or 0
        print(f"Resuming Nikki Calc version {version}")
    else:
        print("Discovering Nikki Calc data version from site JS…")
        version, batch_size, update_unix = discover_nikkicalc_version(delay)
        meta = {
            "version": version,
            "batch_size": batch_size,
            "update_unix": update_unix,
            "origin": NIKKICALC_ORIGIN,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"  version={version} batch_size={batch_size} updated_unix={update_unix}")

    def cached(url: str, dest: Path) -> Any:
        if resume and dest.exists() and dest.stat().st_size > 0:
            return json.loads(dest.read_text(encoding="utf-8"))
        print(f"  GET {url}")
        data = http_get_json(url, delay)
        dest.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        return data

    key_to_file = cached(
        f"{NIKKICALC_ORIGIN}/data/items/key-to-file-{version}.json",
        paths["key_to_file"],
    )
    # optional indexes (useful, not required for item records)
    for label, url, dest in [
        ("items-index", f"{NIKKICALC_ORIGIN}/data/items-{version}.json", paths["items_index"]),
        ("suits", f"{NIKKICALC_ORIGIN}/data/suits-{version}.json", paths["suits"]),
        ("suit-permalinks", f"{NIKKICALC_ORIGIN}/data/suit-permalinks-{version}.json", paths["suit_permalinks"]),
    ]:
        try:
            cached(url, dest)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {label}: {e}")

    # Select offsets
    seed_offsets = []
    missing_seeds = []
    for perm in SEED_PERMALINKS:
        if perm in key_to_file:
            seed_offsets.append(int(key_to_file[perm]))
        else:
            missing_seeds.append(perm)
    if missing_seeds:
        print(f"  warning: seed permalinks not in key-to-file: {missing_seeds}")

    all_offsets = sorted(set(int(v) for v in key_to_file.values()))
    if limit is not None:
        # lowest N numeric offsets (few batches) plus seeds (may add a few batches)
        first = all_offsets[:limit]
        wanted = sorted(set(first) | set(seed_offsets))
        print(f"  limit={limit}: {len(wanted)} unique item offsets (includes {len(seed_offsets)} seeds)")
    else:
        wanted = all_offsets
        print(f"  full catalog: {len(wanted)} item offsets")

    batch_ids = sorted({(o // batch_size) * batch_size for o in wanted})
    items: Dict[int, dict] = {}
    for bid in batch_ids:
        dest = paths["batches"] / f"item-batch-{version}-{bid}.json"
        url = f"{NIKKICALC_ORIGIN}/data/items/item-batch-{version}-{bid}.json"
        batch = cached(url, dest)
        for k, rec in batch.items():
            off = int(k)
            if off in wanted or limit is None:
                rec = dict(rec)
                rec["_offset"] = off
                items[off] = rec
    # If limit, keep only wanted
    if limit is not None:
        items = {o: items[o] for o in wanted if o in items}

    meta["item_count"] = len(items)
    meta["batch_ids"] = batch_ids
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  loaded {len(items)} item records from {len(batch_ids)} batches")
    return {"meta": meta, "key_to_file": key_to_file, "items": items}


# ---------------------------------------------------------------------------
# Wiki fallback
# ---------------------------------------------------------------------------

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def wiki_api(delay: float, **params: Any) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    params.setdefault("maxlag", "5")
    url = WIKI_API + "?" + urllib.parse.urlencode(params, doseq=True)
    return http_get_json(url, delay)


def enumerate_wiki_titles(delay: float, limit: Optional[int]) -> List[str]:
    titles: List[str] = []
    seen = set()
    # seeds first
    for t in WIKI_SEED_TITLES:
        if t not in seen:
            titles.append(t)
            seen.add(t)
    if limit is not None and len(titles) >= limit:
        return titles[:limit] if False else titles  # keep seeds even if > limit
    cont = None
    while True:
        kw: Dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": WIKI_CATEGORY,
            "cmtype": "page",
            "cmlimit": 500,
        }
        if cont:
            kw["cmcontinue"] = cont
        data = wiki_api(delay, **kw)
        for m in data.get("query", {}).get("categorymembers", []):
            t = m["title"]
            if t not in seen:
                titles.append(t)
                seen.add(t)
        if limit is not None and len(titles) >= limit + len(WIKI_SEED_TITLES):
            # keep seeds + up to limit others
            extras = [t for t in titles if t not in WIKI_SEED_TITLES][:limit]
            return list(dict.fromkeys(WIKI_SEED_TITLES + extras))
        cont = (data.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    return titles


def _match_braces(text: str, start: int) -> Optional[int]:
    """start points at '{'; return index after matching '}}' or None."""
    if start + 1 >= len(text) or text[start:start + 2] != "{{":
        return None
    i = start + 2
    depth = 1
    n = len(text)
    while i < n:
        if text[i:i + 2] == "{{":
            depth += 1
            i += 2
            continue
        if text[i:i + 2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        i += 1
    return None


def iter_templates(text: str, name: Optional[str] = None) -> Iterable[Tuple[str, str]]:
    """Yield (template_name, inner) for top-level and nested {{templates}}."""
    i = 0
    n = len(text)
    want = name.casefold().replace("_", " ") if name else None
    while i < n:
        j = text.find("{{", i)
        if j < 0:
            return
        end = _match_braces(text, j)
        if end is None:
            return
        inner = text[j + 2:end - 2]
        # name is up to first |
        pipe = inner.find("|")
        tname = (inner[:pipe] if pipe >= 0 else inner).strip()
        body = inner[pipe + 1:] if pipe >= 0 else ""
        key = tname.casefold().replace("_", " ")
        if want is None or key == want or key.startswith(want):
            yield tname, body
        # Advance only past '{{' so nested templates (e.g. Crafting Table/Row
        # inside Crafting Table) are also found.
        i = j + 2


def split_wiki_args(body: str) -> Tuple[List[str], Dict[str, str]]:
    pos: List[str] = []
    named: Dict[str, str] = {}
    parts: List[str] = []
    buf = []
    depth = 0
    bracket = 0
    for ch in body:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "[":
            bracket += 1
            buf.append(ch)
        elif ch == "]":
            bracket = max(0, bracket - 1)
            buf.append(ch)
        elif ch == "|" and depth == 0 and bracket == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            named[k.strip().casefold()] = v.strip()
        else:
            if p.strip() != "" or pos:
                pos.append(p.strip())
    return pos, named


def _strip_wiki(text: str) -> str:
    text = COMMENT_RE.sub("", text)
    text = re.sub(r"\[\[(?:[^\|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^}]*\}\}", " ", text)
    text = re.sub(r"<br\s*/?>", ", ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def parse_wiki_page(title: str, wikitext: str) -> Optional[dict]:
    text = COMMENT_RE.sub("", wikitext or "")
    if re.match(r"\s*#(redirect|REDIRECT)", text):
        return None
    if re.search(r"\{\{\s*Disambig", text, re.I):
        return None
    infobox = None
    for tname, body in iter_templates(text, "clothing infobox"):
        infobox = body
        break
    if infobox is None:
        return None
    _, fields = split_wiki_args(infobox)
    name = fields.get("name") or title
    typ = fields.get("type") or ""
    wardrobe = fields.get("wardrobe nr") or fields.get("wardrobe_nr") or ""
    try:
        vis = int(re.sub(r"\D", "", wardrobe) or "0") or None
    except ValueError:
        vis = None
    rarity_raw = fields.get("rarity") or ""
    rm = re.search(r"(\d+)", rarity_raw)
    rarity = int(rm.group(1)) if rm else None
    obtained = _strip_wiki(fields.get("how to obtain") or "")

    # Recipe (crafted from)
    crafted_from: List[dict] = []
    for tname, body in iter_templates(text, "recipe"):
        _, named = split_wiki_args(body)
        for i in range(1, 8):
            item = named.get(f"item{i}") or named.get(f"item{i} ")
            qty = named.get(f"item{i}_quantity") or named.get(f"item{i} quantity") or named.get(f"item{i}quantity")
            if item:
                try:
                    n = int(qty) if qty and qty.isdigit() else None
                except ValueError:
                    n = None
                crafted_from.append({"name": item, "num": n})

    # Used-to-craft table
    used_to: List[dict] = []
    for tname, body in iter_templates(text, "crafting table/row"):
        pos, named = split_wiki_args(body)
        item = pos[0] if pos else named.get("1")
        needed = named.get("needed")
        if item:
            try:
                n = int(needed) if needed and re.match(r"^\d+$", needed) else None
            except ValueError:
                n = None
            used_to.append({"name": item, "num": n})

    # Evolution
    ev_from, ev_to = [], []
    for tname, body in iter_templates(text, "evolutionfrom"):
        pos, named = split_wiki_args(body)
        item = pos[0] if pos else ""
        qty = int(pos[1]) if len(pos) > 1 and pos[1].isdigit() else None
        ev_from.append({"name": item, "num": qty})
    for tname, body in iter_templates(text, "evolutionto"):
        pos, named = split_wiki_args(body)
        item = pos[0] if pos else ""
        qty = int(pos[1]) if len(pos) > 1 and pos[1].isdigit() else None
        ev_to.append({"name": item, "num": qty})

    # Customisation
    customs = []
    for tname, body in iter_templates(text, "customization"):
        pos, named = split_wiki_args(body)
        other = pos[0] if pos else ""
        dyes = {k: v for k, v in named.items()}
        customs.append({"other": other, "args": dyes})

    return {
        "source": "wiki",
        "title": title,
        "name": name,
        "type": typ,
        "vis": vis,
        "rarity": rarity,
        "obtained": obtained,
        "crafted_from": crafted_from,
        "used_to_craft_listed": used_to,
        "evolves_from": ev_from,
        "evolves_to": ev_to,
        "customisations_wiki": customs,
        "wikitext_len": len(wikitext or ""),
    }


def fetch_wiki(out: Path, delay: float, limit: Optional[int], resume: bool = True,
               titles: Optional[List[str]] = None) -> Dict[str, dict]:
    raw = out / "wiki_raw"
    raw.mkdir(parents=True, exist_ok=True)
    batches_dir = raw / "batches"
    batches_dir.mkdir(exist_ok=True)
    titles_path = raw / "titles.json"
    if titles is None:
        if resume and titles_path.exists() and limit is None:
            titles = json.loads(titles_path.read_text(encoding="utf-8"))
        else:
            print("Enumerating wiki Category:Wardrobe Item…")
            titles = enumerate_wiki_titles(delay, limit)
            titles_path.write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wiki titles: {len(titles)}")
    pages: Dict[str, dict] = {}
    # resume: load existing batches
    if resume:
        for p in sorted(batches_dir.glob("batch_*.json")):
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                for rec in blob:
                    if rec.get("title") and rec.get("wikitext") is not None:
                        pages[rec["title"]] = rec
            except Exception:
                pass
    missing = [t for t in titles if t not in pages]
    BATCH = 50
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        dest = batches_dir / f"batch_{i:05d}.json"
        print(f"  wiki fetch {i + 1}–{i + len(chunk)} / {len(missing)}")
        data = wiki_api(
            delay,
            action="query",
            titles="|".join(chunk),
            prop="revisions",
            rvprop="content",
            rvslots="main",
            redirects="1",
        )
        recs = []
        for p in data.get("query", {}).get("pages", []):
            title = p.get("title")
            if p.get("missing"):
                recs.append({"title": title, "wikitext": None, "missing": True})
                continue
            rev = (p.get("revisions") or [{}])[0]
            slots = rev.get("slots") or {}
            text = (slots.get("main") or {}).get("content") or rev.get("content") or ""
            recs.append({"title": title, "wikitext": text, "pageid": p.get("pageid")})
            pages[title] = recs[-1]
        dest.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return pages


# ---------------------------------------------------------------------------
# Normalize Nikki Calc records + required counts
# ---------------------------------------------------------------------------

def slot_name(slot: Any) -> str:
    try:
        return SLOT_NAMES.get(int(slot), f"slot-{slot}")
    except (TypeError, ValueError):
        return str(slot or "")


def normalize_nc_item(rec: dict) -> dict:
    off = int(rec.get("_offset") or rec.get("id") or -1)
    vis_raw = rec.get("vis")
    try:
        vis = int(vis_raw) if vis_raw not in (None, "") else None
    except ValueError:
        vis = None
    used = [{"id": int(x["id"]), "num": int(x["num"])} for x in rec.get("usedIn") or [] if "id" in x and "num" in x]
    recipe = [{"id": int(x["id"]), "num": int(x["num"])} for x in rec.get("recipe") or [] if "id" in x and "num" in x]
    ev_from_ids = rec.get("evolvesFrom") or []
    ev_from_n = rec.get("evolvesFromCount") or []
    ev_to_ids = rec.get("evolvesTo") or []
    ev_to_n = rec.get("evolvesToCount") or []
    if not isinstance(ev_from_ids, list):
        ev_from_ids = [ev_from_ids] if ev_from_ids is not None else []
    if not isinstance(ev_from_n, list):
        ev_from_n = [ev_from_n] if ev_from_n is not None else []
    if not isinstance(ev_to_ids, list):
        ev_to_ids = [ev_to_ids] if ev_to_ids is not None else []
    if not isinstance(ev_to_n, list):
        ev_to_n = [ev_to_n] if ev_to_n is not None else []
    ev_from = []
    for i, eid in enumerate(ev_from_ids):
        ev_from.append({"id": int(eid), "num": int(ev_from_n[i]) if i < len(ev_from_n) and ev_from_n[i] is not None else None})
    ev_to = []
    for i, eid in enumerate(ev_to_ids):
        ev_to.append({"id": int(eid), "num": int(ev_to_n[i]) if i < len(ev_to_n) and ev_to_n[i] is not None else None})
    customs = rec.get("customisations") or []
    return {
        "id": off,
        "vis": vis,
        "name": rec.get("name") or "",
        "slot": rec.get("slot"),
        "category": slot_name(rec.get("slot")),
        "rarity": rec.get("rarity"),
        "desc": rec.get("desc") or "",
        "permalink": rec.get("nameAbbr") or "",
        "used_in": used,
        "recipe": recipe,
        "evolves_from": ev_from,
        "evolves_to": ev_to,
        "customisations": customs,
        "future_recipe": bool(rec.get("futureRecipe")),
        "source": "nikkicalc",
    }


def _times_crafted(items: Dict[int, dict], include_custom: bool) -> Dict[int, int]:
    """times_crafted[C] = 1 (keep) + copies of C consumed to craft others."""
    consumers: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    # ingredient X used in product P with num n  => P consumes n of X
    for pid, it in items.items():
        for ing in it.get("recipe") or []:
            consumers[int(ing["id"])].append((pid, int(ing["num"])))
        # used_in on ingredient side (authoritative when present)
        for u in it.get("used_in") or []:
            consumers[pid].append((int(u["id"]), int(u["num"])))
        for ev in it.get("evolves_to") or []:
            if ev.get("num"):
                consumers[pid].append((int(ev["id"]), int(ev["num"])))
        if include_custom:
            for c in it.get("customisations") or []:
                dyed = c.get("dyedItem")
                if dyed is not None and int(dyed) != pid:
                    consumers[pid].append((int(dyed), 1))
    # dedupe edges (recipe + used_in inversion would double)
    deduped: Dict[int, Dict[int, int]] = defaultdict(dict)
    for src, lst in consumers.items():
        for dst, n in lst:
            # keep max if both listed (should match)
            prev = deduped[src].get(dst)
            if prev is None or n > prev:
                deduped[src][dst] = n

    memo: Dict[int, int] = {}
    stack: set = set()

    def rec(cid: int) -> int:
        if cid in memo:
            return memo[cid]
        if cid in stack:
            return 1  # cycle: count the keep only
        stack.add(cid)
        total = 1
        for dst, n in deduped.get(cid, {}).items():
            if dst in items:
                total += n * rec(dst)
            else:
                # product not in this (limited) dataset: still count n * 1 keep
                total += n
        stack.remove(cid)
        memo[cid] = total
        return total

    for cid in items:
        rec(cid)
    return memo


def compute_required(items: Dict[int, dict]) -> None:
    name_of = {i: items[i]["name"] for i in items}

    def attach(it: dict, include_custom: bool) -> Tuple[int, int, List[dict]]:
        edges: List[Tuple[int, int]] = []
        seen = set()
        def add(dst: int, n: int) -> None:
            key = (dst, n)
            if (dst, "any") in seen and False:
                return
            # unique by dest (prefer existing)
            if dst in {d for d, _ in edges}:
                return
            edges.append((dst, n))
        for u in it.get("used_in") or []:
            add(int(u["id"]), int(u["num"]))
        for ev in it.get("evolves_to") or []:
            if ev.get("num"):
                add(int(ev["id"]), int(ev["num"]))
        if include_custom:
            for c in it.get("customisations") or []:
                dyed = c.get("dyedItem")
                if dyed is not None and int(dyed) != it["id"]:
                    add(int(dyed), 1)
        # invert recipes of others if used_in empty
        if not it.get("used_in"):
            for oid, other in items.items():
                if oid == it["id"]:
                    continue
                for ing in other.get("recipe") or []:
                    if int(ing["id"]) == it["id"]:
                        add(oid, int(ing["num"]))
        direct = sum(n for _, n in edges)
        used_named = [{"id": d, "name": name_of.get(d, ""), "num": n} for d, n in edges]
        return direct, used_named

    # First pass: named used-to-craft + direct (no custom / with custom)
    for it in items.values():
        d, used = attach(it, False)
        dc, used_c = attach(it, True)
        it["used_to_craft"] = used
        it["required_direct"] = d
        it["required_direct_custom"] = dc

    times = _times_crafted(items, include_custom=False)
    times_c = _times_crafted(items, include_custom=True)
    for it in items.values():
        rec_from = 0
        for u in it.get("used_to_craft") or []:
            dst, n = int(u["id"]), int(u["num"])
            rec_from += n * times.get(dst, 1)
        it["required_recursive"] = rec_from + 1
        rec_from_c = 0
        # rebuild custom edges similarly
        _, used_c = attach(it, True)
        for u in used_c:
            dst, n = int(u["id"]), int(u["num"])
            rec_from_c += n * times_c.get(dst, 1)
        it["required_recursive_custom"] = rec_from_c + 1
        it["times_crafted"] = times.get(it["id"], 1)


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------

CSV_COLS = [
    "id", "vis", "name", "category", "rarity", "permalink",
    "required_direct", "required_recursive", "required_direct_custom",
    "required_recursive_custom", "future_recipe", "used_to_craft", "source",
]


def write_datasets(out: Path, items: Dict[int, dict], meta: dict) -> Path:
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "items": [items[k] for k in sorted(items)],
    }
    json_path = data_dir / "items.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = data_dir / "items.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(items):
            it = dict(items[k])
            utc = it.get("used_to_craft") or []
            it["used_to_craft"] = "; ".join(
                f"{u.get('name') or u.get('id')} x{u.get('num')}" for u in utc
            )
            w.writerow({c: it.get(c, "") for c in CSV_COLS})
    print(f"Wrote {json_path} and {csv_path} ({len(items)} items)")
    return json_path



# --- Editorial overrides (Google Sheet CSV) ---------------------------------

OVERRIDE_FIELDS: Dict[str, Tuple[str, str]] = {
    # field in the sheet -> (item key, value type)
    "comment": ("comment", "text"),
    "cannot_decompose": ("cannot_decompose", "bool"),
    "required_override": ("required_direct", "int"),
    "required_recursive_override": ("required_recursive", "int"),
    "used_to_craft_note": ("used_to_craft_note", "text"),
    "obtained_from": ("obtained", "text"),
    "hidden": ("hidden", "bool"),
    "rd": ("required_direct", "int"),
    "rr": ("required_recursive", "int"),
    "rdc": ("required_direct_custom", "int"),
    "rrc": ("required_recursive_custom", "int"),
    "name": ("name", "text"),
    "cat": ("category", "text"),
    "n": ("vis", "int"),
    "r": ("rarity", "int"),
    "fut": ("future_recipe", "bool"),
}

# Item-key -> compact bundle key, used for the `edited` marker list.
OVERRIDE_COMPACT_KEY = {
    "comment": "comment",
    "cannot_decompose": "cannot_decompose",
    "required_direct": "rd",
    "required_recursive": "rr",
    "used_to_craft_note": "used_to_craft_note",
    "obtained": "o",
    "hidden": "hidden",
    "required_direct_custom": "rdc",
    "required_recursive_custom": "rrc",
    "name": "name",
    "category": "cat",
    "vis": "n",
    "rarity": "r",
    "future_recipe": "fut",
}


def _parse_bool(raw: str) -> Optional[bool]:
    s = (raw or "").strip().casefold()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


def _parse_override_value(kind: str, raw: str) -> Tuple[Optional[Any], Optional[str]]:
    if kind == "text":
        return raw, None
    if kind == "int":
        s = (raw or "").strip().replace(",", "")
        if s == "":
            return None, "empty integer"
        try:
            return int(s), None
        except ValueError:
            return None, f"not an integer: {raw!r}"
    if kind == "bool":
        v = _parse_bool(raw)
        if v is None:
            return None, f"not a boolean: {raw!r}"
        return v, None
    return None, f"unknown type {kind}"


def fetch_overrides_csv(source: str, delay: float = 1.0) -> Tuple[str, Path]:
    """Return (csv_text, origin description). `source` is a URL or local path."""
    src = (source or "").strip()
    if not src:
        raise SystemExit("Empty overrides source")
    if src.startswith("http://") or src.startswith("https://"):
        raw = http_get(src, delay=delay)
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return text, Path("overrides.csv")
    path = Path(src).expanduser()
    if not path.is_file():
        raise SystemExit(f"Overrides file not found: {path}")
    return path.read_text(encoding="utf-8"), path


def parse_overrides_csv(csv_text: str) -> Tuple[List[dict], List[str]]:
    """Parse the Overrides tab. Returns (valid-looking rows, skip logs).

    Rows with a missing item_id or field are logged and omitted. Type and
    item-id checks happen in apply_overrides, once the catalog is in memory.
    """
    logs: List[str] = []
    rows: List[dict] = []
    # Strip a UTF-8 BOM if Sheets adds one
    sample = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(sample.splitlines())
    if not reader.fieldnames:
        logs.append("overrides CSV has no header row")
        return [], logs
    # tolerate slight header variants
    def col(row: dict, *names: str) -> str:
        lower = {((k or "").strip().casefold()): (v if v is not None else "") for k, v in row.items()}
        for n in names:
            if n.casefold() in lower:
                return str(lower[n.casefold()]).strip()
        return ""

    for i, raw in enumerate(reader, start=2):
        item_id_s = col(raw, "item_id", "id")
        field = col(raw, "field")
        value = col(raw, "value")
        item_name = col(raw, "item_name", "name")
        if not item_id_s and not field and not value:
            continue  # blank row
        if not item_id_s or not field:
            logs.append(f"row {i}: skipped (missing item_id or field)")
            continue
        try:
            item_id = int(item_id_s)
        except ValueError:
            logs.append(f"row {i}: skipped unknown item_id {item_id_s!r}")
            continue
        rows.append({
            "line": i,
            "item_id": item_id,
            "item_name": item_name,
            "field": field.casefold(),
            "value": value,
            "comment": col(raw, "comment"),
            "editor": col(raw, "editor"),
            "date": col(raw, "date"),
        })
    return rows, logs


def apply_overrides(items: Dict[int, dict], rows: List[dict]) -> Tuple[int, List[str]]:
    """Merge validated override rows into the in-memory item catalog.

    Unknown item_id or field values are skipped and logged, not applied.
    item_name is reference-only: a mismatch is logged but does not block.
    """
    logs: List[str] = []
    applied = 0
    for row in rows:
        iid = row["item_id"]
        field = row["field"]
        line = row["line"]
        if iid not in items:
            logs.append(f"row {line}: skipped unknown item_id {iid}")
            continue
        spec = OVERRIDE_FIELDS.get(field)
        if not spec:
            logs.append(f"row {line}: skipped unknown field {field!r} (item_id {iid})")
            continue
        key, kind = spec
        parsed, err = _parse_override_value(kind, row["value"])
        if err:
            logs.append(f"row {line}: skipped item_id {iid} field {field}: {err}")
            continue
        it = items[iid]
        ref = row.get("item_name") or ""
        if ref and it.get("name") and _norm(ref) != _norm(str(it.get("name"))):
            logs.append(
                f"row {line}: item_name {ref!r} does not match catalog "
                f"{it.get('name')!r} for item_id {iid} (applied anyway; name is reference-only)"
            )
        it[key] = parsed
        compact = OVERRIDE_COMPACT_KEY.get(key, key)
        edited = list(it.get("edited") or [])
        if compact not in edited:
            edited.append(compact)
        it["edited"] = edited
        applied += 1
    return applied, logs


def load_and_apply_overrides(items: Dict[int, dict], source: str, snapshot: Optional[Path], delay: float = 1.0) -> None:
    try:
        csv_text, _origin = fetch_overrides_csv(source, delay=delay)
    except Exception as e:  # noqa: BLE001 — network or missing file should not abort a scrape
        print(f"WARNING: could not load overrides from {source}: {e}")
        print("Continuing without applying new overrides (existing snapshot left unchanged).")
        return
    head = (csv_text or "").lstrip("\ufeff").lstrip()[:80].casefold()
    if head.startswith("<!doctype") or head.startswith("<html"):
        print(f"WARNING: overrides source {source} returned HTML, not CSV (sheet may be private).")
        print("Continuing without applying new overrides (existing snapshot left unchanged).")
        return
    if snapshot is not None:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(csv_text if csv_text.endswith("\n") else csv_text + "\n", encoding="utf-8")
        print(f"Overrides snapshot: {snapshot}")
    rows, parse_logs = parse_overrides_csv(csv_text)
    applied, apply_logs = apply_overrides(items, rows)
    for line in parse_logs + apply_logs:
        print("overrides:", line)
    print(f"Overrides: applied {applied} of {len(rows)} parsed row(s) from {source}")


def resolve_overrides_source(cli_value: Optional[str]) -> Optional[str]:
    if cli_value:
        return cli_value
    return (os.environ.get("OVERRIDES_CSV_URL") or "").strip() or None


def compact_bundle(items: Dict[int, dict], meta: dict) -> dict:
    compact_items = []
    for k in sorted(items):
        it = items[k]
        row = {
            "id": it["id"],
            "n": it.get("vis"),
            "name": it.get("name"),
            "cat": it.get("category"),
            "r": it.get("rarity"),
            "rd": it.get("required_direct"),
            "rr": it.get("required_recursive"),
            "fut": 1 if it.get("future_recipe") else 0,
            "u": [
                # `name` is what App.tsx displays; keep `n` as a compact name alias.
                {"id": u["id"], "n": u.get("name") or "", "name": u.get("name") or "", "q": u.get("num")}
                for u in (it.get("used_to_craft") or [])
            ],
        }
        if it.get("required_direct_custom") is not None:
            row["rdc"] = it["required_direct_custom"]
        if it.get("required_recursive_custom") is not None:
            row["rrc"] = it["required_recursive_custom"]
        if it.get("recipe"):
            row["cf"] = [
                {"id": x["id"], "q": x.get("num")}
                for x in it["recipe"]
            ]
        if it.get("evolves_from") or it.get("evolves_to"):
            row["ev"] = {
                "from": it.get("evolves_from") or [],
                "to": it.get("evolves_to") or [],
            }
        dyes = []
        self_id = it.get("id")
        for c in it.get("customisations") or []:
            if not isinstance(c, dict):
                continue
            dyed = c.get("dyedItem")
            try:
                dyed_id = int(dyed) if dyed is not None else None
            except (TypeError, ValueError):
                dyed_id = None
            if dyed_id is not None and dyed_id == self_id:
                continue  # skip the base item listed as its own dye
            name = c.get("name") or ""
            if not name and dyed_id is None:
                continue
            entry: Dict[str, Any] = {}
            if name:
                # `name` is what App.tsx displays; keep `n` as a compact name alias.
                entry["n"] = name
                entry["name"] = name
            if dyed_id is not None:
                entry["id"] = dyed_id
            if entry:
                dyes.append(entry)
        if dyes:
            row["d"] = dyes
        if it.get("obtained"):
            row["o"] = it["obtained"]
        if it.get("comment"):
            row["comment"] = it["comment"]
        if it.get("used_to_craft_note"):
            row["used_to_craft_note"] = it["used_to_craft_note"]
        if it.get("cannot_decompose"):
            row["cannot_decompose"] = True
        if it.get("hidden"):
            row["hidden"] = True
        if it.get("edited"):
            row["edited"] = list(it["edited"])
        compact_items.append(row)
    return {
        "v": 1,
        "app": "Pikki's Info",
        "meta": {
            "source": meta.get("primary_source"),
            "nikkicalc_version": (meta.get("nikkicalc") or {}).get("version"),
            "nikkicalc_updated": (meta.get("nikkicalc") or {}).get("update_iso"),
            "item_count": len(compact_items),
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": "required_direct (rd) = sum of recipe/evolution quantities that consume this item; "
                      "required_recursive (rr) = BOM explosion assuming 1 kept copy of every item + those consumes; "
                      "rdc/rrc add +1 per recolour of this base; "
                      "fut=1 means Nikki Calc flagged future recipes not yet listed. "
                      "comment / cannot_decompose / hidden / used_to_craft_note / edited come from the overrides sheet when present.",
            "credit": "Item data from Nikki Calc (nikkicalc.com) and/or Love Nikki Wiki (CC BY-SA 4.0). "
                      "Fan reconstruction, not affiliated with Elex, Paper, or Nikki's Info. "
                      "Editorial comments and overrides are from the Pikki's Info Overrides sheet.",
            "overrides": meta.get("overrides") or None,
        },
        "items": compact_items,
    }


def write_build_mode_prompt(path: Path, bundle_name: str, bundle_bytes: int, item_count: int) -> None:
    """Copy the canonical Build Mode prompt if present; otherwise write a short DATA_URL prompt.

    The full functional spec lives in BUILD_MODE_PROMPT.md next to this script
    (and in the pikkis-info repo). Weekly builds must not regress it to a
    stale short prompt, and the generated app must fetch DATA_URL rather than
    hardcoding the catalog.
    """
    data_url = "https://raw.githubusercontent.com/wdoucette/pikkis-info/main/data/pikkis-info.min.json"
    fallback_url = "https://cdn.jsdelivr.net/gh/wdoucette/pikkis-info@main/data/pikkis-info.min.json"
    canonical = Path(__file__).resolve().parent / "BUILD_MODE_PROMPT.md"
    if canonical.is_file():
        path.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text(
            "\n".join([
                "# Pikki's Info — Grok Build Mode prompt",
                "",
                "Build a polished, mobile-friendly web app called **Pikki's Info**: a fan-made",
                "**decompose guide** for Love Nikki: Dress Up Queen. Primary purpose is the",
                "decompose guide (home page), matching the archived Nikki's Info decompose",
                "workflow (Used to craft Need × N, required counts, ownership, Done, filters",
                "Extra clothes / Still needed / Cannot decompose, category tabs, search,",
                "wardrobe item pages) with up-to-date data and a fresh P brand. Original",
                "implementation — do not copy Nikki's Info code, logo, or images.",
                "",
                "The app is data-driven. At load time it must fetch DATA_URL and must not",
                "hardcode the item catalog:",
                "",
                f"    DATA_URL={data_url}",
                "",
                "Fallback:",
                "",
                f"    {fallback_url}",
                "",
                "Show data version / last updated from meta.nikkicalc_version,",
                "meta.nikkicalc_updated, and meta.generated. A GitHub Actions workflow",
                "rebuilds the JSON weekly (Monday 12:00 UTC) and on manual dispatch, and",
                "commits only if the data changed. No front-end republish is required for",
                "data updates.",
                "",
                f"This bundle is {bundle_bytes:,} bytes, {item_count} items, file {bundle_name}.",
                "Item keys: i, n, c, no, h, o, r, e, u, d, rd, rr, rdc, rrc, fut.",
                "Required in wardrobe = rd (recipes + evolution). Separate incl. recolours = rdc.",
                "Ownership/Done in localStorage; CSV import/export columns Item, Category, No., Owned, Done.",
                "Credit Nikki Calc as the data source.",
                "",
            ]) + "\n",
            encoding="utf-8",
        )
    (path.parent / "SIZE_NOTE.txt").write_text(
        (
            f"The compact bundle is {bundle_bytes:,} bytes ({item_count} items) — too large to "
            "hardcode into a Build Mode app. Host it at:\n\n"
            f"  {data_url}\n\n"
            "The app must fetch DATA_URL at load time (see BUILD_MODE_PROMPT.md). Weekly "
            "GitHub Actions rebuilds this file when Nikki Calc data changes; no front-end "
            "republish is required.\n"
        ),
        encoding="utf-8",
    )


def write_local_site(out: Path, items: Dict[int, dict], meta: dict, bundle_rel: str) -> None:
    site = out / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "assets").mkdir(exist_ok=True)
    rows = [items[k] for k in sorted(items, key=lambda i: ((items[i].get("name") or "").casefold(), i))]
    # minimal preview so Wayne can glance at test output locally
    css = """
:root { font-family: Georgia, "Times New Roman", serif; background:#f6f1e8; color:#1c1612; }
body { max-width: 960px; margin: 0 auto; padding: 1.5rem; }
header, footer { border-color:#d9cbb8; }
a { color:#6b2d3c; }
input { font: inherit; padding:.35rem .5rem; width: min(100%, 28rem); }
table { border-collapse: collapse; width:100%; font-size:.95rem; }
th, td { border-bottom:1px solid #e6d9c8; padding:.35rem .4rem; text-align:left; vertical-align:top; }
.muted { color:#6b5b4f; font-size:.85rem; }
.fut { color:#8a2f12; font-weight:bold; }
"""
    (site / "assets" / "style.css").write_text(css, encoding="utf-8")
    index_rows = []
    for it in rows:
        fut = " yes" if it.get("future_recipe") else ""
        href = f"items/i{it['id']}.html"
        index_rows.append(
            f"<tr data-n=\"{it.get('name','').lower()}\" data-v=\"{it.get('vis') or ''}\">"
            f"<td><a href=\"{href}\">{_esc(it.get('name'))}</a></td>"
            f"<td>{it.get('vis') or ''}</td><td>{_esc(it.get('category'))}</td>"
            f"<td>{it.get('required_direct')}</td><td>{it.get('required_recursive')}</td>"
            f"<td class='fut'>{fut}</td></tr>"
        )
    search_js = """
const q=document.getElementById('q');
q&&q.addEventListener('input',()=>{
  const s=q.value.toLowerCase().trim();
  document.querySelectorAll('tbody tr').forEach(tr=>{
    const ok=!s|| (tr.dataset.n||'').includes(s) || (tr.dataset.v||'')===s;
    tr.hidden=!ok;
  });
});
"""
    index = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Pikki's Info (local preview)</title>
<link rel="stylesheet" href="assets/style.css"></head>
<body>
<header><h1>Pikki's Info</h1>
<p class="muted">Local preview of {len(rows)} items. Publish the real app with the Grok Build Mode prompt in ../bundle/BUILD_MODE_PROMPT.md. Data bundle: {bundle_rel}</p>
<p><a href="decompose.html">Decompose guide</a></p>
<p><label>Search <input id="q" type="search" placeholder="Name or wardrobe number"></label></p>
</header>
<table><thead><tr><th>Item</th><th>#</th><th>Category</th><th>Required (direct)</th><th>Required (recursive)</th><th>Future</th></tr></thead>
<tbody>{''.join(index_rows)}</tbody></table>
<footer class="muted"><p>Fan reconstruction, not affiliated with Elex, Paper, or Nikki's Info. Data from Nikki Calc and/or Love Nikki Wiki (CC BY-SA 4.0).</p></footer>
<script>{search_js}</script></body></html>"""
    (site / "index.html").write_text(index, encoding="utf-8")

    decomp = [it for it in rows if (it.get("required_direct") or 0) > 0 or it.get("future_recipe") or it.get("used_to_craft")]
    drows = []
    for it in decomp:
        u = ", ".join(f"{_esc(u.get('name') or u.get('id'))} ×{u.get('num')}" for u in (it.get("used_to_craft") or [])[:8])
        if len(it.get("used_to_craft") or []) > 8:
            u += "…"
        drows.append(
            f"<tr><td><a href='items/i{it['id']}.html'>{_esc(it.get('name'))}</a></td>"
            f"<td>{it.get('vis') or ''}</td><td>{it.get('required_direct')}</td>"
            f"<td>{it.get('required_recursive')}</td><td>{u}</td></tr>"
        )
    (site / "decompose.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Decompose guide — Pikki's Info</title>
<link rel="stylesheet" href="assets/style.css"></head><body>
<p><a href="index.html">Home</a></p>
<h1>Decompose guide</h1>
<p class="muted">Required (direct) is the sum of Need×N across recipes and evolutions that consume the item. Recursive also assumes you keep one of every product. The game will not decompose your last copy.</p>
<table><thead><tr><th>Item</th><th>#</th><th>Direct</th><th>Recursive</th><th>Used to craft</th></tr></thead>
<tbody>{''.join(drows)}</tbody></table>
</body></html>""",
        encoding="utf-8",
    )
    items_dir = site / "items"
    items_dir.mkdir(exist_ok=True)
    for it in rows:
        urows = "".join(
            f"<li>{_esc(u.get('name') or u.get('id'))} — Need ×{u.get('num')}</li>"
            for u in (it.get("used_to_craft") or [])
        )
        (items_dir / f"i{it['id']}.html").write_text(
            f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(it.get('name'))} — Pikki's Info</title>
<link rel="stylesheet" href="../assets/style.css"></head><body>
<p><a href="../index.html">Home</a> · <a href="../decompose.html">Decompose</a></p>
<h1>{_esc(it.get('name'))}</h1>
<p>{_esc(it.get('category'))} no. {it.get('vis') or '—'} · rarity {it.get('rarity') or '—'}</p>
<p>Required in wardrobe (direct): <strong>{it.get('required_direct')}</strong><br>
Required (recursive, keep 1 of each product): <strong>{it.get('required_recursive')}</strong></p>
{"<p class='fut'>Flagged for future recipes not fully listed yet.</p>" if it.get("future_recipe") else ""}
<h2>Used to craft</h2>
<ul>{urows or "<li class='muted'>None listed in this dataset.</li>"}</ul>
</body></html>""",
            encoding="utf-8",
        )


def _esc(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def find_sandals(items: Dict[int, dict]) -> Optional[dict]:
    for it in items.values():
        n = (it.get("name") or "").replace("·", "-").replace("—", "-")
        if n.casefold() == "simple sandals-black" and (it.get("vis") == 1 or it.get("permalink") == "1simpl"):
            return it
        if it.get("permalink") == "1simpl":
            return it
    return None


def validate_sandals(items: Dict[int, dict]) -> Dict[str, Any]:
    it = find_sandals(items)
    report: Dict[str, Any] = {"found": bool(it)}
    if not it:
        report["error"] = "Simple Sandals-Black not in dataset"
        return report
    used = it.get("used_to_craft") or it.get("used_in") or []
    # map by normalized name
    by_name = {}
    for u in used:
        nm = (u.get("name") or "").replace("·", "-")
        if nm:
            by_name[nm.casefold()] = u.get("num")
    subset = {}
    missing = []
    for name, qty in WIKI_SANDALS_SUBSET.items():
        got = by_name.get(name.casefold())
        subset[name] = {"expected": qty, "got": got, "ok": got == qty}
        if got != qty:
            missing.append(name)
    subset_sum = sum(WIKI_SANDALS_SUBSET.values())
    got_subset_sum = sum(v["got"] or 0 for v in subset.values())
    report.update({
        "id": it.get("id"),
        "name": it.get("name"),
        "required_direct": it.get("required_direct"),
        "required_recursive": it.get("required_recursive"),
        "used_to_craft_count": len(used),
        "wiki_subset_expected_sum": subset_sum,
        "wiki_subset_got_sum": got_subset_sum,
        "wiki_subset_ok": got_subset_sum == subset_sum and not missing,
        "wiki_subset": subset,
        "future_recipe": it.get("future_recipe"),
    })
    return report


def print_validation(report: Dict[str, Any]) -> None:
    print("\n===== Simple Sandals-Black validation =====")
    if not report.get("found"):
        print("FAIL:", report.get("error"))
        return
    print(f"  name={report['name']!r} id={report['id']}")
    print(f"  required_direct (Nikki Calc / computed) = {report['required_direct']}")
    print(f"  required_recursive = {report['required_recursive']}")
    print(f"  used-to-craft rows = {report['used_to_craft_count']}; future_recipe={report['future_recipe']}")
    print(f"  wiki 8-recipe subset expected sum = {report['wiki_subset_expected_sum']}")
    print(f"  wiki 8-recipe subset got sum      = {report['wiki_subset_got_sum']}  "
          f"{'OK' if report['wiki_subset_ok'] else 'MISMATCH'}")
    for name, rec in report["wiki_subset"].items():
        flag = "OK" if rec["ok"] else "DIFF"
        print(f"    {flag}  {name}: expected {rec['expected']}, got {rec['got']}")


# ---------------------------------------------------------------------------
# Wayback (optional, resume, slow)
# ---------------------------------------------------------------------------

def fetch_wayback(out: Path, delay: float, limit: Optional[int]) -> None:
    raw = out / "wayback"
    raw.mkdir(parents=True, exist_ok=True)
    cdx_path = raw / "cdx.json"
    if not cdx_path.exists():
        url = (
            WAYBACK_CDX
            + "?"
            + urllib.parse.urlencode({
                "url": "ln.nikkis.info/wardrobe/*",
                "output": "json",
                "collapse": "urlkey",
                "filter": "statuscode:200",
                "fl": "original,timestamp,statuscode,mimetype,length",
            })
        )
        print("CDX query (may be rate-limited by archive.org)…")
        try:
            data = http_get_json(url, max(delay, 1.5), timeout=90)
            cdx_path.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print("  CDX failed:", e)
            return
    else:
        data = json.loads(cdx_path.read_text(encoding="utf-8"))
    rows = data[1:] if data and isinstance(data[0], list) and data[0] and data[0][0] == "original" else data
    print(f"  CDX unique URLs: {len(rows)}")
    dest_dir = raw / "pages"
    dest_dir.mkdir(exist_ok=True)
    n = 0
    for row in rows:
        if limit is not None and n >= limit:
            break
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        original, ts = row[0], row[1]
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", original.split("wardrobe", 1)[-1])[:80]
        dest = dest_dir / f"{ts}_{slug}.html"
        if dest.exists():
            n += 1
            continue
        id_url = f"https://web.archive.org/web/{ts}id_/{original}"
        try:
            body = http_get(id_url, max(delay, 1.5), timeout=45, retries=2)
            dest.write_bytes(body)
            n += 1
            print(f"  saved {dest.name} ({len(body)} bytes)")
        except Exception as e:  # noqa: BLE001
            print(f"  skip {original}: {e}")


# ---------------------------------------------------------------------------
# Build-site / scrape orchestration
# ---------------------------------------------------------------------------

def load_items_from_out(out: Path) -> Tuple[Dict[int, dict], dict]:
    path = out / "data" / "items.json"
    if not path.exists():
        raise SystemExit(f"No {path}. Run scrape first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = {int(it["id"]): it for it in payload["items"]}
    return items, payload.get("meta") or {}


def cmd_scrape(args: argparse.Namespace) -> None:
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    delay = float(args.delay)
    limit = args.limit
    source = args.source
    meta: Dict[str, Any] = {
        "primary_source": source,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    items: Dict[int, dict] = {}

    if not args.skip_fetch and source in ("auto", "nikkicalc"):
        try:
            nc = fetch_nikkicalc(out, delay, limit, resume=True)
            items = {off: normalize_nc_item(rec) for off, rec in nc["items"].items()}
            um = nc["meta"]
            if um.get("update_unix"):
                um["update_iso"] = datetime.fromtimestamp(int(um["update_unix"]), tz=timezone.utc).isoformat()
            meta["nikkicalc"] = um
            meta["primary_source"] = "nikkicalc"
        except Exception as e:  # noqa: BLE001
            print("Nikki Calc fetch failed:", e)
            if source == "nikkicalc":
                raise
            print("Falling back to wiki…")
            source = "wiki"

    if (not items and source in ("auto", "wiki")) or args.also_wiki:
        if not args.skip_fetch:
            wiki_pages = fetch_wiki(out, delay, limit, resume=True)
        else:
            wiki_pages = {}
            wr = out / "wiki_raw" / "batches"
            if wr.exists():
                for p in wr.glob("batch_*.json"):
                    for rec in json.loads(p.read_text(encoding="utf-8")):
                        if rec.get("title"):
                            wiki_pages[rec["title"]] = rec
        parsed = 0
        # Wiki is fallback: if we already have Nikki Calc items, skip merge of full catalog
        # unless --also-wiki. A limited wiki parse of seeds is used only when NC failed.
        if not items:
            next_id = 1
            by_name: Dict[str, int] = {}
            tmp_list = []
            for title, rec in wiki_pages.items():
                p = parse_wiki_page(title, rec.get("wikitext") or "")
                if not p:
                    continue
                parsed += 1
                tmp_list.append(p)
            # assign ids and invert recipes into used_in
            for i, p in enumerate(tmp_list, start=1):
                p["id"] = i
                p["category"] = (p.get("type") or "").split(",")[0].strip() or "Unknown"
                p["permalink"] = ""
                p["used_in"] = []
                p["recipe"] = []
                p["future_recipe"] = False
                p["customisations"] = []
                items[i] = p
                by_name[_norm(p["name"])] = i
                by_name[_norm(p.get("title") or "")] = i
            # resolve recipe / used-to-craft names to ids
            for p in tmp_list:
                recs = []
                for cf in p.get("crafted_from") or []:
                    nid = by_name.get(_norm(cf["name"]))
                    if nid and cf.get("num") is not None:
                        recs.append({"id": nid, "num": cf["num"]})
                p["recipe"] = recs
                used = []
                for u in p.get("used_to_craft_listed") or []:
                    nid = by_name.get(_norm(u["name"]))
                    if nid and u.get("num") is not None:
                        used.append({"id": nid, "num": u["num"]})
                p["used_in"] = used
            meta["primary_source"] = "wiki"
            meta["wiki_parsed"] = parsed
            print(f"  parsed {parsed} wiki clothing pages")

    if args.skip_fetch and not items:
        # reload previous
        items, meta = load_items_from_out(out)

    if not items:
        raise SystemExit("No items loaded.")

    compute_required(items)
    ov = resolve_overrides_source(getattr(args, "overrides", None))
    if ov:
        snap = out / "bundle" / "overrides.csv"
        load_and_apply_overrides(items, ov, snap, delay=delay)
        meta["overrides"] = {"source": ov, "snapshot": str(snap)}
    meta["item_count"] = len(items)
    write_datasets(out, items, meta)
    report = validate_sandals(items)
    print_validation(report)
    (out / "data" / "validation-sandals.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if args.wayback and not args.skip_fetch:
        fetch_wayback(out, delay, limit)


def _norm(s: str) -> str:
    s = (s or "").replace("_", " ").replace("·", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


def cmd_build_site(args: argparse.Namespace) -> None:
    out = Path(args.out).expanduser().resolve()
    items, meta = load_items_from_out(out)
    ov = resolve_overrides_source(getattr(args, "overrides", None))
    if ov:
        snap = out / "bundle" / "overrides.csv"
        load_and_apply_overrides(items, ov, snap, delay=float(getattr(args, "delay", 1.0) or 1.0))
        meta["overrides"] = {"source": ov, "snapshot": str(snap)}
    bundle = compact_bundle(items, meta)
    bdir = out / "bundle"
    bdir.mkdir(parents=True, exist_ok=True)
    bpath = bdir / "pikkis-info.min.json"
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    bpath.write_text(raw, encoding="utf-8")
    size = bpath.stat().st_size
    print(f"Compact bundle: {bpath}  {size:,} bytes  ({len(items)} items)")
    write_build_mode_prompt(bdir / "BUILD_MODE_PROMPT.md", "pikkis-info.min.json", size, len(items))
    print(f"Build Mode prompt: {bdir / 'BUILD_MODE_PROMPT.md'}")
    write_local_site(out, items, meta, os.path.relpath(bpath, out / "site"))
    print(f"Local preview: {out / 'site' / 'index.html'}")
    print("Phase II does not publish anything. Open the prompt and (if needed) host the JSON, then paste into Grok Build Mode on grok.me yourself.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scrape Love Nikki item/recipe data (Nikki Calc primary, wiki fallback) "
                    "and build a Pikki's Info bundle for Grok Build Mode."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--out", default="nikki_data", help="Output directory (default: ./nikki_data)")
        sp.add_argument("--delay", type=float, default=1.0, help="Seconds between HTTP requests (default: 1)")
        sp.add_argument("--limit", type=int, default=None, help="Max items (for tests). Seeds are always included.")
        sp.add_argument("--skip-fetch", action="store_true", help="Parse/build from files already in --out")
        sp.add_argument(
            "--overrides",
            default=None,
            help="Overrides CSV path or URL (Google Sheet export). "
                 "Falls back to the OVERRIDES_CSV_URL environment variable.",
        )

    sc = sub.add_parser("scrape", help="Fetch + parse + compute required counts")
    add_common(sc)
    sc.add_argument("--source", choices=["auto", "nikkicalc", "wiki"], default="auto",
                    help="auto: Nikki Calc, wiki if that fails")
    sc.add_argument("--also-wiki", action="store_true", help="Also fetch wiki pages (slower)")
    sc.add_argument("--wayback", action="store_true", help="Also list/download archived ln.nikkis.info wardrobe pages")
    sc.set_defaults(func=cmd_scrape)

    bs = sub.add_parser("build-site", help="Write compact JSON bundle, Build Mode prompt, and local HTML preview")
    add_common(bs)
    bs.set_defaults(func=cmd_build_site)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
