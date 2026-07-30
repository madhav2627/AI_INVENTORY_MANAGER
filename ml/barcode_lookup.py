"""
Multi-Source High-Precision Barcode Lookup Engine  (v6 -- Full Parallel + Indian DB)
-------------------------------------------------------------------------------------
Queries global AND Indian barcode databases concurrently for maximum hit rate:
  1. Open Food Facts       (Food, grocery)
  2. Open Beauty Facts     (Cosmetics, hygiene, soaps, personal care)
  3. Open Products Facts   (Household, electronics, toys)
  4. Open Library API      (Books, ISBN 978/979)
  5. UPC ItemDB            (General retail products)
  6. go-upc.com            (General retail fallback)
  7. EAN Search            (Global GTIN/EAN)
  8. barcodelookup.com     (Scrape -- largest free DB, great Indian coverage)
  9. barcodesdb.com        (Community barcode registry)
 10. GS1-India prefix KB   (Offline instant brand/manufacturer hints for 890... barcodes)

v6 improvements over v5:
- ALL sources queried in FULL parallel (not just Open-Facts) for fastest first-hit
- Per-variant caching: any cached variant returns instantly on retry
- Larger variant set + automatic check-digit correction for near-miss scans
- barcodelookup.com scrape (biggest free barcode DB -- good Indian retail coverage)
- GS1-India prefix (890...) offline knowledge base: instant brand hints
- User-Agent rotation to reduce 429 rate-limit rejections
- Each source has its own timeout; first valid winner returned immediately

If NO database matches, returns _found=False with empty name (plus brand hints
for Indian products) so the user can fill details without synthetic overwrites.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import os
import re
import sys
import random
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMEOUT = 8           # seconds per individual HTTP request
FULL_PARALLEL_TIMEOUT = 10  # overall parallel batch timeout

# ---------------------------------------------------------------------------
# User-Agent pool (rotate to reduce rate-limit rejections)
# ---------------------------------------------------------------------------

_UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17 Mobile/15E148 Safari/604.1",
    "AIInventoryManager/6.0 (contact@aiinventory.local)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

def _ua() -> str:
    return random.choice(_UA_POOL)


# ---------------------------------------------------------------------------
# GS1-India prefix knowledge base (offline, instant)
# ---------------------------------------------------------------------------
# Indian EAN-13 barcodes begin with 890. For common manufacturers we can
# immediately return brand/category hints even without a DB hit.

_INDIA_PREFIX_BRANDS = {
    "8901030": ("Hindustan Unilever", "Hindustan Unilever Ltd", "Personal Care"),
    "8901063": ("P&G India", "Procter & Gamble Hygiene & Health Care", "Personal Care"),
    "8901396": ("Nestle India", "Nestle India Ltd", "Food & Grocery"),
    "8901719": ("ITC", "ITC Limited", "Food & Grocery"),
    "8902102": ("Britannia", "Britannia Industries Ltd", "Snacks"),
    "8906002": ("Amul", "GCMMF (Amul)", "Dairy"),
    "8901058": ("Parle", "Parle Products Pvt Ltd", "Snacks"),
    "8901764": ("Dabur", "Dabur India Ltd", "Personal Care"),
    "8901234": ("Colgate India", "Colgate-Palmolive (India) Ltd", "Personal Care"),
    "8902519": ("Patanjali", "Patanjali Ayurved Ltd", "Food & Grocery"),
    "8901612": ("Marico", "Marico Limited", "Personal Care"),
    "8901072": ("Tata Consumer", "Tata Consumer Products Ltd", "Food & Grocery"),
    "8901042": ("Cadbury India", "Mondelez India Foods", "Snacks"),
    "8901777": ("Godrej Consumer", "Godrej Consumer Products Ltd", "Personal Care"),
    "8906025": ("Haldirams", "Haldiram Foods Pvt Ltd", "Snacks"),
    "8901893": ("Emami", "Emami Limited", "Personal Care"),
    "8901526": ("Himalaya", "The Himalaya Drug Company", "Personal Care"),
    "8901629": ("GlaxoSmithKline", "GSK Consumer Healthcare India", "Health & Hygiene"),
    "8901463": ("Johnson & Johnson India", "Johnson & Johnson Ltd", "Personal Care"),
}


def _indian_prefix_hint(barcode: str) -> dict | None:
    """Return brand/manufacturer hints for GS1-India (890...) barcodes."""
    if not barcode.startswith("890") or len(barcode) < 7:
        return None
    for prefix, (brand, mfr, cat) in _INDIA_PREFIX_BRANDS.items():
        if barcode.startswith(prefix):
            return {"brand": brand, "manufacturer": mfr, "category": cat,
                    "country_of_origin": "India"}
    return {"country_of_origin": "India"}


# ---------------------------------------------------------------------------
# Barcode variant generator
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Barcode variant generator  (improved in v6)
# ---------------------------------------------------------------------------

def _ean_check_digit(body: str, total_len: int) -> int:
    """Compute the expected EAN/UPC check digit given the barcode body (all chars except last)."""
    weights = [3 if (total_len - 1 - i) % 2 == 0 else 1 for i in range(len(body))]
    s = sum(int(d) * w for d, w in zip(body, weights))
    return (10 - (s % 10)) % 10


def _fix_check_digit(barcode: str) -> str | None:
    """Return barcode with the correct last digit if it was misread, else None."""
    if not barcode.isdigit():
        return None
    body = barcode[:-1]
    correct = _ean_check_digit(body, len(barcode))
    if correct == int(barcode[-1]):
        return None  # already correct
    return body + str(correct)


def _get_barcode_variants(barcode: str) -> list[str]:
    """
    Generate all plausible barcode variants to maximise lookup hit rate.

    Strategy:
    - Strip non-digit characters first (handles CODE-128 with hyphens/spaces).
    - Keep original (might be alphanumeric CODE-128).
    - EAN-13 <-> UPC-A (leading-zero padding in both directions).
    - EAN-8 kept as-is.
    - UPC-E 7-digit -> 0-prefix expansion.
    - 11-digit (dropped check digit) -> try computed completion.
    - 14-digit ITF-14 -> strip leading zero to get EAN-13.
    - Also try auto-correcting a misread check digit (common in bad lighting).
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
            # UPC-A -> pad to EAN-13
            candidates.append("0" + digits_only)
        elif n == 13:
            if digits_only.startswith("0"):
                candidates.append(digits_only[1:])
            # Also try with corrected check digit (catches single-digit scan errors)
            corrected = _fix_check_digit(digits_only)
            if corrected and corrected != digits_only:
                candidates.append(corrected)
                if corrected.startswith("0"):
                    candidates.append(corrected[1:])
        elif n == 7:
            # UPC-E 7-digit (without check digit)
            candidates.append("0" + digits_only)
        elif n == 8:
            # EAN-8 -- also try as padded UPC-A (rare edge case)
            candidates.append("0000" + digits_only)
        elif n == 11:
            # EAN-13 with dropped check digit -- compute the correct last digit
            for last in "0123456789":
                trial = digits_only + last
                if _ean_check_digit(trial[:-1], len(trial)) == int(last):
                    candidates.append(trial)
                    break
            candidates.append("0" + digits_only + "0")  # brute fallback
        elif n == 14:
            # ITF-14 / GTIN-14 -> strip leading zero -> EAN-13
            if digits_only.startswith("0"):
                candidates.append(digits_only[1:])

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
            url, headers={"User-Agent": _ua()})
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
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
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
            url, headers={"User-Agent": _ua(), "Accept": "application/json"})
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
                "User-Agent": _ua(),
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
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
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


def _fetch_barcodelookup(barcode: str) -> dict | None:
    """
    Scrape barcodelookup.com — one of the largest free barcode databases with
    strong coverage of Indian retail products (Hindustan Unilever, ITC, Nestle
    India, Parle, Britannia, Amul, etc.).
    """
    url = f"https://www.barcodelookup.com/{barcode}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.barcodelookup.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    # Try multiple patterns to extract product name from the page
    name_match = (
        re.search(r'<h4[^>]*class="[^"]*product-name[^"]*"[^>]*>\s*([^<]+)\s*<', html, re.IGNORECASE) or
        re.search(r'<h1[^>]*>\s*([^<]{5,120})\s*</h1>', html, re.IGNORECASE) or
        re.search(r'"name"\s*:\s*"([^"]{5,120})"', html)
    )
    if not name_match:
        return None
    name = name_match.group(1).strip()
    # Reject generic page titles
    if any(x in name.lower() for x in ["barcode lookup", "scan", "upc", "ean", "search"]):
        return None
    if len(name) < 3:
        return None

    brand_match = (
        re.search(r'<span[^>]*>\s*Brand\s*</span>\s*<span[^>]*>\s*([^<]+)\s*</span>', html, re.IGNORECASE) or
        re.search(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
    )
    brand = brand_match.group(1).strip() if brand_match else ""

    cat_match = re.search(
        r'<span[^>]*>\s*Category\s*</span>\s*<span[^>]*>\s*([^<]+)\s*</span>', html, re.IGNORECASE)
    category = cat_match.group(1).strip() if cat_match else ""

    desc_match = re.search(
        r'<p[^>]*class="[^"]*product-description[^"]*"[^>]*>\s*([^<]{10,}?)\s*</p>', html, re.IGNORECASE)
    description = desc_match.group(1).strip() if desc_match else ""

    img_match = (
        re.search(r'<img[^>]*class="[^"]*product-image[^"]*"[^>]*src="([^"]+)"', html, re.IGNORECASE) or
        re.search(r'"image"\s*:\s*"(https?://[^"]+)"', html)
    )
    image_url = img_match.group(1).strip() if img_match else ""

    return {
        "source_db": "Barcode Lookup",
        "name": name,
        "brand": brand,
        "category": category,
        "description": description,
        "image_url": image_url,
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_barcodesdb(barcode: str) -> dict | None:
    """barcodesdb.com — community-maintained barcode registry."""
    url = f"https://barcodesdb.com/api/v1/barcode/{barcode}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _ua(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not raw or not isinstance(raw, dict):
        return None
    product = raw.get("product") or raw
    name = (product.get("name") or product.get("title") or "").strip()
    if not name:
        return None
    return {
        "source_db": "BarcodesDB",
        "name": name,
        "brand": (product.get("brand") or "").strip(),
        "category": (product.get("category") or "").strip(),
        "description": (product.get("description") or "").strip(),
        "image_url": product.get("image") or product.get("image_url") or "",
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
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT data, source FROM barcode_cache WHERE barcode = ?", (barcode,)
        ).fetchone()
        conn.close()
        if row:
            # Reject old synthetic AI-generated cache entries
            if row["source"] in ("AI Model", "AI Prediction"):
                return None
            d = json.loads(row["data"])
            if d.get("name"):
                return d
    except Exception:
        pass
    return None


def _cache_get_any_variant(variants: list[str]) -> dict | None:
    """Check cache for any of the barcode variants — returns first hit."""
    for v in variants:
        cached = _cache_get(v)
        if cached and cached.get("name"):
            return cached
    return None


def _cache_set(barcode: str, data: dict, source: str) -> None:
    try:
        import sqlite3
        from datetime import datetime
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "store.db")
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO barcode_cache (barcode, data, source, cached_at) VALUES (?, ?, ?, ?)",
            (barcode, json.dumps(data), source, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Full parallel multi-source fetcher  (v6 -- ALL sources in parallel)
# ---------------------------------------------------------------------------

def _fetch_all_sources_parallel(variants: list[str]) -> dict | None:
    """
    Query ALL barcode sources across ALL variants concurrently.
    First result with a valid product name wins.
    Sources are submitted in priority order so faster/more reliable sources
    tend to win ties.
    """
    tasks = []

    # Priority 1 — Open Food/Beauty/Products Facts (best structured data)
    for domain, source_name in [
        ("openfoodfacts",     "Open Food Facts"),
        ("openbeautyfacts",   "Open Beauty Facts"),
        ("openproductsfacts", "Open Products Facts"),
    ]:
        for v in variants:
            tasks.append((_fetch_open_facts, (v, domain, source_name)))

    # Priority 2 — UPC ItemDB
    for v in variants:
        tasks.append((_fetch_upc_itemdb, (v,)))

    # Priority 3 — Go-UPC
    for v in variants:
        tasks.append((_fetch_go_upc, (v,)))

    # Priority 4 — barcodelookup.com (large DB, good Indian coverage)
    for v in variants:
        tasks.append((_fetch_barcodelookup, (v,)))

    # Priority 5 — EAN Search
    for v in variants:
        tasks.append((_fetch_ean_search, (v,)))

    # Priority 6 — Open Library (books only)
    for v in variants:
        if v.startswith("978") or v.startswith("979"):
            tasks.append((_fetch_open_library, (v,)))

    # Priority 7 — BarcodesDB
    for v in variants:
        tasks.append((_fetch_barcodesdb, (v,)))

    max_workers = min(len(tasks), 20)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fn, *args): (fn.__name__, args)
            for fn, args in tasks
        }
        try:
            for future in as_completed(futures, timeout=FULL_PARALLEL_TIMEOUT):
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

    Returns _found=True with real product details if found.
    Returns _found=False with empty name (plus brand hints for Indian products)
    if not found, so the user can type the product name without synthetic overwrites.
    """
    barcode = str(barcode).strip()
    if not barcode:
        return {"_found": False, "name": "", "code_value": "", "barcode_raw": ""}

    variants = _get_barcode_variants(barcode)

    # Step 1: Check cache for any variant (fastest path)
    if use_cache and not force_ai:
        cached = _cache_get_any_variant(variants)
        if cached and cached.get("name"):
            cached["_from_cache"] = True
            cached["_found"] = True
            cached["barcode_raw"] = barcode
            cached["code_value"] = barcode
            return cached

    # Step 2: Offline Indian prefix hints (instant, no network needed)
    india_hint = _indian_prefix_hint(barcode)

    # Step 3: Query ALL sources in full parallel across all variants
    result: dict = {}
    res = _fetch_all_sources_parallel(variants)
    if res and res.get("name"):
        result = res

    # Step 4: Found — enrich with Indian hints, apply brand rules, cache all variants
    if result.get("name"):
        # Fill gaps with Indian prefix hints
        if india_hint:
            for k, v in india_hint.items():
                if not result.get(k):
                    result[k] = v
        result = _apply_ai_suggestions(result)
        result["barcode_raw"] = barcode
        result["code_value"] = barcode
        result.setdefault("unit_price",    0.0)
        result.setdefault("cost_price",    0.0)
        result.setdefault("stock_qty",     10)
        result.setdefault("reorder_level", 5)
        # Cache under ALL variants so future scans are instant
        source = result.get("source_db", "Open DB")
        for v in variants:
            _cache_set(v, result, source)
        result["_found"] = True
        return result

    # Step 5: Partial Indian hit — return brand hints without a product name
    # The user can fill in the product name; brand/category are pre-filled.
    if india_hint and india_hint.get("brand"):
        return {
            "_found": False,
            "source_db": "GS1 India Prefix",
            "name": "",
            "brand": india_hint.get("brand", ""),
            "category": india_hint.get("category", "General"),
            "manufacturer": india_hint.get("manufacturer", ""),
            "country_of_origin": "India",
            "unit_label": "pcs",
            "stock_qty": 10,
            "reorder_level": 5,
            "unit_price": 0.0,
            "cost_price": 0.0,
            "barcode_raw": barcode,
            "code_value": barcode,
        }

    # Step 6: Force AI synthetic prediction (only when explicitly requested)
    if force_ai:
        try:
            from ml.barcode_ai import analyze_barcode as _ai
            ai_res = _ai(barcode)
            if ai_res and ai_res.get("name"):
                ai_res["source_db"] = "AI Prediction"
                ai_res["barcode_raw"] = barcode
                ai_res["_found"] = True
                return ai_res
        except Exception:
            pass

    # Step 7: Not found — return clean empty structure
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
