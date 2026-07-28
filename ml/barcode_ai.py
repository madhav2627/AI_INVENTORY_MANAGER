"""
AI Barcode Predictive Model
---------------------------
A deterministic offline model that analyzes barcode/QR digits and structure
to predict highly realistic product information (name, category, price, cost, etc.).
This simulates an advanced on-device AI scanner that extracts product metadata
directly from code patterns.
"""
import hashlib

# Country prefix categories
EAN_PREFIXES = {
    "00": "US/Canada (Retail)",
    "01": "US/Canada (Retail)",
    "02": "US/Canada (Retail)",
    "03": "US/Canada (Retail)",
    "04": "US/Canada (Retail)",
    "05": "US/Canada (Retail)",
    "06": "US/Canada (Retail)",
    "07": "US/Canada (Retail)",
    "08": "US/Canada (Retail)",
    "09": "US/Canada (Retail)",
    "30": "France (Cosmetics/Gourmet)",
    "31": "France (Cosmetics/Gourmet)",
    "32": "France (Cosmetics/Gourmet)",
    "33": "France (Cosmetics/Gourmet)",
    "34": "France (Cosmetics/Gourmet)",
    "35": "France (Cosmetics/Gourmet)",
    "36": "France (Cosmetics/Gourmet)",
    "37": "France (Cosmetics/Gourmet)",
    "40": "Germany (Household/Industrial)",
    "41": "Germany (Household/Industrial)",
    "42": "Germany (Household/Industrial)",
    "43": "Germany (Household/Industrial)",
    "44": "Germany (Household/Industrial)",
    "49": "Japan (Electronics/Stationery)",
    "50": "UK (Grocery/Beverages)",
    "69": "China (Electronics/Plastic Ware)",
    "73": "Sweden (Nordic Products)",
    "76": "Switzerland (Premium Goods)",
    "80": "Italy (Fashion/Food)",
    "81": "Italy (Fashion/Food)",
    "82": "Italy (Fashion/Food)",
    "83": "Italy (Fashion/Food)",
    "84": "Spain (Olive Oil/Gourmet)",
    "890": "India (Grocery/Personal Care)",
    "893": "Vietnam (Coffee/Noodles)",
    "899": "Indonesia (Spices/Snacks)",
    "93": "Australia (Local Produce)"
}

BRANDS = {
    "India (Grocery/Personal Care)": [
        ("Amul", "Dairy"), ("Tata", "Beverages"), ("Parle-G", "Snacks"), 
        ("Britannia", "Snacks"), ("Dabur", "Personal Care"), ("Himalaya", "Personal Care"),
        ("Haldiram's", "Snacks"), ("Maggi", "Groceries"), ("Colgate", "Personal Care"),
        ("Surf Excel", "Household"), ("Taj Mahal", "Beverages"), ("Aashirvaad", "Groceries")
    ],
    "US/Canada (Retail)": [
        ("Heinz", "Groceries"), ("Kellogg's", "Groceries"), ("Campbell's", "Groceries"),
        ("Coca-Cola", "Beverages"), ("Pepsi", "Beverages"), ("Gillette", "Personal Care"),
        ("Colgate", "Personal Care"), ("Tide", "Household"), ("Starbucks", "Beverages"),
        ("Oreo", "Snacks"), ("Doritos", "Snacks"), ("Duracell", "Household")
    ],
    "Japan (Electronics/Stationery)": [
        ("Sony", "Electronics"), ("Panasonic", "Electronics"), ("Muji", "Household"),
        ("Pilot", "Stationery"), ("Uni-ball", "Stationery"), ("Canon", "Electronics"),
        ("Casio", "Electronics"), ("Nintendo", "Electronics"), ("Kuretake", "Stationery")
    ],
    "UK (Grocery/Beverages)": [
        ("Lipton", "Beverages"), ("Cadbury", "Snacks"), ("Twinings", "Beverages"),
        ("McVitie's", "Snacks"), ("Heinz", "Groceries"), ("Dyson", "Electronics")
    ],
    "Germany (Household/Industrial)": [
        ("Nivea", "Personal Care"), ("Bosch", "Household"), ("Henkel", "Household"),
        ("Haribo", "Snacks"), ("Ritter Sport", "Snacks"), ("Braun", "Personal Care")
    ],
    "France (Cosmetics/Gourmet)": [
        ("L'Oréal", "Personal Care"), ("Garnier", "Personal Care"), ("Danone", "Dairy"),
        ("Evian", "Beverages"), ("Perrier", "Beverages"), ("Lindt", "Snacks")
    ],
    "China (Electronics/Plastic Ware)": [
        ("Xiaomi", "Electronics"), ("Lenovo", "Electronics"), ("Miniso", "Household"),
        ("Anker", "Electronics"), ("TP-Link", "Electronics")
    ]
}

DEFAULT_BRANDS = [
    ("Generic Premium", "Groceries"), ("Apex", "Household"), ("Scribe", "Stationery"),
    ("Hydra", "Beverages"), ("Nourish", "Personal Care"), ("Volt", "Electronics")
]

ITEMS = {
    "Dairy": [
        "Fresh Butter 500g", "Pure Ghee 1L", "Pasteurized Milk 1L", "Cheddar Cheese 200g", 
        "Greek Yogurt 150g", "Paneer block 200g"
    ],
    "Beverages": [
        "Premium Tea Bag 100s", "Instant Coffee Gold 100g", "Sparkling Water 500ml", 
        "Diet Soda 330ml", "Pure Orange Juice 1L", "Energy Drink 250ml", "Green Tea 25s"
    ],
    "Snacks": [
        "Chocolate Chip Cookies", "Salted Potato Chips 150g", "Roasted Almonds 200g",
        "Wafer Biscuits 120g", "Fruit & Nut Chocolate 80g", "Mint Candy Tin"
    ],
    "Personal Care": [
        "Moisturizing Cream 100ml", "Anti-Dandruff Shampoo 200ml", "Charcoal Face Wash 100g",
        "Triple Action Toothpaste", "Active Charcoal Toothbrush", "Sandalwood Soap 125g"
    ],
    "Groceries": [
        "Basmati Rice 1kg", "Whole Wheat Atta 5kg", "Tomato Ketchup 500g", 
        "Instant Noodles 4-Pack", "Extra Virgin Olive Oil 500ml", "Organic Honey 250g"
    ],
    "Household": [
        "Liquid Detergent 1L", "Glass Cleaner Spray", "Dishwash Gel 500ml", 
        "Alkaline AA Batteries 4s", "Microfiber Cleaning Cloth", "Air Freshener Gel"
    ],
    "Electronics": [
        "Braided USB-C Cable", "Dual Port Wall Charger", "Wireless Optical Mouse",
        "In-Ear Wired Earphones", "CR2032 Lithium Coin Cells 5s"
    ],
    "Stationery": [
        "Fine Tip Gel Pen 3s", "Spiral Notebook A5", "Sticky Notes Pastel",
        "Permanent Marker Black", "Fluorescent Highlighter Set"
    ]
}

def analyze_barcode(code: str, hint: str = None):
    """
    Deterministically analyzes a barcode/QR string and returns predicted product details.
    """
    code = str(code).strip()
    if not code:
        return None
        
    # Generate a stable hash of the code value
    h = hashlib.sha256(code.encode('utf-8')).hexdigest()
    # Convert segments of hash to integers for index selections
    idx_brand = int(h[0:8], 16)
    idx_item = int(h[8:16], 16)
    idx_price = int(h[16:24], 16)
    
    # Identify origin region/prefix class
    region = "Default"
    for prefix, reg_name in EAN_PREFIXES.items():
        if code.startswith(prefix):
            region = reg_name
            break
            
    # Select brand & primary category based on region
    brand_list = BRANDS.get(region, DEFAULT_BRANDS)
    selected_brand, category = brand_list[idx_brand % len(brand_list)]

    # Override category if hint maps to a known category
    if hint:
        hint_lower = hint.lower()
        for cat in ITEMS.keys():
            if cat.lower() in hint_lower:
                category = cat
                break
    
    # Select a specific item category list (fallback to general list if needed)
    item_list = ITEMS.get(category, ITEMS["Groceries"])
    selected_item = item_list[idx_item % len(item_list)]
    
    # Assemble final name
    if hint:
        clean_hint = hint.strip().title()
        # If the hint is exactly a category name, don't append it to product name
        if clean_hint in ITEMS.keys():
            product_name = f"{selected_brand} {selected_item}"
        else:
            product_name = f"{selected_brand} {selected_item} ({clean_hint})"
    else:
        product_name = f"{selected_brand} {selected_item}"
    
    # Predict pricing details
    # Generate base price between 40 and 1200
    base_price = 40.0 + (idx_price % 1160)
    # Give it a nice decimal ending (like .00 or .50 or .99)
    endings = [0.00, 0.00, 0.50, 0.99, 0.99]
    ending = endings[idx_price % len(endings)]
    selling_price = round(int(base_price) + ending, 2)
    
    # Cost price is typically 65% to 80% of selling price
    margin_percentage = 0.65 + ((idx_price % 15) / 100.0)
    cost_price = round(selling_price * margin_percentage, 2)

    # Scale price if hint contains a multiplier number (e.g. "pack of 6", "12-pack")
    if hint:
        import re
        nums = re.findall(r'\d+', hint)
        if nums:
            try:
                mult = int(nums[0])
                if 1 < mult <= 48:
                    discount = 0.85 if mult >= 6 else 0.9
                    selling_price = round(selling_price * mult * discount, 2)
                    cost_price = round(cost_price * mult * discount, 2)
            except ValueError:
                pass
    
    # Stock details
    opening_stock = 10 + (idx_price % 90)
    reorder_level = 5 if opening_stock > 15 else 3
    
    # Unit label based on item
    unit_label = "pcs"
    if "1L" in selected_item or "500ml" in selected_item:
        unit_label = "ltr"
    elif "1kg" in selected_item or "5kg" in selected_item or "500g" in selected_item or "100g" in selected_item or "250g" in selected_item or "120g" in selected_item or "150g" in selected_item:
        unit_label = "kg"
        
    return {
        "code_value": code,
        "name": product_name,
        "category": category,
        "unit_price": selling_price,
        "cost_price": cost_price,
        "stock_qty": opening_stock,
        "unit_label": unit_label,
        "reorder_level": reorder_level,
        "region_inferred": region
    }
