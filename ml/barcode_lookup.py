"""
Multi-Source High-Precision Barcode Lookup Engine
-------------------------------------------------
Queries global open barcode databases with candidate padding (EAN-13 / UPC-A):
  1. Open Food Facts      (Food, grocery)
  2. Open Beauty Facts    (Cosmetics, hygiene, soaps, personal care)
  3. Open Products Facts  (Household, electronics, toys)
  4. Open Library API     (Books, ISBN 978/979)
  5. UPC ItemDB          (General retail products)
  6. EAN Search          (Global GTIN/EAN products)

If online databases return a match, real product details are returned.
If NO database matches the barcode, the engine DOES NOT fill fake/synthetic names,
leaving the item name clean for the user to type while setting _found = False.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMEOUT = 4  # seconds per request for fast scanning


def _get_barcode_variants(barcode: str) -> list[str]:
    """
    Generate barcode variants (e.g. padding 12-digit UPC-A to 13-digit EAN-13 with '0',
    or stripping leading '0' from 13-digit EAN-13).
    """
    cleaned = "".join(c for c in str(barcode).strip() if c.isdigit())
    if not cleaned:
        return [str(barcode).strip()]

    candidates = [cleaned]
    if len(cleaned) == 12:
        candidates.append("0" + cleaned)
    elif len(cleaned) == 13 and cleaned.startswith("0"):
        candidates.append(cleaned[1:])
    return candidates


def _fetch_open_facts(barcode: str, domain: str = "openfoodfacts", source_name: str = "Open Food Facts") -> dict | None:
    url = f"https://world.{domain}.org/api/v2/product/{barcode}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AIInventoryManager/2.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if raw.get("status") != 1:
        return None

    p = raw.get("product", {})
    name = p.get("product_name_en") or p.get("product_name") or ""
    if not name:
        return None

    nutriments = p.get("nutriments", {})
    nutri_parts = []
    if nutriments:
        for k in ["energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g", "fiber_100g", "salt_100g"]:
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
        if any(u in str(quantity).lower() for u in ["ml", "l", "litre", "liter"]):
            volume = str(quantity)
        else:
            weight = str(quantity)

    image_url = (
        p.get("image_front_url") or
        p.get("image_url") or
        p.get("image_thumb_url") or ""
    )

    return {
        "source_db": source_name,
        "name": name.strip(),
        "brand": (p.get("brands") or "").strip(),
        "category": _first_category(categories),
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


def _first_category(cats: str) -> str:
    if not cats:
        return ""
    parts = [c.strip().title() for c in str(cats).split(",") if c.strip()]
    return parts[0] if parts else ""


def _guess_unit(weight: str, volume: str, quantity: str) -> str:
    if volume:
        return "ltr" if any(u in volume.lower() for u in ["l", "litre", "liter"]) else "ml"
    if weight:
        return "kg" if "kg" in weight.lower() else "g"
    return "pcs"


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
        req = urllib.request.Request(url, headers={"User-Agent": "AIInventoryManager/2.0", "Accept": "application/json"})
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


def _merge(base: dict, extra: dict) -> dict:
    """Fill empty fields in base from extra."""
    for key, val in extra.items():
        if not base.get(key) and val:
            base[key] = val
    return base


def _apply_ai_suggestions(data: dict) -> dict:
    """Fill remaining gaps using rule-based AI knowledge base."""
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
        "dabur":      {"category": "Personal Care",  "manufacturer": "Dabur India Ltd",  "country_of_origin": "India"},
        "himalaya":   {"category": "Personal Care",  "manufacturer": "The Himalaya Drug Company", "country_of_origin": "India"},
        "colgate":    {"category": "Personal Care",  "unit_label":   "pcs",      "manufacturer": "Colgate-Palmolive"},
        "gillette":   {"category": "Personal Care",  "warranty_info": "N/A",     "manufacturer": "Procter & Gamble"},
        "dettol":     {"category": "Health & Hygiene","manufacturer": "Reckitt Benckiser"},
        "surf excel": {"category": "Household",      "manufacturer": "Hindustan Unilever"},
        "maggi":      {"category": "Food & Grocery", "unit_label":   "pcs",      "manufacturer": "Nestlé India"},
        "tata":       {"category": "Food & Grocery", "manufacturer": "Tata Consumer Products", "country_of_origin": "India"},
        "haldiram":   {"category": "Snacks",         "manufacturer": "Haldiram Foods Pvt Ltd", "country_of_origin": "India"},
        "lay's":      {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "PepsiCo India"},
        "cadbury":    {"category": "Snacks",         "manufacturer": "Cadbury India"},
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


def _cache_get(barcode: str) -> dict | None:
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT data, source FROM barcode_cache WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
        if row and row[1] != "AI Model" and row[1] != "AI Prediction":  # Ignore old synthetic AI cache
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
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO barcode_cache (barcode, data, source, cached_at) VALUES (?, ?, ?, ?)",
            (barcode, json.dumps(data), source, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def lookup_barcode(barcode: str, use_cache: bool = True, force_ai: bool = False) -> dict:
    """
    Look up a barcode across global product databases (Open Food Facts, Open Beauty Facts,
    Open Products Facts, Open Library, UPC ItemDB, EAN Search).

    Returns real product details if found. If NOT found, leaves product name clean/empty
    so the user can type the real name without synthetic fake overwrites!
    """
    barcode = str(barcode).strip()
    if not barcode:
        return {"_found": False, "name": "", "code_value": "", "barcode_raw": ""}

    # 1. Check local cache first
    if use_cache and not force_ai:
        cached = _cache_get(barcode)
        if cached and cached.get("name"):
            cached["_from_cache"] = True
            cached["_found"] = True
            return cached

    result = {}
    variants = _get_barcode_variants(barcode)

    # 2. Open Food Facts
    for v in variants:
        res = _fetch_open_facts(v, domain="openfoodfacts", source_name="Open Food Facts")
        if res and res.get("name"):
            result = res
            break

    # 3. Open Beauty Facts (for cosmetics, soaps, shampoos, skincare)
    if not result or not result.get("name"):
        for v in variants:
            res = _fetch_open_facts(v, domain="openbeautyfacts", source_name="Open Beauty Facts")
            if res and res.get("name"):
                result = res
                break

    # 4. Open Products Facts (for electronics, household, toys)
    if not result or not result.get("name"):
        for v in variants:
            res = _fetch_open_facts(v, domain="openproductsfacts", source_name="Open Products Facts")
            if res and res.get("name"):
                result = res
                break

    # 5. Open Library API (for ISBN books)
    if not result or not result.get("name"):
        for v in variants:
            res = _fetch_open_library(v)
            if res and res.get("name"):
                result = res
                break

    # 6. UPC ItemDB
    if not result or not result.get("name"):
        for v in variants:
            res = _fetch_upc_itemdb(v)
            if res and res.get("name"):
                result = res
                break

    # 7. EAN Search
    if not result or not result.get("name"):
        for v in variants:
            res = _fetch_ean_search(v)
            if res and res.get("name"):
                result = res
                break

    # 8. If found: apply brand rules and cache
    if result and result.get("name"):
        result = _apply_ai_suggestions(result)
        result["barcode_raw"] = barcode
        result["code_value"] = barcode
        if not result.get("unit_price"):
            result["unit_price"] = 0.0
        if not result.get("cost_price"):
            result["cost_price"] = 0.0
        if not result.get("stock_qty"):
            result["stock_qty"] = 10
        if not result.get("reorder_level"):
            result["reorder_level"] = 5
        _cache_set(barcode, result, result.get("source_db", "Open DB"))
        result["_found"] = True
        return result

    # 9. ONLY if force_ai is requested by user button click, run AI synthetic predictor
    if force_ai:
        from ml.barcode_ai import analyze_barcode as _ai
        ai_res = _ai(barcode)
        if ai_res and ai_res.get("name"):
            ai_res["source_db"] = "AI Prediction"
            ai_res["barcode_raw"] = barcode
            ai_res["_found"] = True
            return ai_res

    # 10. UNRECOGNIZED BARCODE: Return clean structure without fake synthetic name!
    return {
        "_found": False,
        "source_db": "Not Found",
        "name": "",  # Empty so user types real name
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
