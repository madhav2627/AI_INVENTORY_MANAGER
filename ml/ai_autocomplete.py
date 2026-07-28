"""
AI Autocomplete
---------------
Provides intelligent field suggestions when barcode database data is missing.
Uses a comprehensive rule-based knowledge base covering brands, product names,
categories, and retail patterns.
"""

def get_suggestions(product_data: dict) -> dict:
    """
    Given partial product data, return a dict of suggested values for empty fields.
    Never overwrites existing values.
    """
    suggestions = {}
    name  = (product_data.get("name") or "").lower()
    brand = (product_data.get("brand") or "").lower()
    cat   = (product_data.get("category") or "").lower()

    # ── Brand → Metadata mappings ────────────────────────────────────────────
    BRAND_META = {
        "samsung":      {"category": "Electronics", "manufacturer": "Samsung Electronics Co., Ltd.", "warranty_info": "1 Year", "country_of_origin": "South Korea"},
        "apple":        {"category": "Electronics", "manufacturer": "Apple Inc.", "warranty_info": "1 Year", "country_of_origin": "USA"},
        "mi":           {"category": "Electronics", "manufacturer": "Xiaomi Technology", "warranty_info": "1 Year", "country_of_origin": "China"},
        "xiaomi":       {"category": "Electronics", "manufacturer": "Xiaomi Technology", "warranty_info": "1 Year", "country_of_origin": "China"},
        "oneplus":      {"category": "Electronics", "manufacturer": "OnePlus Technology", "warranty_info": "1 Year", "country_of_origin": "China"},
        "realme":       {"category": "Electronics", "manufacturer": "Realme Mobile Telecommunications", "warranty_info": "1 Year"},
        "vivo":         {"category": "Electronics", "manufacturer": "Vivo Communication Technology", "warranty_info": "1 Year"},
        "oppo":         {"category": "Electronics", "manufacturer": "Guangdong Oppo Mobile Telecommunications", "warranty_info": "1 Year"},
        "nokia":        {"category": "Electronics", "manufacturer": "Nokia Corporation", "warranty_info": "1 Year", "country_of_origin": "Finland"},
        "sony":         {"category": "Electronics", "manufacturer": "Sony Corporation", "warranty_info": "1 Year", "country_of_origin": "Japan"},
        "lg":           {"category": "Electronics", "manufacturer": "LG Electronics", "warranty_info": "1 Year", "country_of_origin": "South Korea"},
        "panasonic":    {"category": "Electronics", "manufacturer": "Panasonic Corporation", "warranty_info": "1 Year", "country_of_origin": "Japan"},
        "bosch":        {"category": "Electronics", "manufacturer": "Robert Bosch GmbH", "warranty_info": "2 Years", "country_of_origin": "Germany"},
        "philips":      {"category": "Electronics", "manufacturer": "Philips International B.V.", "warranty_info": "1 Year"},
        "amul":         {"category": "Dairy", "manufacturer": "GCMMF (Amul)", "country_of_origin": "India"},
        "mother dairy": {"category": "Dairy", "manufacturer": "Mother Dairy Fruit & Vegetable Pvt. Ltd.", "country_of_origin": "India"},
        "britannia":    {"category": "Snacks", "manufacturer": "Britannia Industries Ltd.", "country_of_origin": "India"},
        "parle":        {"category": "Snacks", "manufacturer": "Parle Products Pvt. Ltd.", "country_of_origin": "India"},
        "haldiram":     {"category": "Snacks", "manufacturer": "Haldiram Foods International Pvt. Ltd.", "country_of_origin": "India"},
        "lays":         {"category": "Snacks", "unit_label": "pcs", "manufacturer": "PepsiCo India Holdings"},
        "lay's":        {"category": "Snacks", "unit_label": "pcs", "manufacturer": "PepsiCo India Holdings"},
        "kurkure":      {"category": "Snacks", "unit_label": "pcs", "manufacturer": "PepsiCo India Holdings"},
        "bingo":        {"category": "Snacks", "unit_label": "pcs", "manufacturer": "ITC Limited"},
        "maggi":        {"category": "Food & Grocery", "unit_label": "pcs", "manufacturer": "Nestlé India Pvt. Ltd.", "country_of_origin": "India"},
        "nestle":       {"category": "Food & Grocery", "manufacturer": "Nestlé S.A.", "country_of_origin": "Switzerland"},
        "kellogg":      {"category": "Food & Grocery", "manufacturer": "Kellogg Company", "country_of_origin": "USA"},
        "tata":         {"category": "Food & Grocery", "manufacturer": "Tata Consumer Products Ltd.", "country_of_origin": "India"},
        "aashirvaad":   {"category": "Food & Grocery", "unit_label": "kg", "manufacturer": "ITC Limited", "country_of_origin": "India"},
        "fortune":      {"category": "Food & Grocery", "unit_label": "ltr", "manufacturer": "Adani Wilmar Ltd.", "country_of_origin": "India"},
        "patanjali":    {"category": "Food & Grocery", "manufacturer": "Patanjali Ayurved Ltd.", "country_of_origin": "India"},
        "dabur":        {"category": "Personal Care", "manufacturer": "Dabur India Ltd.", "country_of_origin": "India"},
        "himalaya":     {"category": "Personal Care", "manufacturer": "The Himalaya Drug Company", "country_of_origin": "India"},
        "colgate":      {"category": "Personal Care", "manufacturer": "Colgate-Palmolive (India) Ltd.", "country_of_origin": "India"},
        "pepsodent":    {"category": "Personal Care", "manufacturer": "Hindustan Unilever Ltd.", "country_of_origin": "India"},
        "oral-b":       {"category": "Personal Care", "manufacturer": "Procter & Gamble", "country_of_origin": "USA"},
        "dove":         {"category": "Personal Care", "manufacturer": "Hindustan Unilever Ltd."},
        "lifebuoy":     {"category": "Personal Care", "manufacturer": "Hindustan Unilever Ltd.", "country_of_origin": "India"},
        "dettol":       {"category": "Health & Hygiene", "manufacturer": "Reckitt Benckiser (India) Ltd."},
        "savlon":       {"category": "Health & Hygiene", "manufacturer": "ITC Limited"},
        "parachute":    {"category": "Personal Care", "manufacturer": "Marico Ltd.", "country_of_origin": "India"},
        "clinic plus":  {"category": "Personal Care", "manufacturer": "Hindustan Unilever Ltd."},
        "head & shoulders": {"category": "Personal Care", "manufacturer": "Procter & Gamble"},
        "pantene":      {"category": "Personal Care", "manufacturer": "Procter & Gamble"},
        "sunsilk":      {"category": "Personal Care", "manufacturer": "Hindustan Unilever Ltd."},
        "gillette":     {"category": "Personal Care", "manufacturer": "Procter & Gamble"},
        "surf excel":   {"category": "Household", "manufacturer": "Hindustan Unilever Ltd."},
        "ariel":        {"category": "Household", "manufacturer": "Procter & Gamble"},
        "harpic":       {"category": "Household", "manufacturer": "Reckitt Benckiser"},
        "vim":          {"category": "Household", "manufacturer": "Hindustan Unilever Ltd."},
        "exo":          {"category": "Household", "manufacturer": "Jyothy Labs Ltd."},
        "coca-cola":    {"category": "Beverages", "unit_label": "ml", "manufacturer": "Hindustan Coca-Cola Beverages Pvt. Ltd.", "country_of_origin": "India"},
        "coke":         {"category": "Beverages", "unit_label": "ml", "manufacturer": "Hindustan Coca-Cola Beverages Pvt. Ltd."},
        "pepsi":        {"category": "Beverages", "unit_label": "ml", "manufacturer": "PepsiCo India Holdings Pvt. Ltd."},
        "sprite":       {"category": "Beverages", "unit_label": "ml", "manufacturer": "Hindustan Coca-Cola Beverages Pvt. Ltd."},
        "7up":          {"category": "Beverages", "unit_label": "ml", "manufacturer": "PepsiCo India"},
        "limca":        {"category": "Beverages", "unit_label": "ml", "manufacturer": "Hindustan Coca-Cola Beverages"},
        "mountain dew": {"category": "Beverages", "unit_label": "ml", "manufacturer": "PepsiCo India"},
        "fanta":        {"category": "Beverages", "unit_label": "ml", "manufacturer": "Hindustan Coca-Cola Beverages"},
        "red bull":     {"category": "Beverages", "unit_label": "can", "manufacturer": "Red Bull GmbH", "country_of_origin": "Austria"},
        "monster":      {"category": "Beverages", "unit_label": "can", "manufacturer": "Monster Beverage Corporation"},
        "lipton":       {"category": "Beverages", "manufacturer": "Hindustan Unilever Ltd."},
        "bru":          {"category": "Beverages", "manufacturer": "Hindustan Unilever Ltd.", "country_of_origin": "India"},
        "nescafe":      {"category": "Beverages", "manufacturer": "Nestlé India", "country_of_origin": "Switzerland"},
        "tropicana":    {"category": "Beverages", "unit_label": "ltr", "manufacturer": "PepsiCo India"},
        "real":         {"category": "Beverages", "unit_label": "ltr", "manufacturer": "Dabur India Ltd."},
        "cadbury":      {"category": "Snacks", "manufacturer": "Mondelez India Foods Pvt. Ltd.", "country_of_origin": "India"},
        "kitkat":       {"category": "Snacks", "manufacturer": "Nestlé India", "unit_label": "pcs"},
        "5 star":       {"category": "Snacks", "manufacturer": "Mondelez India", "unit_label": "pcs"},
        "oreo":         {"category": "Snacks", "manufacturer": "Mondelez India", "unit_label": "pcs"},
        "good day":     {"category": "Snacks", "manufacturer": "Britannia Industries", "unit_label": "pcs"},
        "boost":        {"category": "Beverages", "manufacturer": "Hindustan Unilever Ltd."},
        "horlicks":     {"category": "Beverages", "manufacturer": "Hindustan Unilever Ltd."},
        "complan":      {"category": "Beverages", "manufacturer": "Kraft Heinz"},
        "cipla":        {"category": "Pharmacy", "manufacturer": "Cipla Ltd.", "country_of_origin": "India"},
        "dr. reddy":    {"category": "Pharmacy", "manufacturer": "Dr. Reddy's Laboratories", "country_of_origin": "India"},
        "sun pharma":   {"category": "Pharmacy", "manufacturer": "Sun Pharmaceutical Industries", "country_of_origin": "India"},
    }

    # ── Category → Unit / Sub-category mappings ──────────────────────────────
    CATEGORY_DEFAULTS = {
        "dairy":            {"unit_label": "ltr",  "sub_category": "Fresh Dairy"},
        "beverages":        {"unit_label": "ml",   "sub_category": "Drinks & Juices"},
        "snacks":           {"unit_label": "pcs",  "sub_category": "Packaged Snacks"},
        "food & grocery":   {"unit_label": "kg",   "sub_category": "Grocery Staples"},
        "groceries":        {"unit_label": "kg",   "sub_category": "Grocery Staples"},
        "electronics":      {"unit_label": "pcs",  "sub_category": "Consumer Electronics"},
        "personal care":    {"unit_label": "pcs",  "sub_category": "Health & Beauty"},
        "household":        {"unit_label": "pcs",  "sub_category": "Home Essentials"},
        "bakery":           {"unit_label": "pcs",  "sub_category": "Fresh Baked Goods"},
        "pharmacy":         {"unit_label": "pcs",  "sub_category": "Medicines & Health"},
        "health & hygiene": {"unit_label": "pcs",  "sub_category": "Hygiene Products"},
        "stationery":       {"unit_label": "pcs",  "sub_category": "Office Supplies"},
    }

    # ── Name keywords → field suggestions ────────────────────────────────────
    NAME_KEYWORDS = {
        "mineral water":  {"category": "Beverages", "unit_label": "ltr", "ingredients": "Mineral water"},
        "packaged water": {"category": "Beverages", "unit_label": "ltr"},
        "cold drink":     {"category": "Beverages", "unit_label": "ml"},
        "energy drink":   {"category": "Beverages", "unit_label": "can"},
        "fruit juice":    {"category": "Beverages", "unit_label": "ltr"},
        "milk":           {"category": "Dairy",     "unit_label": "ltr"},
        "curd":           {"category": "Dairy",     "unit_label": "kg"},
        "paneer":         {"category": "Dairy",     "unit_label": "kg"},
        "butter":         {"category": "Dairy",     "unit_label": "kg"},
        "ghee":           {"category": "Dairy",     "unit_label": "ltr"},
        "cheese":         {"category": "Dairy",     "unit_label": "kg"},
        "bread":          {"category": "Bakery",    "unit_label": "pcs"},
        "biscuit":        {"category": "Snacks",    "unit_label": "pcs"},
        "cookies":        {"category": "Snacks",    "unit_label": "pcs"},
        "chocolate":      {"category": "Snacks",    "unit_label": "pcs"},
        "chips":          {"category": "Snacks",    "unit_label": "pcs"},
        "wafer":          {"category": "Snacks",    "unit_label": "pcs"},
        "noodles":        {"category": "Food & Grocery", "unit_label": "pcs", "ingredients": "Wheat flour, water, salt"},
        "pasta":          {"category": "Food & Grocery", "unit_label": "pcs"},
        "atta":           {"category": "Food & Grocery", "unit_label": "kg"},
        "rice":           {"category": "Food & Grocery", "unit_label": "kg"},
        "sugar":          {"category": "Food & Grocery", "unit_label": "kg"},
        "salt":           {"category": "Food & Grocery", "unit_label": "kg"},
        "oil":            {"category": "Food & Grocery", "unit_label": "ltr"},
        "ketchup":        {"category": "Food & Grocery", "unit_label": "pcs"},
        "sauce":          {"category": "Food & Grocery", "unit_label": "pcs"},
        "shampoo":        {"category": "Personal Care", "unit_label": "ml"},
        "conditioner":    {"category": "Personal Care", "unit_label": "ml"},
        "face wash":      {"category": "Personal Care", "unit_label": "ml"},
        "moisturizer":    {"category": "Personal Care", "unit_label": "ml"},
        "cream":          {"category": "Personal Care", "unit_label": "ml"},
        "lotion":         {"category": "Personal Care", "unit_label": "ml"},
        "soap":           {"category": "Personal Care", "unit_label": "pcs"},
        "toothpaste":     {"category": "Personal Care", "unit_label": "pcs"},
        "toothbrush":     {"category": "Personal Care", "unit_label": "pcs"},
        "deodorant":      {"category": "Personal Care", "unit_label": "pcs"},
        "perfume":        {"category": "Personal Care", "unit_label": "ml"},
        "sanitizer":      {"category": "Health & Hygiene", "unit_label": "ml"},
        "handwash":       {"category": "Health & Hygiene", "unit_label": "ml"},
        "detergent":      {"category": "Household",  "unit_label": "kg"},
        "dishwash":       {"category": "Household",  "unit_label": "ml"},
        "floor cleaner":  {"category": "Household",  "unit_label": "ml"},
        "toilet cleaner": {"category": "Household",  "unit_label": "ml"},
        "pen":            {"category": "Stationery", "unit_label": "pcs"},
        "pencil":         {"category": "Stationery", "unit_label": "pcs"},
        "notebook":       {"category": "Stationery", "unit_label": "pcs"},
        "tablet":         {"category": "Electronics", "warranty_info": "1 Year"},
        "charger":        {"category": "Electronics", "warranty_info": "6 Months"},
        "earphone":       {"category": "Electronics", "warranty_info": "6 Months"},
        "headphone":      {"category": "Electronics", "warranty_info": "1 Year"},
        "cable":          {"category": "Electronics", "warranty_info": "6 Months"},
        "battery":        {"category": "Electronics", "unit_label": "pcs"},
        "capsule":        {"category": "Pharmacy",   "unit_label": "pcs"},
        "tablet medicine":{"category": "Pharmacy",   "unit_label": "pcs"},
        "syrup":          {"category": "Pharmacy",   "unit_label": "ml"},
        "drops":          {"category": "Pharmacy",   "unit_label": "ml"},
        "ointment":       {"category": "Pharmacy",   "unit_label": "g"},
        "injection":      {"category": "Pharmacy",   "unit_label": "pcs"},
    }

    # ── Apply brand rules ────────────────────────────────────────────────────
    for b_key, meta in BRAND_META.items():
        if b_key in brand:
            for field, val in meta.items():
                if not product_data.get(field):
                    suggestions[field] = val
            break

    # ── Apply category defaults ──────────────────────────────────────────────
    for c_key, defaults in CATEGORY_DEFAULTS.items():
        if c_key in cat:
            for field, val in defaults.items():
                if not product_data.get(field) and field not in suggestions:
                    suggestions[field] = val
            break

    # ── Apply name keyword rules ─────────────────────────────────────────────
    for n_key, rules in NAME_KEYWORDS.items():
        if n_key in name:
            for field, val in rules.items():
                if not product_data.get(field) and field not in suggestions:
                    suggestions[field] = val
            break

    return suggestions
