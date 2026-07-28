"""
Offline Billing & Inventory System
-----------------------------------
A self-contained Flask application. Every route here is served from this
same process on localhost - there are no calls to any third-party API,
and the app is fully usable with no internet connection at all.

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""
import io
import json
import os

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_file, flash, abort
)

import database as db
from ml import train as ml_train
from ml import predict as ml_predict
from ml import inventory_ai as ai_engine
import codegen

app = Flask(__name__)
app.secret_key = "local-offline-billing-secret"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

@app.before_request
def ensure_db():
    if not os.path.exists(db.DB_PATH):
        db.init_db()


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def next_invoice_number(conn, settings):
    prefix = settings.get("invoice_prefix", "INV")
    counter = int(settings.get("invoice_counter", "1000"))
    invoice_no = f"{prefix}-{counter}"
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = 'invoice_counter'",
        (str(counter + 1),),
    )
    return invoice_no


def low_stock_products(conn):
    return conn.execute(
        "SELECT * FROM products WHERE stock_qty <= reorder_level ORDER BY stock_qty ASC"
    ).fetchall()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    conn = db.get_connection()
    settings = get_settings(conn)

    today_row = conn.execute(
        """SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS cnt
           FROM transactions WHERE date(created_at) = date('now', 'localtime')"""
    ).fetchone()

    week_row = conn.execute(
        """SELECT COALESCE(SUM(total), 0) AS total
           FROM transactions WHERE date(created_at) >= date('now', 'localtime', '-6 days')"""
    ).fetchone()

    product_count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    low_stock = low_stock_products(conn)

    trend_rows = conn.execute(
        """SELECT date(created_at) AS d, SUM(total) AS total
           FROM transactions
           WHERE date(created_at) >= date('now', 'localtime', '-13 days')
           GROUP BY date(created_at) ORDER BY d ASC"""
    ).fetchall()
    trend = {r["d"]: r["total"] for r in trend_rows}

    recent = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT 6"
    ).fetchall()

    model_status = ml_predict.get_model_status()

    stockout_soon = []
    if product_count > 0:
        intel = ml_predict.get_intelligence(conn)
        for pid, info in intel.items():
            if info["days_to_stockout"] is not None and info["days_to_stockout"] <= 7:
                stockout_soon.append(info)
        stockout_soon.sort(key=lambda x: x["days_to_stockout"])

    conn.close()
    return render_template(
        "dashboard.html",
        settings=settings,
        today_total=today_row["total"],
        today_count=today_row["cnt"],
        week_total=week_row["total"],
        product_count=product_count,
        low_stock=low_stock,
        trend=trend,
        recent=recent,
        model_status=model_status,
        stockout_soon=stockout_soon[:5],
        active="dashboard",
    )


# ---------------------------------------------------------------------------
# Billing (POS)
# ---------------------------------------------------------------------------

@app.route("/billing")
def billing():
    conn = db.get_connection()
    settings = get_settings(conn)
    products = conn.execute("SELECT * FROM products ORDER BY name ASC").fetchall()
    conn.close()
    return render_template("billing.html", settings=settings, products=products, active="billing")


@app.route("/billing/lookup")
def billing_lookup():
    code = request.args.get("code", "").strip()
    conn = db.get_connection()
    product = conn.execute(
        "SELECT * FROM products WHERE code_value = ?", (code,)
    ).fetchone()
    conn.close()
    if not product:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, "product": dict(product)})


@app.route("/inventory/ai_lookup")
def inventory_ai_lookup():
    """Legacy endpoint – kept for backward compat. Delegates to full lookup."""
    code = request.args.get("code", "").strip()
    hint = request.args.get("hint", "").strip() or None
    if not code:
        return jsonify({"error": "No barcode provided"}), 400
    from ml.barcode_ai import analyze_barcode
    prediction = analyze_barcode(code, hint)
    if not prediction:
        return jsonify({"error": "Failed to analyze barcode"}), 500
    return jsonify(prediction)


@app.route("/inventory/barcode_lookup")
def inventory_barcode_lookup():
    """Multi-source barcode lookup with offline cache support."""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "No barcode provided"}), 400
    # Check for existing duplicate first
    conn = db.get_connection()
    existing = conn.execute(
        "SELECT id, name, stock_qty FROM products WHERE code_value = ? OR barcode_raw = ?",
        (code, code)
    ).fetchone()
    conn.close()
    if existing:
        return jsonify({
            "_duplicate": True,
            "_existing_id": existing["id"],
            "_existing_name": existing["name"],
            "_existing_stock": existing["stock_qty"],
        })
    # Perform multi-source lookup
    from ml.barcode_lookup import lookup_barcode
    result = lookup_barcode(code)
    return jsonify(result)


@app.route("/inventory/check_duplicate")
def inventory_check_duplicate():
    """Check if a barcode already exists in inventory."""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"exists": False})
    conn = db.get_connection()
    product = conn.execute(
        "SELECT id, name, stock_qty, unit_label FROM products WHERE code_value = ? OR barcode_raw = ?",
        (code, code)
    ).fetchone()
    conn.close()
    if product:
        return jsonify({"exists": True, "product": dict(product)})
    return jsonify({"exists": False})


@app.route("/inventory/stock_increase/<int:product_id>", methods=["POST"])
def inventory_stock_increase(product_id):
    """Increase stock quantity for an existing product (barcode duplicate handling)."""
    payload = request.get_json(force=True)
    qty = float(payload.get("qty", 1))
    conn = db.get_connection()
    conn.execute(
        "UPDATE products SET stock_qty = stock_qty + ?, updated_at = ? WHERE id = ?",
        (qty, db.now_iso(), product_id)
    )
    conn.execute(
        "INSERT INTO stock_adjustments (product_id, change_qty, reason, created_at) VALUES (?, ?, ?, ?)",
        (product_id, qty, "Barcode scan - stock increase", db.now_iso())
    )
    conn.commit()
    row = conn.execute("SELECT stock_qty, name FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return jsonify({"ok": True, "new_stock": row["stock_qty"], "name": row["name"]})


@app.route("/inventory/expiry_report")
def inventory_expiry_report():
    """Returns all products with expiry dates, color-coded status."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, name, expiry_date, stock_qty, category, brand FROM products WHERE expiry_date != '' AND expiry_date IS NOT NULL ORDER BY expiry_date ASC"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        status, days = db.get_expiry_status(r["expiry_date"])
        result.append({
            **dict(r),
            "expiry_status": status,
            "days_remaining": days,
        })
    return jsonify(result)


@app.route("/inventory/image_proxy")
def inventory_image_proxy():
    """Proxy-download a product image to avoid CORS issues in the browser."""
    import urllib.request
    img_url = request.args.get("url", "").strip()
    if not img_url or not img_url.startswith("http"):
        abort(400)
    try:
        req = urllib.request.Request(img_url, headers={"User-Agent": "LedgerInventory/2.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        abort(404)
    from flask import Response
    return Response(data, mimetype=content_type)


@app.route("/ai/recommendations")
def ai_recommendations():
    """AI product movement recommendations: fast movers, slow movers, dead stock."""
    conn = db.get_connection()
    days = int(request.args.get("days", 30))

    # Fast movers: most sales in period
    fast = conn.execute(
        f"""SELECT ti.product_name, ti.product_id, SUM(ti.quantity) AS qty_sold,
               SUM(ti.line_total) AS revenue
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            WHERE date(t.created_at) >= date('now','localtime','-{days} days')
            GROUP BY ti.product_id ORDER BY qty_sold DESC LIMIT 10"""
    ).fetchall()

    # Slow movers: products with low/no sales but positive stock
    all_prods = conn.execute("SELECT id, name, stock_qty, category FROM products WHERE stock_qty > 0").fetchall()
    sold_ids = {r["product_id"] for r in fast if r["product_id"]}
    slow = [dict(p) for p in all_prods if p["id"] not in sold_ids][:10]

    # Dead stock: no movement in dead_stock_days
    settings = get_settings(conn)
    dead_days = int(settings.get("dead_stock_days", 60))
    dead = conn.execute(
        f"""SELECT p.id, p.name, p.stock_qty, p.category,
               MAX(t.created_at) AS last_sale
            FROM products p
            LEFT JOIN transaction_items ti ON ti.product_id = p.id
            LEFT JOIN transactions t ON t.id = ti.transaction_id
            WHERE p.stock_qty > 0
            GROUP BY p.id
            HAVING last_sale IS NULL OR date(last_sale) < date('now','localtime','-{dead_days} days')
            ORDER BY p.stock_qty DESC LIMIT 10"""
    ).fetchall()

    # Restock suggestions: low stock items
    restock = conn.execute(
        "SELECT id, name, stock_qty, reorder_level FROM products WHERE stock_qty <= reorder_level ORDER BY stock_qty ASC LIMIT 10"
    ).fetchall()

    conn.close()
    return jsonify({
        "fast_movers": [dict(r) for r in fast],
        "slow_movers": slow,
        "dead_stock": [dict(r) for r in dead],
        "restock_needed": [dict(r) for r in restock],
    })



@app.route("/billing/search")
def billing_search():
    q = request.args.get("q", "").strip()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM products WHERE name LIKE ? ORDER BY name ASC LIMIT 15",
        (f"%{q}%",),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/billing/checkout", methods=["POST"])
def billing_checkout():
    payload = request.get_json(force=True)
    items = payload.get("items", [])
    discount = float(payload.get("discount", 0) or 0)
    tax_rate = float(payload.get("tax_rate", 0) or 0)
    payment_method = payload.get("payment_method", "Cash")
    customer_name = payload.get("customer_name", "")

    if not items:
        return jsonify({"error": "Cart is empty."}), 400

    conn = db.get_connection()
    settings = get_settings(conn)

    subtotal = 0.0
    validated_items = []
    for item in items:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (item["product_id"],)
        ).fetchone()
        if not product:
            continue
        qty = float(item["quantity"])
        if qty <= 0:
            continue
        if qty > product["stock_qty"]:
            conn.close()
            return jsonify(
                {"error": f"Only {product['stock_qty']:g} {product['unit_label']} of "
                          f"{product['name']} left in stock."}
            ), 400
        line_total = round(qty * product["unit_price"], 2)
        subtotal += line_total
        validated_items.append((product, qty, line_total))

    if not validated_items:
        conn.close()
        return jsonify({"error": "No valid items in cart."}), 400

    tax_amount = round((subtotal - discount) * (tax_rate / 100.0), 2)
    total = round(subtotal - discount + tax_amount, 2)
    invoice_no = next_invoice_number(conn, settings)
    created_at = db.now_iso()

    cur = conn.execute(
        """INSERT INTO transactions
           (invoice_no, created_at, subtotal, discount, tax, total, payment_method, customer_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_no, created_at, subtotal, discount, tax_amount, total, payment_method, customer_name),
    )
    txn_id = cur.lastrowid

    for product, qty, line_total in validated_items:
        conn.execute(
            """INSERT INTO transaction_items
               (transaction_id, product_id, product_name, quantity, unit_price, line_total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (txn_id, product["id"], product["name"], qty, product["unit_price"], line_total),
        )
        conn.execute(
            "UPDATE products SET stock_qty = stock_qty - ?, updated_at = ? WHERE id = ?",
            (qty, db.now_iso(), product["id"]),
        )

    conn.commit()
    conn.close()
    return jsonify({"invoice_no": invoice_no, "transaction_id": txn_id})


@app.route("/receipt/<invoice_no>")
def receipt(invoice_no):
    conn = db.get_connection()
    settings = get_settings(conn)
    txn = conn.execute(
        "SELECT * FROM transactions WHERE invoice_no = ?", (invoice_no,)
    ).fetchone()
    if not txn:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM transaction_items WHERE transaction_id = ?", (txn["id"],)
    ).fetchall()
    conn.close()
    return render_template("receipt_print.html", settings=settings, txn=txn, items=items)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@app.route("/inventory")
def inventory():
    conn = db.get_connection()
    settings = get_settings(conn)
    q = request.args.get("q", "").strip()
    if q:
        products = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR code_value LIKE ? ORDER BY name ASC",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        products = conn.execute("SELECT * FROM products ORDER BY name ASC").fetchall()
    conn.close()
    return render_template(
        "inventory.html", settings=settings, products=products, query=q, active="inventory"
    )


@app.route("/inventory/add", methods=["GET", "POST"])
def inventory_add():
    conn = db.get_connection()
    settings = get_settings(conn)

    if request.method == "POST":
        form = request.form
        code_value = form.get("code_value", "").strip() or None
        try:
            cur = conn.execute(
                """INSERT INTO products
                   (name, category, code_value, code_type, unit_price, cost_price,
                    stock_qty, unit_label, reorder_level, created_at, updated_at,
                    brand, sub_category, description, image_url, expiry_date, mfg_date,
                    batch_number, serial_number, weight, volume, mrp, manufacturer,
                    country_of_origin, ingredients, nutritional_info, dimensions,
                    color, size_label, warranty_info, supplier_details, barcode_raw, source_db)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    form.get("name", "").strip(),
                    form.get("category", "General").strip() or "General",
                    code_value,
                    form.get("code_type", settings.get("preferred_code_type", "CODE128")),
                    float(form.get("unit_price", 0) or 0),
                    float(form.get("cost_price", 0) or 0),
                    float(form.get("stock_qty", 0) or 0),
                    form.get("unit_label", "pcs").strip() or "pcs",
                    float(form.get("reorder_level", 5) or 5),
                    db.now_iso(), db.now_iso(),
                    form.get("brand", "").strip(),
                    form.get("sub_category", "").strip(),
                    form.get("description", "").strip(),
                    form.get("image_url", "").strip(),
                    form.get("expiry_date", "").strip(),
                    form.get("mfg_date", "").strip(),
                    form.get("batch_number", "").strip(),
                    form.get("serial_number", "").strip(),
                    form.get("weight", "").strip(),
                    form.get("volume", "").strip(),
                    float(form.get("mrp", 0) or 0),
                    form.get("manufacturer", "").strip(),
                    form.get("country_of_origin", "").strip(),
                    form.get("ingredients", "").strip(),
                    form.get("nutritional_info", "").strip(),
                    form.get("dimensions", "").strip(),
                    form.get("color", "").strip(),
                    form.get("size_label", "").strip(),
                    form.get("warranty_info", "").strip(),
                    form.get("supplier_details", "").strip(),
                    form.get("barcode_raw", code_value or "").strip(),
                    form.get("source_db", "").strip(),
                ),
            )
            product_id = cur.lastrowid
            # Track expiry alert if expiry date provided
            expiry_date = form.get("expiry_date", "").strip()
            if expiry_date and product_id:
                status, _ = db.get_expiry_status(expiry_date)
                conn.execute(
                    "INSERT OR REPLACE INTO expiry_alerts (product_id, expiry_date, status, notified, created_at) VALUES (?, ?, ?, 0, ?)",
                    (product_id, expiry_date, status or "fresh", db.now_iso())
                )
            conn.commit()
            conn.close()
            flash("Item added to inventory.", "success")
            return redirect(url_for("inventory"))
        except db.sqlite3.IntegrityError:
            conn.close()
            flash("That barcode/QR value is already assigned to another item.", "error")
            return redirect(url_for("inventory_add"))

    conn.close()
    return render_template("add_item.html", settings=settings, product=None, active="inventory")


@app.route("/inventory/edit/<int:product_id>", methods=["GET", "POST"])
def inventory_edit(product_id):
    conn = db.get_connection()
    settings = get_settings(conn)
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        conn.close()
        abort(404)

    if request.method == "POST":
        form = request.form
        code_value = form.get("code_value", "").strip() or None
        try:
            conn.execute(
                """UPDATE products SET name=?, category=?, code_value=?, code_type=?,
                   unit_price=?, cost_price=?, stock_qty=?, unit_label=?, reorder_level=?,
                   updated_at=?,
                   brand=?, sub_category=?, description=?, image_url=?,
                   expiry_date=?, mfg_date=?, batch_number=?, serial_number=?,
                   weight=?, volume=?, mrp=?, manufacturer=?, country_of_origin=?,
                   ingredients=?, nutritional_info=?, dimensions=?, color=?,
                   size_label=?, warranty_info=?, supplier_details=?, barcode_raw=?, source_db=?
                   WHERE id=?""",
                (
                    form.get("name", "").strip(),
                    form.get("category", "General").strip() or "General",
                    code_value,
                    form.get("code_type", "CODE128"),
                    float(form.get("unit_price", 0) or 0),
                    float(form.get("cost_price", 0) or 0),
                    float(form.get("stock_qty", 0) or 0),
                    form.get("unit_label", "pcs").strip() or "pcs",
                    float(form.get("reorder_level", 5) or 5),
                    db.now_iso(),
                    form.get("brand", "").strip(),
                    form.get("sub_category", "").strip(),
                    form.get("description", "").strip(),
                    form.get("image_url", "").strip(),
                    form.get("expiry_date", "").strip(),
                    form.get("mfg_date", "").strip(),
                    form.get("batch_number", "").strip(),
                    form.get("serial_number", "").strip(),
                    form.get("weight", "").strip(),
                    form.get("volume", "").strip(),
                    float(form.get("mrp", 0) or 0),
                    form.get("manufacturer", "").strip(),
                    form.get("country_of_origin", "").strip(),
                    form.get("ingredients", "").strip(),
                    form.get("nutritional_info", "").strip(),
                    form.get("dimensions", "").strip(),
                    form.get("color", "").strip(),
                    form.get("size_label", "").strip(),
                    form.get("warranty_info", "").strip(),
                    form.get("supplier_details", "").strip(),
                    form.get("barcode_raw", code_value or "").strip(),
                    form.get("source_db", "").strip(),
                    product_id,
                ),
            )
            # Update expiry alert
            expiry_date = form.get("expiry_date", "").strip()
            if expiry_date:
                status, _ = db.get_expiry_status(expiry_date)
                conn.execute(
                    "INSERT OR REPLACE INTO expiry_alerts (product_id, expiry_date, status, notified, created_at) VALUES (?, ?, ?, 0, ?)",
                    (product_id, expiry_date, status or "fresh", db.now_iso())
                )
            conn.commit()
            conn.close()
            flash("Item updated.", "success")
            return redirect(url_for("inventory"))
        except db.sqlite3.IntegrityError:
            conn.close()
            flash("That barcode/QR value is already assigned to another item.", "error")
            return redirect(url_for("inventory_edit", product_id=product_id))

    conn.close()
    return render_template("add_item.html", settings=settings, product=product, active="inventory")


@app.route("/inventory/delete/<int:product_id>", methods=["POST"])
def inventory_delete(product_id):
    conn = db.get_connection()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    flash("Item removed.", "success")
    return redirect(url_for("inventory"))


@app.route("/inventory/adjust/<int:product_id>", methods=["POST"])
def inventory_adjust(product_id):
    payload = request.get_json(force=True)
    change = float(payload.get("change_qty", 0))
    reason = payload.get("reason", "Manual adjustment")
    conn = db.get_connection()
    conn.execute(
        "UPDATE products SET stock_qty = stock_qty + ?, updated_at = ? WHERE id = ?",
        (change, db.now_iso(), product_id),
    )
    conn.execute(
        "INSERT INTO stock_adjustments (product_id, change_qty, reason, created_at) VALUES (?, ?, ?, ?)",
        (product_id, change, reason, db.now_iso()),
    )
    conn.commit()
    row = conn.execute("SELECT stock_qty FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return jsonify({"stock_qty": row["stock_qty"]})



# ---------------------------------------------------------------------------
# Fast Scanner Page
# ---------------------------------------------------------------------------

@app.route("/scanner")
def scanner_page():
    conn = db.get_connection()
    settings = get_settings(conn)
    conn.close()
    return render_template("scanner_page.html", settings=settings, active="scanner")


# ---------------------------------------------------------------------------
# Barcode / QR generator
# ---------------------------------------------------------------------------

@app.route("/barcode")
def barcode_page():
    conn = db.get_connection()
    settings = get_settings(conn)
    products = conn.execute("SELECT id, name, code_value FROM products ORDER BY name ASC").fetchall()
    conn.close()
    return render_template(
        "barcode_generator.html", settings=settings, products=products,
        code_types=codegen.SUPPORTED_TYPES, active="barcode"
    )


@app.route("/barcode/preview")
def barcode_preview():
    code_type = request.args.get("type", "CODE128").upper()
    value = request.args.get("value", "").strip()
    if not value:
        # Auto-generate a suitable code — no DB call needed
        value = codegen.random_code(12, digits_only=(code_type == "EAN13"))
    try:
        img, final_value = codegen.generate_image(code_type, value)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    response = send_file(buf, mimetype="image/png")
    response.headers["X-Barcode-Value"] = final_value
    return response


@app.route("/barcode/create_product", methods=["POST"])
def barcode_create_product():
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    value = (payload.get("value") or "").strip()
    code_type = payload.get("code_type", "CODE128")

    if not name:
        return jsonify({"error": "Product name is required."}), 400
    if not value:
        return jsonify({"error": "Generate a code (with an explicit value) before saving."}), 400

    conn = db.get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO products
               (name, category, code_value, code_type, unit_price, cost_price,
                stock_qty, unit_label, reorder_level, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (payload.get("category") or "General").strip() or "General",
                value,
                code_type,
                float(payload.get("unit_price", 0) or 0),
                float(payload.get("cost_price", 0) or 0),
                float(payload.get("stock_qty", 0) or 0),
                (payload.get("unit_label") or "pcs").strip() or "pcs",
                float(payload.get("reorder_level", 5) or 5),
                db.now_iso(),
                db.now_iso(),
            ),
        )
        conn.commit()
        product_id = cur.lastrowid
    except db.sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "That code is already assigned to another item."}), 400
    conn.close()
    return jsonify({"ok": True, "product_id": product_id, "name": name})


@app.route("/barcode/assign", methods=["POST"])
def barcode_assign():
    payload = request.get_json(force=True)
    product_id = payload.get("product_id")
    code_type = payload.get("code_type", "CODE128")
    value = payload.get("value", "").strip()
    if not product_id or not value:
        return jsonify({"error": "Product and value are required."}), 400
    conn = db.get_connection()
    try:
        conn.execute(
            "UPDATE products SET code_value = ?, code_type = ?, updated_at = ? WHERE id = ?",
            (value, code_type, db.now_iso(), product_id),
        )
        conn.commit()
    except db.sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "That code is already assigned to another item."}), 400
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.route("/alerts")
def alerts():
    conn = db.get_connection()
    settings = get_settings(conn)
    low_stock = low_stock_products(conn)
    product_count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    intel = ml_predict.get_intelligence(conn) if product_count else {}
    model_status = ml_predict.get_model_status()

    predicted = [v for v in intel.values() if v["days_to_stockout"] is not None]
    predicted.sort(key=lambda x: x["days_to_stockout"])

    conn.close()
    return render_template(
        "alerts.html",
        settings=settings,
        low_stock=low_stock,
        predicted=predicted,
        model_status=model_status,
        active="alerts",
    )


@app.route("/ml/retrain", methods=["POST"])
def ml_retrain():
    conn = db.get_connection()
    result = ml_train.train_model(conn)
    conn.close()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route("/reports")
def reports():
    conn = db.get_connection()
    settings = get_settings(conn)
    conn.close()
    return render_template("reports.html", settings=settings, active="reports")


@app.route("/reports/data")
def reports_data():
    days = int(request.args.get("days", 30))
    conn = db.get_connection()

    trend_rows = conn.execute(
        f"""SELECT date(created_at) AS d, SUM(total) AS total, COUNT(*) AS cnt
            FROM transactions
            WHERE date(created_at) >= date('now', 'localtime', '-{days - 1} days')
            GROUP BY date(created_at) ORDER BY d ASC"""
    ).fetchall()

    top_products = conn.execute(
        f"""SELECT ti.product_name, SUM(ti.quantity) AS qty, SUM(ti.line_total) AS revenue
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            WHERE date(t.created_at) >= date('now', 'localtime', '-{days - 1} days')
            GROUP BY ti.product_name ORDER BY revenue DESC LIMIT 8"""
    ).fetchall()

    category_rows = conn.execute(
        f"""SELECT p.category AS category, SUM(ti.line_total) AS revenue
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            LEFT JOIN products p ON p.id = ti.product_id
            WHERE date(t.created_at) >= date('now', 'localtime', '-{days - 1} days')
            GROUP BY p.category ORDER BY revenue DESC"""
    ).fetchall()

    totals = conn.execute(
        f"""SELECT COALESCE(SUM(total),0) AS revenue, COUNT(*) AS orders,
                   COALESCE(AVG(total),0) AS avg_order
            FROM transactions
            WHERE date(created_at) >= date('now', 'localtime', '-{days - 1} days')"""
    ).fetchone()

    conn.close()
    return jsonify({
        "trend": [dict(r) for r in trend_rows],
        "top_products": [dict(r) for r in top_products],
        "categories": [dict(r) for r in category_rows],
        "totals": dict(totals),
    })


@app.route("/reports/export")
def reports_export():
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT t.invoice_no, t.created_at, ti.product_name, ti.quantity,
                  ti.unit_price, ti.line_total, t.payment_method
           FROM transaction_items ti
           JOIN transactions t ON t.id = ti.transaction_id
           ORDER BY t.created_at DESC"""
    ).fetchall()
    conn.close()

    output = io.StringIO()
    output.write("Invoice No,Date,Product,Quantity,Unit Price,Line Total,Payment Method\n")
    for r in rows:
        output.write(
            f'{r["invoice_no"]},{r["created_at"]},"{r["product_name"]}",'
            f'{r["quantity"]},{r["unit_price"]},{r["line_total"]},{r["payment_method"]}\n'
        )
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(
        mem, mimetype="text/csv", as_attachment=True, download_name="sales_export.csv"
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    conn = db.get_connection()
    if request.method == "POST":
        form = request.form
        for key in [
            "business_name", "business_address", "business_phone",
            "currency_symbol", "default_tax_rate", "low_stock_default",
            "preferred_code_type", "invoice_prefix",
        ]:
            if key in form:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, form.get(key, "")),
                )
        conn.commit()
        flash("Settings saved.", "success")
        conn.close()
        return redirect(url_for("settings_page"))

    settings = get_settings(conn)
    conn.close()
    return render_template("settings.html", settings=settings, active="settings")


# ---------------------------------------------------------------------------
# AI Command Center
# ---------------------------------------------------------------------------

@app.route("/ai")
def ai_center():
    conn = db.get_connection()
    settings = get_settings(conn)
    conn.close()
    return render_template("ai_center.html", settings=settings, active="ai")


@app.route("/ai/report")
def ai_report():
    conn = db.get_connection()
    settings = get_settings(conn)
    report = ai_engine.run_full_analysis(conn, settings)
    conn.close()
    return jsonify(report)


@app.route("/ai/nlp", methods=["POST"])
def ai_nlp():
    payload = request.get_json(force=True)
    query = payload.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided."}), 400
    conn = db.get_connection()
    settings = get_settings(conn)
    result = ai_engine.parse_natural_language(query, conn, settings)
    # Log NLP action
    conn.execute(
        "INSERT INTO ai_agent_log (action_type, summary, details, created_at) VALUES (?, ?, ?, ?)",
        ("nlp", f"NLP: {query}", json.dumps(result.get('response', '')), db.now_iso())
    )
    conn.commit()
    conn.close()
    return jsonify(result)


@app.route("/ai/restock", methods=["POST"])
def ai_restock():
    conn = db.get_connection()
    settings = get_settings(conn)
    result = ai_engine.generate_purchase_orders(conn, settings)
    conn.close()
    return jsonify(result)


@app.route("/ai/po/<int:po_id>")
def ai_po_detail(po_id):
    conn = db.get_connection()
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po:
        conn.close()
        return jsonify({"error": "Purchase order not found."}), 404
    items = conn.execute(
        "SELECT * FROM purchase_order_items WHERE po_id = ?", (po_id,)
    ).fetchall()
    conn.close()
    return jsonify({"po": dict(po), "items": [dict(i) for i in items]})


@app.route("/ai/po/<int:po_id>/print")
def ai_po_print(po_id):
    conn = db.get_connection()
    settings = get_settings(conn)
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM purchase_order_items WHERE po_id = ?", (po_id,)
    ).fetchall()
    conn.close()
    return render_template("po_print.html", settings=settings, po=po, items=items)


@app.route("/ai/warehouses")
def ai_warehouses():
    conn = db.get_connection()
    result = ai_engine.get_warehouse_optimization(conn)
    conn.close()
    return jsonify(result)


@app.route("/ai/warehouses/transfer", methods=["POST"])
def ai_warehouse_transfer():
    payload = request.get_json(force=True)
    product_id = payload.get("product_id")
    from_wh = payload.get("from_warehouse_id")
    to_wh = payload.get("to_warehouse_id")
    qty = float(payload.get("quantity", 0))
    reason = payload.get("reason", "AI-optimized transfer")

    if not all([product_id, from_wh, to_wh]) or qty <= 0:
        return jsonify({"error": "Invalid transfer parameters."}), 400

    conn = db.get_connection()
    # Deduct from source
    conn.execute(
        "UPDATE product_warehouse SET stock_qty = stock_qty - ? WHERE product_id = ? AND warehouse_id = ?",
        (qty, product_id, from_wh)
    )
    # Add to destination (insert if not exists)
    conn.execute(
        """INSERT INTO product_warehouse (product_id, warehouse_id, stock_qty) VALUES (?, ?, ?)
           ON CONFLICT(product_id, warehouse_id) DO UPDATE SET stock_qty = stock_qty + ?""",
        (product_id, to_wh, qty, qty)
    )
    # Log transfer
    conn.execute(
        "INSERT INTO warehouse_transfers (product_id, from_warehouse, to_warehouse, quantity, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (product_id, from_wh, to_wh, qty, reason, db.now_iso())
    )
    conn.execute(
        "INSERT INTO ai_agent_log (action_type, summary, details, created_at) VALUES (?, ?, ?, ?)",
        ("transfer", f"Transferred {qty} units", json.dumps(payload), db.now_iso())
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/ai/agent-log")
def ai_agent_log():
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM ai_agent_log ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def find_available_port(preferred_port=5000):
    import socket
    port = preferred_port
    while port < preferred_port + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return preferred_port


if __name__ == "__main__":
    import threading
    import time
    import webbrowser

    db.init_db()
    
    port = int(os.environ.get("PORT", 0))
    if not port:
        port = find_available_port(5000)

    url = f"http://127.0.0.1:{port}/"
    print("\n" + "=" * 60)
    print(f"  LEDGER SMART BILLING SYSTEM IS READY!")
    print(f"  URL: {url}")
    if port != 5000:
        print(f"  [NOTE] Port 5000 was in use by another app.")
        print(f"         Automatically running on port {port} instead.")
    print("=" * 60 + "\n")

    def auto_open_browser():
        time.sleep(1.2)
        webbrowser.open(url)

    threading.Thread(target=auto_open_browser, daemon=True).start()
    app.run(debug=False, host="127.0.0.1", port=port)

