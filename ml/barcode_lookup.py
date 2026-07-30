"""
Multi-Source High-Precision Barcode Lookup Engine  (v5 — Parallel + Accurate)
------------------------------------------------------------------------------
Queries global open barcode databases with concurrent requests for speed:
  1. Open Food Facts      (Food, grocery)
  2. Open Beauty Facts    (Cosmetics, hygiene, soaps, personal care)
  3. Open Products Facts  (Household, electronics, toys)
  4. Open Library API     (Books, ISBN 978/979)
  5. UPC ItemDB          (General retail products)
  6. go-upc.com          (Additional general retail fallback)
  7. EAN Search          (Global GTIN/EAN products)

Key improvements over v4:
- Open Facts DBs 1–3 queried in PARALLEL (ThreadPoolExecutor) → 3× faster
- Timeout raised 4 s → 7 s (catches slow responders)
- Cache hits now always return _found: True
- Expanded barcode variant generation (strips non-digits, handles EAN-8, UPC-E,
  leading-zero padding in both directions)
- go-upc.com added as reliable fallback
- conn.close() fixed in caller (app.py)

If NO database matches, returns _found=False and an empty name so the user
can type the real product name without synthetic overwrite.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMEOUT = 7          # seconds per individual HTTP request
PARALLEL_TIMEOUT = 8  # max seconds to wait for the parallel Open-Facts batch


# ---------------------------------------------------------------------------
# Barcode variant generator
# ---------------------------------------------------------------------------

def _get_barcode_variants(barcode: str) -> list[str]:
    """
    Generate all plausible barcode variants to maximise lookup hit rate.

    Strategy:
    - Strip non-digit characters first (handles CODE-128 with hyphens/spaces).
    - Keep original (might be alphanumeric CODE-128).
    - For 12-digit (UPC-A):  try with leading '0' → 13-digit EAN-13.
    - For 13-digit (EAN-13): try stripping leading '0' → 12-digit UPC-A.
    - For 8-digit  (EAN-8):  keep as-is (separate spec — no padding needed).
    - For 7-digit  (UPC-E):  try expanded form (60xxxxx0 pattern).
    - Also include the digit-only version even if original had chars.
    """
    original = str(barcode).strip()
    digits_only = "".join(c for c in original if c.isdigit())

    candidates: list[str] = []

    # Always include the original first (handles CODE-128, QR etc.)
    candidates.append(original)

    if digits_only and digits_only != original:
        candidates.append(digits_only)

    if digits_only:
        n = len(digits_only)
        if n == 12:
            # UPC-A → pad to EAN-13
            candidates.append("0" + digits_only)
        elif n == 13:
            if digits_only.startswith("0"):
                # EAN-13 starting with 0 → strip leading zero → UPC-A
                candidates.append(digits_only[1:])
        elif n == 7:
            # UPC-E 7-digit (without check digit) — try common 0-prefix expansion
            candidates.append("0" + digits_only)
        elif n == 11:
            # Sometimes barcode readers drop the check digit on EAN-13
            candidates.append(digits_only + "0")
            candidates.append("0" + digits_only + "0")

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# Individual source fetchers
# ---------------------------------------------------------------------------

def _fetch_open_facts(barcode: str, domain: str = "openfoodfacts",
                      source_name: str = "Open Food Facts") -> dict | None:
    url = f"https://world.{domain}.org/api/v2/product/{barcode}.json"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AIInventoryManager/2.0 (contact@aiinventory.local)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if raw.get("status") != 1:
        return None

    p = raw.get("product", {})
    name = (p.get("product_name_en") or p.get("product_name") or
            p.get("abbreviated_product_name") or "").strip()
    if not name:
        return None

    nutriments = p.get("nutriments", {})
    nutri_parts = []
    for k in ["energy-kcal_100g", "proteins_100g", "carbohydrates_100g",
               "fat_100g", "fiber_100g", "salt_100g"]:
        if k in nutriments:
            lbl = k.replace("_100g", "").replace("-", " ").title()
            nutri_parts.append(f"{lbl}: {nutriments[k]}")
    nutri_text = " | ".join(nutri_parts)

    ingredients = p.get("ingredients_text_en") or p.get("ingredients_text") or ""
    categories = p.get("categories_en") or p.get("categories") or ""
    if isinstance(categories, list):
        categories = ", ".join(str(c) for c in categories[:3])
    elif "," in str(categories):
        categories = ", ".join(str(categories).split(",")[:3])

    quantity = p.get("quantity") or ""
    weight, volume = "", ""
    if quantity:
        ql = str(quantity).lower()
        if any(u in ql for u in ["ml", "l", "litre", "liter", "fl oz"]):
            volume = str(quantity)
        else:
            weight = str(quantity)

    image_url = (p.get("image_front_url") or p.get("image_url") or
                 p.get("image_thumb_url") or "")

    return {
        "source_db": source_name,
        "name": name,
        "brand": (p.get("brands") or "").split(",")[0].strip(),
        "category": _first_category(str(categories)),
        "sub_category": str(categories),
        "description": (p.get("generic_name_en") or p.get("generic_name") or "").strip(),
        "image_url": image_url,
        "barcode_raw": barcode,
        "weight": weight,
        "volume": volume,
        "manufacturer": (p.get("manufacturing_places") or "").strip(),
        "country_of_origin": (p.get("countries_en") or p.get("countries") or "").strip(),
        "ingredients": ingredients[:1000] if ingredients else "",
        "nutritional_info": nutri_text,
        "unit_label": _guess_unit(weight, volume, quantity),
    }


def _fetch_open_library(barcode: str) -> dict | None:
    """Fetch book information for ISBN barcodes starting with 978 or 979."""
    if not (barcode.startswith("978") or barcode.startswith("979")):
        return None
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{barcode}&jscmd=data&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AIInventoryManager/2.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    book_data = raw.get(f"ISBN:{barcode}") or (list(raw.values())[0] if raw else None)
    if not book_data or not book_data.get("title"):
        return None

    authors = book_data.get("authors", [])
    author_name = authors[0].get("name") if authors else ""
    cover = book_data.get("cover", {})
    image_url = cover.get("large") or cover.get("medium") or cover.get("small") or ""

    return {
        "source_db": "Open Library (Book)",
        "name": book_data.get("title"),
        "brand": author_name,
        "category": "Books & Stationery",
        "sub_category": "Books",
        "description": f"Book by {author_name}" if author_name else "Book",
        "image_url": image_url,
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_upc_itemdb(barcode: str) -> dict | None:
    url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AIInventoryManager/2.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    items = raw.get("items", [])
    if not items or not items[0].get("title"):
        return None

    item = items[0]
    offers = item.get("offers", [])
    price = 0.0
    if offers:
        try:
            price = float(offers[0].get("price", 0))
        except Exception:
            pass

    images = item.get("images", [])
    image_url = images[0] if images else ""

    return {
        "source_db": "UPC ItemDB",
        "name": item.get("title") or "",
        "brand": item.get("brand") or "",
        "category": item.get("category") or "",
        "sub_category": "",
        "description": item.get("description") or "",
        "image_url": image_url,
        "barcode_raw": barcode,
        "weight": item.get("weight") or "",
        "dimensions": item.get("dimension") or "",
        "color": item.get("color") or "",
        "size_label": item.get("size") or "",
        "unit_label": "pcs",
        "unit_price": price,
    }


def _fetch_go_upc(barcode: str) -> dict | None:
    """
    go-upc.com — free public barcode lookup (no API key required for basic use).
    Returns structured JSON similar to UPC ItemDB.
    """
    url = f"https://go-upc.com/api/v1/code/{barcode}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AIInventoryManager/2.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    product = raw.get("product") or {}
    name = (product.get("name") or "").strip()
    if not name:
        return None

    image_url = product.get("imageUrl") or ""
    brand = (product.get("brand") or "").strip()
    category = (product.get("category") or "").strip()
    description = (product.get("description") or "").strip()

    return {
        "source_db": "Go-UPC",
        "name": name,
        "brand": brand,
        "category": category,
        "description": description,
        "image_url": image_url,
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_ean_search(barcode: str) -> dict | None:
    url = f"https://ean-search.org/perl/api.pl?q={barcode}&lang=1&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AIInventoryManager/2.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not raw or not isinstance(raw, list) or not raw[0].get("name"):
        return None

    item = raw[0]
    return {
        "source_db": "EAN Search",
        "name": item.get("name") or "",
        "brand": "",
        "category": item.get("categoryName") or "",
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_category(cats: str) -> str:
    if not cats:
        return ""
    parts = [c.strip().title() for c in str(cats).split(",") if c.strip()]
    return parts[0] if parts else ""


def _guess_unit(weight: str, volume: str, quantity: str) -> str:
    if volume:
        vl = volume.lower()
        if any(u in vl for u in ["l ", "litre", "liter", " l"]) and "ml" not in vl:
            return "ltr"
        return "ml"
    if weight:
        wl = weight.lower()
        if "kg" in wl:
            return "kg"
        return "g"
    return "pcs"


def _merge(base: dict, extra: dict) -> dict:
    """Fill empty fields in base from extra."""
    for key, val in extra.items():
        if not base.get(key) and val:
            base[key] = val
    return base


def _apply_ai_suggestions(data: dict) -> dict:
    """Fill remaining gaps using rule-based brand knowledge base."""
    brand = (data.get("brand") or "").lower()

    BRAND_RULES = {
        "samsung":    {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Samsung Electronics"},
        "apple":      {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Apple Inc."},
        "sony":       {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Sony Corporation"},
        "lg":         {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "LG Electronics"},
        "panasonic":  {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Panasonic Corporation"},
        "coca-cola":  {"category": "Beverages",      "unit_label":   "bottle",   "manufacturer": "The Coca-Cola Company"},
        "pepsi":      {"category": "Beverages",      "unit_label":   "bottle",   "manufacturer": "PepsiCo Inc."},
        "nestle":     {"category": "Food & Grocery", "manufacturer": "Nestlé S.A."},
        "amul":       {"category": "Dairy",          "manufacturer": "GCMMF",    "country_of_origin": "India"},
        "britannia":  {"category": "Snacks",         "manufacturer": "Britannia Industries", "country_of_origin": "India"},
        "parle":      {"category": "Snacks",         "manufacturer": "Parle Products Pvt Ltd", "country_of_origin": "India"},
        "dabur":      {"category": "Personal Care",  "manufacturer": "Dabur India Ltd", "country_of_origin": "India"},
        "himalaya":   {"category": "Personal Care",  "manufacturer": "The Himalaya Drug Company", "country_of_origin": "India"},
        "colgate":    {"category": "Personal Care",  "unit_label":   "pcs",      "manufacturer": "Colgate-Palmolive"},
        "gillette":   {"category": "Personal Care",  "warranty_info": "N/A",     "manufacturer": "Procter & Gamble"},
        "dettol":     {"category": "Health & Hygiene", "manufacturer": "Reckitt Benckiser"},
        "surf excel": {"category": "Household",      "manufacturer": "Hindustan Unilever"},
        "maggi":      {"category": "Food & Grocery", "unit_label":   "pcs",      "manufacturer": "Nestlé India"},
        "tata":       {"category": "Food & Grocery", "manufacturer": "Tata Consumer Products", "country_of_origin": "India"},
        "haldiram":   {"category": "Snacks",         "manufacturer": "Haldiram Foods Pvt Ltd", "country_of_origin": "India"},
        "lay's":      {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "PepsiCo India"},
        "cadbury":    {"category": "Snacks",         "manufacturer": "Cadbury India"},
        "unilever":   {"manufacturer": "Hindustan Unilever"},
        "itc":        {"country_of_origin": "India", "manufacturer": "ITC Limited"},
        "godrej":     {"country_of_origin": "India", "manufacturer": "Godrej Consumer Products"},
        "wipro":      {"country_of_origin": "India"},
        "patanjali":  {"country_of_origin": "India", "manufacturer": "Patanjali Ayurved"},
        "marico":     {"country_of_origin": "India", "manufacturer": "Marico Limited"},
        "emami":      {"country_of_origin": "India", "manufacturer": "Emami Limited"},
        "pidilite":   {"country_of_origin": "India", "manufacturer": "Pidilite Industries"},
    }

    for b_key, rules in BRAND_RULES.items():
        if b_key in brand:
            for field, val in rules.items():
                if not data.get(field):
                    data[field] = val
            break

    if not data.get("unit_label"):
        data["unit_label"] = "pcs"
    if not data.get("category"):
        data["category"] = "General"

    return data


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_get(barcode: str) -> dict | None:
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "store.db")
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT data, source FROM barcode_cache WHERE barcode = ?", (barcode,)
        ).fetchone()
        conn.close()
        if row:
            # Only reject old synthetic AI-generated cache entries
            if row[1] in ("AI Model", "AI Prediction"):
                return None
            d = json.loads(row[0])
            if d.get("name"):
                return d
    except Exception:
        pass
    return None


def _cache_set(barcode: str, data: dict, source: str) -> None:
    try:
        import sqlite3
        from datetime import datetime
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "store.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO barcode_cache (barcode, data, source, cached_at) VALUES (?, ?, ?, ?)",
            (barcode, json.dumps(data), source, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Parallel Open-Facts fetcher (runs all 3 in parallel)
# ---------------------------------------------------------------------------

def _fetch_open_facts_parallel(variants: list[str]) -> dict | None:
    """
    Query Open Food Facts, Open Beauty Facts, and Open Products Facts
    concurrently across all barcode variants.
    Returns the first successful result or None.
    """
    tasks = []
    for domain, source_name in [
        ("openfoodfacts",    "Open Food Facts"),
        ("openbeautyfacts",  "Open Beauty Facts"),
        ("openproductsfacts","Open Products Facts"),
    ]:
        for v in variants:
            tasks.append((v, domain, source_name))

    with ThreadPoolExecutor(max_workers=min(len(tasks), 9)) as executor:
        futures = {
            executor.submit(_fetch_open_facts, v, domain, sname): (v, domain, sname)
            for v, domain, sname in tasks
        }
        # Collect results as they complete; return first non-None
        try:
            for future in as_completed(futures, timeout=PARALLEL_TIMEOUT):
                try:
                    res = future.result()
                    if res and res.get("name"):
                        # Cancel remaining futures (best-effort)
                        for f in futures:
                            f.cancel()
                        return res
                except Exception:
                    continue
        except FuturesTimeout:
            pass

    return None


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def lookup_barcode(barcode: str, use_cache: bool = True, force_ai: bool = False) -> dict:
    """
    Look up a barcode across global product databases.

    Returns real product details if found. If NOT found, returns _found=False
    with an empty name so the user can type the real name without synthetic
    overwrites.
    """
    barcode = str(barcode).strip()
    if not barcode:
        return {"_found": False, "name": "", "code_value": "", "barcode_raw": ""}

    # 1. Check local cache first
    if use_cache and not force_ai:
        cached = _cache_get(barcode)
        if cached and cached.get("name"):
            cached["_from_cache"] = True
            cached["_found"] = True          # ← was missing in v4!
            return cached

    variants = _get_barcode_variants(barcode)
    result: dict = {}

    # 2. Open Food/Beauty/Products Facts — queried IN PARALLEL for speed
    res = _fetch_open_facts_parallel(variants)
    if res and res.get("name"):
        result = res

    # 3. Open Library (books) — only needed if nothing found yet
    if not result.get("name"):
        for v in variants:
            res = _fetch_open_library(v)
            if res and res.get("name"):
                result = res
                break

    # 4. UPC ItemDB
    if not result.get("name"):
        for v in variants:
            res = _fetch_upc_itemdb(v)
            if res and res.get("name"):
                result = res
                break

    # 5. go-upc.com (free, no API key)
    if not result.get("name"):
        for v in variants:
            res = _fetch_go_upc(v)
            if res and res.get("name"):
                result = res
                break

    # 6. EAN Search
    if not result.get("name"):
        for v in variants:
            res = _fetch_ean_search(v)
            if res and res.get("name"):
                result = res
                break

    # 7. Found: apply brand rules, normalise, cache
    if result.get("name"):
        result = _apply_ai_suggestions(result)
        result["barcode_raw"] = barcode
        result["code_value"] = barcode
        result.setdefault("unit_price",     0.0)
        result.setdefault("cost_price",     0.0)
        result.setdefault("stock_qty",      10)
        result.setdefault("reorder_level",  5)
        _cache_set(barcode, result, result.get("source_db", "Open DB"))
        result["_found"] = True
        return result

    # 8. Force AI synthetic prediction (only when explicitly requested)
    if force_ai:
        from ml.barcode_ai import analyze_barcode as _ai
        ai_res = _ai(barcode)
        if ai_res and ai_res.get("name"):
            ai_res["source_db"] = "AI Prediction"
            ai_res["barcode_raw"] = barcode
            ai_res["_found"] = True
            return ai_res

    # 9. Not found — return clean empty structure
    return {
        "_found": False,
        "source_db": "Not Found",
        "name": "",
        "brand": "",
        "category": "General",
        "unit_label": "pcs",
        "stock_qty": 10,
        "reorder_level": 5,
        "unit_price": 0.0,
        "cost_price": 0.0,
        "barcode_raw": barcode,
        "code_value": barcode,
    }
