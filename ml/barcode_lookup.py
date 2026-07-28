"""
Multi-Source Barcode Lookup
---------------------------
Queries multiple free barcode databases in order:
  1. Open Food Facts (no API key required)
  2. UPC ItemDB     (free tier, no key required)
  3. OpenGTINdb     (free, no key required)
  4. AI fallback    (always works, fully offline)

Results are merged and cached in SQLite for offline use.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import os
import sys

# Ensure parent package is importable even when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMEOUT = 5  # seconds per request

# ──────────────────────────────────────────────────────────────────────────────
# Source 1 — Open Food Facts
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_open_food_facts(barcode: str) -> dict | None:
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LedgerInventory/2.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if raw.get("status") != 1:
        return None

    p = raw.get("product", {})
    nutriments = p.get("nutriments", {})
    nutri_text = ""
    if nutriments:
        parts = []
        for key in ["energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g", "fiber_100g", "salt_100g"]:
            if key in nutriments:
                label = key.replace("_100g", "").replace("-", " ").title()
                parts.append(f"{label}: {nutriments[key]}")
        nutri_text = " | ".join(parts)

    # Ingredients
    ingredients = p.get("ingredients_text_en") or p.get("ingredients_text") or ""

    # Nutriscore / categories
    categories = p.get("categories_en") or p.get("categories") or ""
    if isinstance(categories, list):
        categories = ", ".join(categories[:3])
    elif "," in str(categories):
        categories = ", ".join(str(categories).split(",")[:3])

    # Quantity / weight
    quantity = p.get("quantity") or ""
    weight = ""
    volume = ""
    if quantity:
        if any(u in quantity.lower() for u in ["ml", "l", "litre", "liter"]):
            volume = quantity
        else:
            weight = quantity

    # Image
    image_url = (
        p.get("image_front_url") or
        p.get("image_url") or
        p.get("image_thumb_url") or ""
    )

    return {
        "source_db": "Open Food Facts",
        "name": p.get("product_name_en") or p.get("product_name") or "",
        "brand": p.get("brands") or "",
        "category": _first_category(categories),
        "sub_category": categories,
        "description": p.get("generic_name_en") or p.get("generic_name") or "",
        "image_url": image_url,
        "barcode_raw": barcode,
        "weight": weight,
        "volume": volume,
        "manufacturer": p.get("manufacturing_places") or "",
        "country_of_origin": p.get("countries_en") or p.get("countries") or "",
        "ingredients": ingredients[:1000] if ingredients else "",
        "nutritional_info": nutri_text,
        "unit_label": _guess_unit(weight, volume, quantity),
    }


def _first_category(cats: str) -> str:
    if not cats:
        return ""
    parts = [c.strip().title() for c in cats.split(",") if c.strip()]
    return parts[0] if parts else ""


def _guess_unit(weight: str, volume: str, quantity: str) -> str:
    if volume:
        return "ltr" if any(u in volume.lower() for u in ["l", "litre", "liter"]) else "ml"
    if weight:
        return "kg" if "kg" in weight.lower() else "g"
    return "pcs"


# ──────────────────────────────────────────────────────────────────────────────
# Source 2 — UPC ItemDB (free trial, no key)
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_upc_itemdb(barcode: str) -> dict | None:
    url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LedgerInventory/2.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    items = raw.get("items", [])
    if not items:
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


# ──────────────────────────────────────────────────────────────────────────────
# Source 3 — OpenGTINdb (free, no key)
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_opengtindb(barcode: str) -> dict | None:
    url = f"https://www.digit-eyes.com/gtin/aHR0cHM6Ly93d3cuZGlnaXQtZXllcy5jb20vY2dpLWJpbi9zZWFyY2guY2dpP3VwY0NvZGU9{barcode}"
    # Digit-eyes requires key; use Open EAN API instead
    url = f"https://ean-search.org/perl/api.pl?q={barcode}&lang=1&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LedgerInventory/2.0"})
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


# ──────────────────────────────────────────────────────────────────────────────
# Merge helper
# ──────────────────────────────────────────────────────────────────────────────

def _merge(base: dict, extra: dict) -> dict:
    """Fill empty fields in base from extra."""
    for key, val in extra.items():
        if not base.get(key) and val:
            base[key] = val
    return base


# ──────────────────────────────────────────────────────────────────────────────
# AI autocomplete suggestions
# ──────────────────────────────────────────────────────────────────────────────

def _apply_ai_suggestions(data: dict) -> dict:
    """Fill remaining gaps using rule-based AI knowledge base."""
    brand = (data.get("brand") or "").lower()
    name  = (data.get("name") or "").lower()
    cat   = (data.get("category") or "").lower()

    BRAND_RULES = {
        "samsung":    {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Samsung Electronics"},
        "apple":      {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Apple Inc."},
        "sony":       {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Sony Corporation"},
        "lg":         {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "LG Electronics"},
        "panasonic":  {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Panasonic Corporation"},
        "nokia":      {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Nokia Corporation"},
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
        "johnson":    {"category": "Personal Care",  "manufacturer": "Johnson & Johnson"},
        "dettol":     {"category": "Health & Hygiene","manufacturer": "Reckitt Benckiser"},
        "lifebuoy":   {"category": "Personal Care",  "manufacturer": "Unilever"},
        "surf excel": {"category": "Household",      "manufacturer": "Hindustan Unilever"},
        "harpic":     {"category": "Household",      "manufacturer": "Reckitt Benckiser"},
        "maggi":      {"category": "Food & Grocery", "unit_label":   "pcs",      "manufacturer": "Nestlé India"},
        "tata":       {"category": "Food & Grocery", "manufacturer": "Tata Consumer Products", "country_of_origin": "India"},
        "haldiram":   {"category": "Snacks",         "manufacturer": "Haldiram Foods Pvt Ltd", "country_of_origin": "India"},
        "lay's":      {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "PepsiCo India"},
        "kurkure":    {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "PepsiCo India"},
        "5 star":     {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "Cadbury India"},
        "kitkat":     {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "Nestlé India"},
        "cadbury":    {"category": "Snacks",         "manufacturer": "Cadbury India"},
        "lindt":      {"category": "Snacks",         "manufacturer": "Lindt & Sprüngli"},
        "redbull":    {"category": "Beverages",      "unit_label":   "can",      "manufacturer": "Red Bull GmbH"},
    }

    NAME_RULES = {
        "water":      {"category": "Beverages", "unit_label": "ltr"},
        "juice":      {"category": "Beverages", "unit_label": "ltr"},
        "tea":        {"category": "Beverages", "unit_label": "g"},
        "coffee":     {"category": "Beverages", "unit_label": "g"},
        "milk":       {"category": "Dairy",     "unit_label": "ltr"},
        "cheese":     {"category": "Dairy",     "unit_label": "kg"},
        "butter":     {"category": "Dairy",     "unit_label": "kg"},
        "curd":       {"category": "Dairy",     "unit_label": "kg"},
        "yogurt":     {"category": "Dairy",     "unit_label": "kg"},
        "bread":      {"category": "Bakery",    "unit_label": "pcs"},
        "biscuit":    {"category": "Snacks",    "unit_label": "pcs"},
        "cookie":     {"category": "Snacks",    "unit_label": "pcs"},
        "chips":      {"category": "Snacks",    "unit_label": "pcs"},
        "noodle":     {"category": "Food & Grocery", "unit_label": "pcs"},
        "rice":       {"category": "Food & Grocery", "unit_label": "kg"},
        "atta":       {"category": "Food & Grocery", "unit_label": "kg"},
        "flour":      {"category": "Food & Grocery", "unit_label": "kg"},
        "oil":        {"category": "Food & Grocery", "unit_label": "ltr"},
        "shampoo":    {"category": "Personal Care", "unit_label": "ml"},
        "soap":       {"category": "Personal Care", "unit_label": "pcs"},
        "toothpaste": {"category": "Personal Care", "unit_label": "pcs"},
        "detergent":  {"category": "Household",  "unit_label": "kg"},
        "phone":      {"category": "Electronics", "warranty_info": "1 Year"},
        "charger":    {"category": "Electronics", "warranty_info": "6 Months"},
        "battery":    {"category": "Electronics", "unit_label": "pcs"},
        "tablet":     {"category": "Electronics", "warranty_info": "1 Year"},
        "laptop":     {"category": "Electronics", "warranty_info": "1 Year"},
        "medicine":   {"category": "Pharmacy",   "unit_label": "pcs"},
        "capsule":    {"category": "Pharmacy",   "unit_label": "pcs"},
        "syrup":      {"category": "Pharmacy",   "unit_label": "ml"},
    }

    # Apply brand rules
    for b_key, rules in BRAND_RULES.items():
        if b_key in brand:
            for field, val in rules.items():
                if not data.get(field):
                    data[field] = val
            break

    # Apply name rules
    for n_key, rules in NAME_RULES.items():
        if n_key in name:
            for field, val in rules.items():
                if not data.get(field):
                    data[field] = val
            break

    # Final defaults
    if not data.get("unit_label"):
        data["unit_label"] = "pcs"
    if not data.get("category"):
        data["category"] = "General"

    return data


# ──────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cache_get(barcode: str) -> dict | None:
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT data FROM barcode_cache WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
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


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def lookup_barcode(barcode: str, use_cache: bool = True) -> dict:
    """
    Look up a barcode across multiple databases. Returns a normalized product dict.
    Always returns something (falls back to AI model if all APIs fail).
    """
    barcode = str(barcode).strip()

    # 1. Check local cache first (offline-friendly)
    if use_cache:
        cached = _cache_get(barcode)
        if cached:
            cached["_from_cache"] = True
            return cached

    result = {}

    # 2. Open Food Facts
    off = _fetch_open_food_facts(barcode)
    if off and off.get("name"):
        result = off

    # 3. UPC ItemDB (merge into result)
    upc = _fetch_upc_itemdb(barcode)
    if upc and upc.get("name"):
        if not result:
            result = upc
        else:
            result = _merge(result, upc)

    # 4. EAN Search (merge into result)
    if not result or not result.get("name"):
        ean = _fetch_opengtindb(barcode)
        if ean and ean.get("name"):
            if not result:
                result = ean
            else:
                result = _merge(result, ean)

    # 5. AI fallback — always runs to fill any remaining gaps
    from ml.barcode_ai import analyze_barcode as _ai
    ai_result = _ai(barcode)
    if ai_result:
        if not result:
            result = ai_result
            result["source_db"] = "AI Model"
        else:
            # Use AI only to fill blanks
            for field in ["category", "unit_label", "unit_price", "cost_price", "reorder_level"]:
                if not result.get(field) and ai_result.get(field):
                    result[field] = ai_result[field]
            if not result.get("unit_price") or result.get("unit_price", 0) == 0:
                result["unit_price"] = ai_result.get("unit_price", 0)
            if not result.get("cost_price") or result.get("cost_price", 0) == 0:
                result["cost_price"] = ai_result.get("cost_price", 0)

    # 6. AI auto-complete suggestions for missing fields
    result = _apply_ai_suggestions(result)

    # Ensure barcode is always set
    result["barcode_raw"] = barcode
    result["code_value"] = barcode

    # Normalize pricing
    if not result.get("unit_price"):
        result["unit_price"] = 0.0
    if not result.get("cost_price"):
        result["cost_price"] = 0.0
    if not result.get("mrp"):
        result["mrp"] = result.get("unit_price", 0.0)
    if not result.get("stock_qty"):
        result["stock_qty"] = 0
    if not result.get("reorder_level"):
        result["reorder_level"] = 5

    # Cache result for offline use
    if result.get("name"):
        _cache_set(barcode, result, result.get("source_db", "unknown"))
    
    result["_found"] = bool(result.get("name"))
    return result
