"""
Multi-Source High-Precision Barcode Lookup Engine (v6.1 -- Robust Retry + Reliable Logging + Render Persistence)
------------------------------------------------------------------------------------------------------------------
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

v6.1 improvements:
- Full Python logging system (logs every lookup, cache HIT/MISS, per-source status)
- Explicit status differentiation: FOUND, NOT_FOUND, TEMPORARY_FAILURE, RATE_LIMITED, TIMEOUT
- Never reports 'Not found in public databases' when an API times out or rate limits
- Automatic 1-step backoff retry for transient network/API errors per source
- First check user's local inventory, then persistent SQLite barcode_cache table
- Successful lookups permanently cached across all barcode variants
- Graceful exception logging (no silent pass on cache/DB errors)
- Strict AI isolation: synthetic predictions never silently override real database lookups
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import os
import re
import sys
import random
import time
import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logger = logging.getLogger("barcode_lookup")
if not logger.handlers:
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(_ch)
    logger.setLevel(logging.INFO)

TIMEOUT = 7                 # seconds per individual HTTP request
FULL_PARALLEL_TIMEOUT = 10  # max seconds to wait for parallel batch

_UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17 Mobile/15E148 Safari/604.1",
    "AIInventoryManager/6.1 (contact@aiinventory.local)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

def _ua() -> str:
    return random.choice(_UA_POOL)


# ---------------------------------------------------------------------------
# GS1-India prefix knowledge base (offline, instant)
# ---------------------------------------------------------------------------
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
    if not barcode.startswith("890") or len(barcode) < 7:
        return None
    for prefix, (brand, mfr, cat) in _INDIA_PREFIX_BRANDS.items():
        if barcode.startswith(prefix):
            return {"brand": brand, "manufacturer": mfr, "category": cat, "country_of_origin": "India"}
    return {"country_of_origin": "India"}


# ---------------------------------------------------------------------------
# Barcode Variant Generator & Check Digit Fixer
# ---------------------------------------------------------------------------
def _ean_check_digit(body: str, total_len: int) -> int:
    weights = [3 if (total_len - 1 - i) % 2 == 0 else 1 for i in range(len(body))]
    s = sum(int(d) * w for d, w in zip(body, weights))
    return (10 - (s % 10)) % 10

def _fix_check_digit(barcode: str) -> str | None:
    if not barcode.isdigit():
        return None
    body = barcode[:-1]
    correct = _ean_check_digit(body, len(barcode))
    if correct == int(barcode[-1]):
        return None
    return body + str(correct)

def _get_barcode_variants(barcode: str) -> list[str]:
    original = str(barcode).strip()
    digits_only = "".join(c for c in original if c.isdigit())
    candidates = [original]
    if digits_only and digits_only != original:
        candidates.append(digits_only)
    if digits_only:
        n = len(digits_only)
        if n == 12:
            candidates.append("0" + digits_only)
        elif n == 13:
            if digits_only.startswith("0"):
                candidates.append(digits_only[1:])
            corrected = _fix_check_digit(digits_only)
            if corrected and corrected != digits_only:
                candidates.append(corrected)
                if corrected.startswith("0"):
                    candidates.append(corrected[1:])
        elif n == 7:
            candidates.append("0" + digits_only)
        elif n == 8:
            candidates.append("0000" + digits_only)
        elif n == 11:
            for last in "0123456789":
                trial = digits_only + last
                if _ean_check_digit(trial[:-1], len(trial)) == int(last):
                    candidates.append(trial)
                    break
            candidates.append("0" + digits_only + "0")
        elif n == 14:
            if digits_only.startswith("0"):
                candidates.append(digits_only[1:])
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# Individual Source Fetchers with Detailed Error Detection
# Returns: (status, result_dict)
# Statuses: 'FOUND', 'NOT_FOUND', 'RATE_LIMITED', 'TIMEOUT', 'TEMPORARY_FAILURE'
# ---------------------------------------------------------------------------

def _fetch_open_facts(barcode: str, domain: str = "openfoodfacts", source_name: str = "Open Food Facts") -> tuple[str, dict | None]:
    url = f"https://world.{domain}.org/api/v2/product/{barcode}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"[{source_name}] HTTP {resp.status} for {barcode}")
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"[{source_name}] RATE_LIMITED (HTTP 429) for {barcode}")
            return "RATE_LIMITED", None
        elif e.code == 404:
            logger.info(f"[{source_name}] NOT_FOUND (HTTP 404) for {barcode}")
            return "NOT_FOUND", None
        else:
            logger.warning(f"[{source_name}] TEMPORARY_FAILURE (HTTP {e.code}) for {barcode}")
            return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError) as e:
        logger.warning(f"[{source_name}] TIMEOUT/NETWORK_ERROR for {barcode}: {e}")
        return "TIMEOUT", None
    except Exception as e:
        logger.warning(f"[{source_name}] Exception for {barcode}: {e}")
        return "TEMPORARY_FAILURE", None

    if raw.get("status") != 1:
        return "NOT_FOUND", None

    p = raw.get("product", {})
    name = (p.get("product_name_en") or p.get("product_name") or p.get("abbreviated_product_name") or "").strip()
    if not name:
        return "NOT_FOUND", None

    nutriments = p.get("nutriments", {})
    nutri_parts = []
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
        ql = str(quantity).lower()
        if any(u in ql for u in ["ml", "l", "litre", "liter", "fl oz"]):
            volume = str(quantity)
        else:
            weight = str(quantity)
    image_url = (p.get("image_front_url") or p.get("image_url") or p.get("image_thumb_url") or "")
    return "FOUND", {
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


def _fetch_open_library(barcode: str) -> tuple[str, dict | None]:
    if not (barcode.startswith("978") or barcode.startswith("979")):
        return "NOT_FOUND", None
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{barcode}&jscmd=data&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMITED", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    book_data = raw.get(f"ISBN:{barcode}") or (list(raw.values())[0] if raw else None)
    if not book_data or not book_data.get("title"):
        return "NOT_FOUND", None
    authors = book_data.get("authors", [])
    author_name = authors[0].get("name") if authors else ""
    cover = book_data.get("cover", {})
    image_url = cover.get("large") or cover.get("medium") or cover.get("small") or ""
    return "FOUND", {
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


def _fetch_upc_itemdb(barcode: str) -> tuple[str, dict | None]:
    url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"[UPC ItemDB] RATE_LIMITED (HTTP 429) for {barcode}")
            return "RATE_LIMITED", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    items = raw.get("items", [])
    if not items or not items[0].get("title"):
        return "NOT_FOUND", None
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
    return "FOUND", {
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


def _fetch_go_upc(barcode: str) -> tuple[str, dict | None]:
    url = f"https://go-upc.com/api/v1/code/{barcode}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMITED", None
        elif e.code == 404:
            return "NOT_FOUND", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    product = raw.get("product") or {}
    name = (product.get("name") or "").strip()
    if not name:
        return "NOT_FOUND", None
    return "FOUND", {
        "source_db": "Go-UPC",
        "name": name,
        "brand": (product.get("brand") or "").strip(),
        "category": (product.get("category") or "").strip(),
        "description": (product.get("description") or "").strip(),
        "image_url": product.get("imageUrl") or "",
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_ean_search(barcode: str) -> tuple[str, dict | None]:
    url = f"https://ean-search.org/perl/api.pl?q={barcode}&lang=1&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMITED", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    if not raw or not isinstance(raw, list) or not raw[0].get("name"):
        return "NOT_FOUND", None
    item = raw[0]
    return "FOUND", {
        "source_db": "EAN Search",
        "name": item.get("name") or "",
        "brand": "",
        "category": item.get("categoryName") or "",
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_barcodelookup(barcode: str) -> tuple[str, dict | None]:
    url = f"https://www.barcodelookup.com/{barcode}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.barcodelookup.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"[Barcode Lookup Scrape] RATE_LIMITED (HTTP 429) for {barcode}")
            return "RATE_LIMITED", None
        elif e.code == 404:
            return "NOT_FOUND", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    name_match = (
        re.search(r'<h4[^>]*class="[^"]*product-name[^"]*"[^>]*>\s*([^<]+)\s*<', html, re.IGNORECASE) or
        re.search(r'<h1[^>]*>\s*([^<]{5,120})\s*</h1>', html, re.IGNORECASE) or
        re.search(r'"name"\s*:\s*"([^"]{5,120})"', html)
    )
    if not name_match:
        return "NOT_FOUND", None
    name = name_match.group(1).strip()
    if any(x in name.lower() for x in ["barcode lookup", "scan", "upc", "ean", "search"]):
        return "NOT_FOUND", None
    if len(name) < 3:
        return "NOT_FOUND", None

    brand_match = (
        re.search(r'<span[^>]*>\s*Brand\s*</span>\s*<span[^>]*>\s*([^<]+)\s*</span>', html, re.IGNORECASE) or
        re.search(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
    )
    brand = brand_match.group(1).strip() if brand_match else ""
    cat_match = re.search(r'<span[^>]*>\s*Category\s*</span>\s*<span[^>]*>\s*([^<]+)\s*</span>', html, re.IGNORECASE)
    category = cat_match.group(1).strip() if cat_match else ""
    desc_match = re.search(r'<p[^>]*class="[^"]*product-description[^"]*"[^>]*>\s*([^<]{10,}?)\s*</p>', html, re.IGNORECASE)
    description = desc_match.group(1).strip() if desc_match else ""
    img_match = (
        re.search(r'<img[^>]*class="[^"]*product-image[^"]*"[^>]*src="([^"]+)"', html, re.IGNORECASE) or
        re.search(r'"image"\s*:\s*"(https?://[^"]+)"', html)
    )
    image_url = img_match.group(1).strip() if img_match else ""
    return "FOUND", {
        "source_db": "Barcode Lookup",
        "name": name,
        "brand": brand,
        "category": category,
        "description": description,
        "image_url": image_url,
        "barcode_raw": barcode,
        "unit_label": "pcs",
    }


def _fetch_barcodesdb(barcode: str) -> tuple[str, dict | None]:
    url = f"https://barcodesdb.com/api/v1/barcode/{barcode}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return "TEMPORARY_FAILURE", None
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMITED", None
        return "TEMPORARY_FAILURE", None
    except (urllib.error.URLError, FuturesTimeout, TimeoutError, socket.timeout, OSError):
        return "TIMEOUT", None
    except Exception:
        return "TEMPORARY_FAILURE", None

    if not raw or not isinstance(raw, dict):
        return "NOT_FOUND", None
    product = raw.get("product") or raw
    name = (product.get("name") or product.get("title") or "").strip()
    if not name:
        return "NOT_FOUND", None
    return "FOUND", {
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
# Per-source Retry Wrapper
# ---------------------------------------------------------------------------
def _fetch_source_with_retry(fetch_fn, *args) -> tuple[str, dict | None]:
    source_name = args[-1] if len(args) > 2 and isinstance(args[-1], str) else fetch_fn.__name__
    barcode = args[0] if args else ""

    status, res = fetch_fn(*args)
    if status == "FOUND":
        logger.info(f"[{source_name}] FOUND: '{res.get('name')}' for barcode {barcode}")
        return status, res

    if status in ("TEMPORARY_FAILURE", "TIMEOUT"):
        logger.info(f"[{source_name}] Retrying {barcode} after transient {status}...")
        time.sleep(0.4)
        status, res = fetch_fn(*args)
        if status == "FOUND":
            logger.info(f"[{source_name}] RETRY SUCCESS: '{res.get('name')}' for barcode {barcode}")
            return status, res

    return status, res


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _first_category(cats):
    if not cats:
        return ""
    parts = [c.strip().title() for c in str(cats).split(",") if c.strip()]
    return parts[0] if parts else ""

def _guess_unit(weight, volume, quantity=""):
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

def _apply_ai_suggestions(data):
    brand = (data.get("brand") or "").lower()
    BRAND_RULES = {
        "samsung":    {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Samsung Electronics"},
        "apple":      {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Apple Inc."},
        "sony":       {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Sony Corporation"},
        "lg":         {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "LG Electronics"},
        "panasonic":  {"category": "Electronics",    "warranty_info": "1 Year",  "manufacturer": "Panasonic Corporation"},
        "coca-cola":  {"category": "Beverages",      "unit_label":   "bottle",   "manufacturer": "The Coca-Cola Company"},
        "pepsi":      {"category": "Beverages",      "unit_label":   "bottle",   "manufacturer": "PepsiCo Inc."},
        "nestle":     {"category": "Food & Grocery", "manufacturer": "Nestle S.A."},
        "amul":       {"category": "Dairy",          "manufacturer": "GCMMF",    "country_of_origin": "India"},
        "britannia":  {"category": "Snacks",         "manufacturer": "Britannia Industries", "country_of_origin": "India"},
        "parle":      {"category": "Snacks",         "manufacturer": "Parle Products Pvt Ltd", "country_of_origin": "India"},
        "dabur":      {"category": "Personal Care",  "manufacturer": "Dabur India Ltd", "country_of_origin": "India"},
        "himalaya":   {"category": "Personal Care",  "manufacturer": "The Himalaya Drug Company", "country_of_origin": "India"},
        "colgate":    {"category": "Personal Care",  "unit_label":   "pcs",      "manufacturer": "Colgate-Palmolive"},
        "gillette":   {"category": "Personal Care",  "warranty_info": "N/A",     "manufacturer": "Procter & Gamble"},
        "dettol":     {"category": "Health & Hygiene", "manufacturer": "Reckitt Benckiser"},
        "surf excel":  {"category": "Household",      "manufacturer": "Hindustan Unilever"},
        "maggi":      {"category": "Food & Grocery", "unit_label":   "pcs",      "manufacturer": "Nestle India"},
        "tata":       {"category": "Food & Grocery", "manufacturer": "Tata Consumer Products", "country_of_origin": "India"},
        "haldiram":   {"category": "Snacks",         "manufacturer": "Haldiram Foods Pvt Ltd", "country_of_origin": "India"},
        "lays":       {"category": "Snacks",         "unit_label":   "pcs",      "manufacturer": "PepsiCo India"},
        "cadbury":    {"category": "Snacks",         "manufacturer": "Cadbury India"},
        "unilever":   {"manufacturer": "Hindustan Unilever"},
        "itc":        {"country_of_origin": "India", "manufacturer": "ITC Limited"},
        "godrej":     {"country_of_origin": "India", "manufacturer": "Godrej Consumer Products"},
        "wipro":      {"country_of_origin": "India"},
        "patanjali":  {"country_of_origin": "India", "manufacturer": "Patanjali Ayurved"},
        "marico":     {"country_of_origin": "India", "manufacturer": "Marico Limited"},
        "emami":      {"country_of_origin": "India", "manufacturer": "Emami Limited"},
        "pidilite":   {"country_of_origin": "India", "manufacturer": "Pidilite Industries"},
        "hindustan unilever": {"country_of_origin": "India", "manufacturer": "Hindustan Unilever Ltd"},
        "p&g":        {"manufacturer": "Procter & Gamble"},
        "reckitt":    {"country_of_origin": "India", "manufacturer": "Reckitt Benckiser India"},
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
# Local SQLite Cache & Inventory Checking with Logging
# ---------------------------------------------------------------------------

def _check_user_inventory(barcode: str, user_id: int = 1) -> dict | None:
    """Check if the barcode is already present in the user's products inventory table."""
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM products WHERE (code_value = ? OR barcode_raw = ?) AND user_id = ?",
            (barcode, barcode, user_id)
        ).fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["_from_inventory"] = True
            d["_found"] = True
            d["status"] = "FOUND"
            d["source_db"] = "Local Inventory"
            logger.info(f"[INVENTORY] HIT for {barcode} -> '{d.get('name')}'")
            return d
    except Exception as e:
        logger.warning(f"[INVENTORY] Database query warning for {barcode}: {e}")
    return None


def _cache_get(barcode: str) -> dict | None:
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data, source FROM barcode_cache WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
        if row:
            if row["source"] in ("AI Model", "AI Prediction"):
                return None
            d = json.loads(row["data"])
            if d.get("name"):
                return d
    except Exception as e:
        logger.warning(f"[CACHE] Read warning for {barcode}: {e}")
    return None


def _cache_get_any_variant(variants: list[str]) -> dict | None:
    for v in variants:
        cached = _cache_get(v)
        if cached and cached.get("name"):
            logger.info(f"[CACHE] HIT {v} -> '{cached.get('name')}'")
            return cached
    return None


def _cache_set(barcode: str, data: dict, source: str) -> None:
    try:
        import sqlite3
        from datetime import datetime
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS barcode_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode     TEXT UNIQUE NOT NULL,
                data        TEXT NOT NULL,
                source      TEXT DEFAULT '',
                cached_at   TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO barcode_cache (barcode, data, source, cached_at) VALUES (?, ?, ?, ?)",
            (barcode, json.dumps(data), source, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()
        logger.info(f"[CACHE] SAVED {barcode} -> '{data.get('name')}' (Source: {source})")
    except Exception as e:
        logger.warning(f"[CACHE] Write error for {barcode}: {e}")


# ---------------------------------------------------------------------------
# Parallel Multi-Source Batch Fetcher
# ---------------------------------------------------------------------------

def _fetch_all_sources_parallel(variants: list[str], barcode_raw: str = "") -> tuple[str, dict | None]:
    tasks = []

    for domain, source_name in [
        ("openfoodfacts",     "Open Food Facts"),
        ("openbeautyfacts",   "Open Beauty Facts"),
        ("openproductsfacts", "Open Products Facts"),
    ]:
        for v in variants:
            tasks.append((_fetch_open_facts, (v, domain, source_name)))

    for v in variants:
        tasks.append((_fetch_upc_itemdb, (v,)))

    for v in variants:
        tasks.append((_fetch_go_upc, (v,)))

    for v in variants:
        tasks.append((_fetch_barcodelookup, (v,)))

    for v in variants:
        tasks.append((_fetch_ean_search, (v,)))

    for v in variants:
        if v.startswith("978") or v.startswith("979"):
            tasks.append((_fetch_open_library, (v,)))

    for v in variants:
        tasks.append((_fetch_barcodesdb, (v,)))

    max_workers = min(len(tasks), 20)
    statuses = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_source_with_retry, fn, *args): (fn.__name__, args)
            for fn, args in tasks
        }
        try:
            for future in as_completed(futures, timeout=FULL_PARALLEL_TIMEOUT):
                try:
                    status, res = future.result()
                    statuses.append(status)
                    if status == "FOUND" and res and res.get("name"):
                        for f in futures:
                            f.cancel()
                        return "FOUND", res
                except Exception as e:
                    logger.warning(f"[PARALLEL] Task exception for {barcode_raw}: {e}")
                    statuses.append("TEMPORARY_FAILURE")
        except FuturesTimeout:
            logger.warning(f"[PARALLEL] Timeout waiting for batch completion for {barcode_raw}")
            statuses.append("TIMEOUT")

    if any(s in ("RATE_LIMITED", "TIMEOUT", "TEMPORARY_FAILURE") for s in statuses):
        return "TEMPORARY_FAILURE", None

    return "NOT_FOUND", None


# ---------------------------------------------------------------------------
# Main Public Barcode Lookup API
# ---------------------------------------------------------------------------

def lookup_barcode(barcode: str, use_cache: bool = True, force_ai: bool = False, user_id: int = 1) -> dict:
    barcode = str(barcode).strip()
    if not barcode:
        return {"_found": False, "status": "INVALID", "name": "", "code_value": "", "barcode_raw": ""}

    logger.info(f"[BARCODE] Looking up: {barcode}")
    variants = _get_barcode_variants(barcode)

    # Step 1: Check user inventory (local SQLite products table)
    if use_cache and not force_ai:
        inv_item = _check_user_inventory(barcode, user_id=user_id)
        if inv_item:
            return inv_item

    # Step 2: Check persistent local barcode_cache table
    if use_cache and not force_ai:
        cached = _cache_get_any_variant(variants)
        if cached and cached.get("name"):
            cached["_from_cache"] = True
            cached["_found"] = True
            cached["status"] = "FOUND"
            cached["barcode_raw"] = barcode
            cached["code_value"] = barcode
            return cached

    logger.info(f"[CACHE] MISS for {barcode}")
    india_hint = _indian_prefix_hint(barcode)

    # Step 3: Query ALL external sources in parallel with backoff retries
    status, res = _fetch_all_sources_parallel(variants, barcode_raw=barcode)

    # Step 4: FOUND -- enrich, cache under all variants permanently, return
    if status == "FOUND" and res and res.get("name"):
        if india_hint:
            for k, v in india_hint.items():
                if not res.get(k):
                    res[k] = v
        res = _apply_ai_suggestions(res)
        res["barcode_raw"] = barcode
        res["code_value"] = barcode
        res.setdefault("unit_price",    0.0)
        res.setdefault("cost_price",    0.0)
        res.setdefault("stock_qty",     10)
        res.setdefault("reorder_level", 5)
        res["_found"] = True
        res["status"] = "FOUND"

        source = res.get("source_db", "Open DB")
        for v in variants:
            _cache_set(v, res, source)

        logger.info(f"[BARCODE] FINAL STATUS: FOUND for {barcode} (via {source})")
        return res

    # Step 5: Temporary Failure (Network/API Timeout/Rate Limited)
    if status == "TEMPORARY_FAILURE":
        logger.warning(f"[BARCODE] FINAL STATUS: TEMPORARY_FAILURE for {barcode}")
        return {
            "_found": False,
            "status": "TEMPORARY_FAILURE",
            "source_db": "Temporary Failure",
            "name": "",
            "brand": india_hint.get("brand", "") if india_hint else "",
            "category": india_hint.get("category", "General") if india_hint else "General",
            "manufacturer": india_hint.get("manufacturer", "") if india_hint else "",
            "barcode_raw": barcode,
            "code_value": barcode,
            "message": "Product details could not be retrieved right now due to a network or API timeout. Tap Retry to try again.",
        }

    # Step 6: Force AI synthetic prediction (ONLY when explicitly requested by user)
    if force_ai:
        try:
            from ml.barcode_ai import analyze_barcode as _ai
            ai_res = _ai(barcode)
            if ai_res and ai_res.get("name"):
                ai_res["source_db"] = "AI Prediction"
                ai_res["barcode_raw"] = barcode
                ai_res["_found"] = True
                ai_res["status"] = "FOUND"
                logger.info(f"[BARCODE] FINAL STATUS: FOUND via AI Prediction for {barcode}")
                return ai_res
        except Exception as e:
            logger.warning(f"[AI_LOOKUP] Exception for {barcode}: {e}")

    # Step 7: Genuinely Not Found across all working sources
    logger.info(f"[BARCODE] FINAL STATUS: NOT_FOUND for {barcode}")
    if india_hint and india_hint.get("brand"):
        return {
            "_found": False,
            "status": "NOT_FOUND",
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
            "message": f"Barcode read successfully. GS1 India prefix detected ({india_hint.get('brand')}). Product name not found in public databases.",
        }

    return {
        "_found": False,
        "status": "NOT_FOUND",
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
        "message": "Barcode read successfully, but product information was not found in public databases. You can enter details manually.",
    }
