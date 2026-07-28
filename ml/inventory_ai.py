"""
Autonomous AI Inventory Intelligence Engine
--------------------------------------------
Runs 100% offline. No cloud APIs. Uses scikit-learn, pandas, numpy,
and SQLite to power 9 intelligent inventory management modules:

1. Demand Forecasting
2. Predictive Stock Detection
3. Restocking Recommendations
4. Anomaly & Fraud Detection
5. Natural Language Inventory Actions
6. Multi-Warehouse Optimization
7. Purchase Order Generation
8. Dead Stock & Slow-Moving Detection
9. Unified Intelligence Report
"""
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

from ml.predict import get_intelligence


# ── 1. Demand Forecasting ─────────────────────────────────────────────────

def get_demand_forecast(conn):
    """
    Returns per-product demand predictions for next day, week, and month.
    Uses the existing ML model / heuristic pipeline from predict.py.
    """
    intel = get_intelligence(conn)
    forecasts = []
    for pid, info in intel.items():
        daily = info["predicted_daily_demand"]
        forecasts.append({
            "product_id": pid,
            "name": info["name"],
            "stock_qty": info["stock_qty"],
            "daily_demand": daily,
            "weekly_demand": round(daily * 7, 1),
            "monthly_demand": round(daily * 30, 1),
            "method": info["method"],
        })
    # Sort by daily demand descending
    forecasts.sort(key=lambda x: x["daily_demand"], reverse=True)
    return forecasts


# ── 2. Predictive Stock Detection ─────────────────────────────────────────

def get_stockout_predictions(conn):
    """
    Classifies each product into urgency tiers:
      - critical: ≤3 days to stockout
      - warning:  ≤7 days to stockout
      - caution:  ≤14 days to stockout
      - safe:     >14 days or no demand
    """
    intel = get_intelligence(conn)
    predictions = []
    for pid, info in intel.items():
        days = info["days_to_stockout"]
        if days is None:
            urgency = "safe"
        elif days <= 3:
            urgency = "critical"
        elif days <= 7:
            urgency = "warning"
        elif days <= 14:
            urgency = "caution"
        else:
            urgency = "safe"

        predictions.append({
            "product_id": pid,
            "name": info["name"],
            "stock_qty": info["stock_qty"],
            "reorder_level": info["reorder_level"],
            "days_to_stockout": days,
            "daily_demand": info["predicted_daily_demand"],
            "urgency": urgency,
        })
    # Sort critical first
    order = {"critical": 0, "warning": 1, "caution": 2, "safe": 3}
    predictions.sort(key=lambda x: (order.get(x["urgency"], 4), x["days_to_stockout"] or 9999))
    return predictions


# ── 3. Restocking Recommendations ─────────────────────────────────────────

def get_restock_recommendations(conn, settings):
    """
    Calculates optimal reorder quantities using:
      reorder_qty = max(0, (daily_demand × lead_time × safety_multiplier) + reorder_level − current_stock)
    Only recommends restocking for products below or near reorder level.
    """
    lead_time = float(settings.get("lead_time_days", "3"))
    safety_mult = float(settings.get("safety_stock_multiplier", "1.5"))
    intel = get_intelligence(conn)
    recommendations = []

    for pid, info in intel.items():
        daily = info["predicted_daily_demand"]
        stock = info["stock_qty"]
        reorder_lvl = info["reorder_level"]

        # Calculate safety stock
        safety_stock = daily * lead_time * safety_mult
        target_stock = safety_stock + reorder_lvl
        reorder_qty = max(0, round(target_stock - stock, 1))

        if reorder_qty > 0 or stock <= reorder_lvl:
            # Fetch cost price for total calculation
            row = conn.execute(
                "SELECT cost_price, category FROM products WHERE id = ?", (pid,)
            ).fetchone()
            cost = row["cost_price"] if row else 0
            category = row["category"] if row else "General"

            if reorder_qty == 0:
                reorder_qty = max(1, reorder_lvl)

            recommendations.append({
                "product_id": pid,
                "name": info["name"],
                "category": category,
                "current_stock": stock,
                "reorder_level": reorder_lvl,
                "daily_demand": daily,
                "recommended_qty": reorder_qty,
                "estimated_cost": round(reorder_qty * cost, 2),
                "unit_cost": cost,
                "days_to_stockout": info["days_to_stockout"],
            })

    recommendations.sort(key=lambda x: x["days_to_stockout"] or 9999)
    return recommendations


# ── 4. Anomaly & Fraud Detection ──────────────────────────────────────────

def detect_anomalies(conn):
    """
    Uses z-score analysis on:
      - Daily sales volume per product (sudden spikes)
      - Transaction discount amounts (unusually high discounts)
      - Stock adjustments (unexplained shrinkage)
    Returns a list of anomaly alerts with severity.
    """
    anomalies = []

    # ---- Sales volume anomalies ----
    sales_df = pd.read_sql_query("""
        SELECT ti.product_id, p.name, date(t.created_at) AS sale_date,
               SUM(ti.quantity) AS daily_qty
        FROM transaction_items ti
        JOIN transactions t ON t.id = ti.transaction_id
        JOIN products p ON p.id = ti.product_id
        WHERE ti.product_id IS NOT NULL
          AND date(t.created_at) >= date('now', 'localtime', '-60 days')
        GROUP BY ti.product_id, date(t.created_at)
    """, conn)

    if not sales_df.empty:
        for pid, group in sales_df.groupby("product_id"):
            if len(group) < 3:
                continue
            mean_qty = group["daily_qty"].mean()
            std_qty = group["daily_qty"].std()
            if std_qty == 0:
                continue
            name = group["name"].iloc[0]
            for _, row in group.iterrows():
                z = (row["daily_qty"] - mean_qty) / std_qty
                if z > 2.5:
                    anomalies.append({
                        "type": "sales_spike",
                        "severity": "high" if z > 3.5 else "medium",
                        "product_id": int(pid),
                        "product_name": name,
                        "date": row["sale_date"],
                        "detail": f"Sold {row['daily_qty']:.0f} units (avg: {mean_qty:.1f}, z-score: {z:.1f})",
                        "z_score": round(z, 2),
                    })

    # ---- Discount anomalies ----
    disc_df = pd.read_sql_query("""
        SELECT id, invoice_no, date(created_at) AS txn_date,
               subtotal, discount, total
        FROM transactions
        WHERE discount > 0
          AND date(created_at) >= date('now', 'localtime', '-60 days')
    """, conn)

    if not disc_df.empty and len(disc_df) >= 3:
        disc_df["disc_pct"] = (disc_df["discount"] / disc_df["subtotal"].replace(0, 1)) * 100
        mean_disc = disc_df["disc_pct"].mean()
        std_disc = disc_df["disc_pct"].std()
        if std_disc > 0:
            for _, row in disc_df.iterrows():
                z = (row["disc_pct"] - mean_disc) / std_disc
                if z > 2.0:
                    anomalies.append({
                        "type": "high_discount",
                        "severity": "high" if z > 3.0 else "medium",
                        "product_id": None,
                        "product_name": f"Invoice {row['invoice_no']}",
                        "date": row["txn_date"],
                        "detail": f"Discount {row['disc_pct']:.1f}% (avg: {mean_disc:.1f}%, z-score: {z:.1f})",
                        "z_score": round(z, 2),
                    })

    # ---- Stock shrinkage anomalies ----
    adj_df = pd.read_sql_query("""
        SELECT sa.product_id, p.name, sa.change_qty, sa.reason,
               date(sa.created_at) AS adj_date
        FROM stock_adjustments sa
        JOIN products p ON p.id = sa.product_id
        WHERE sa.change_qty < 0
          AND date(sa.created_at) >= date('now', 'localtime', '-60 days')
    """, conn)

    if not adj_df.empty and len(adj_df) >= 2:
        mean_adj = adj_df["change_qty"].mean()
        std_adj = adj_df["change_qty"].std()
        if std_adj > 0:
            for _, row in adj_df.iterrows():
                z = (row["change_qty"] - mean_adj) / std_adj
                if z < -2.0:  # Unusually large negative adjustment
                    anomalies.append({
                        "type": "stock_shrinkage",
                        "severity": "high" if z < -3.0 else "medium",
                        "product_id": int(row["product_id"]),
                        "product_name": row["name"],
                        "date": row["adj_date"],
                        "detail": f"Removed {abs(row['change_qty']):.0f} units — {row['reason'] or 'no reason given'}",
                        "z_score": round(abs(z), 2),
                    })

    # Sort by date descending, then severity
    sev_order = {"high": 0, "medium": 1, "low": 2}
    anomalies.sort(key=lambda x: (sev_order.get(x["severity"], 3), x["date"] or ""), reverse=False)
    return anomalies


# ── 5. Dead Stock & Slow-Moving Detection ─────────────────────────────────

def detect_dead_slow_stock(conn, settings):
    """
    Dead stock:    Products with ZERO sales in the last `dead_stock_days` days.
    Slow-moving:   Products sold less than their average daily demand × 0.3 threshold
                   over the last `slow_moving_days` days.
    """
    dead_days = int(settings.get("dead_stock_days", "60"))
    slow_days = int(settings.get("slow_moving_days", "30"))

    # All products
    products = conn.execute("SELECT id, name, stock_qty, cost_price, category FROM products").fetchall()

    # Sales in dead_stock window
    sales = pd.read_sql_query(f"""
        SELECT ti.product_id, SUM(ti.quantity) AS total_sold
        FROM transaction_items ti
        JOIN transactions t ON t.id = ti.transaction_id
        WHERE date(t.created_at) >= date('now', 'localtime', '-{dead_days} days')
        GROUP BY ti.product_id
    """, conn)
    sold_map = dict(zip(sales["product_id"], sales["total_sold"])) if not sales.empty else {}

    # Sales in slow_moving window
    slow_sales = pd.read_sql_query(f"""
        SELECT ti.product_id, SUM(ti.quantity) AS total_sold
        FROM transaction_items ti
        JOIN transactions t ON t.id = ti.transaction_id
        WHERE date(t.created_at) >= date('now', 'localtime', '-{slow_days} days')
        GROUP BY ti.product_id
    """, conn)
    slow_map = dict(zip(slow_sales["product_id"], slow_sales["total_sold"])) if not slow_sales.empty else {}

    dead_stock = []
    slow_moving = []

    for p in products:
        pid = p["id"]
        total_sold_dead = sold_map.get(pid, 0)
        total_sold_slow = slow_map.get(pid, 0)
        stock_val = p["stock_qty"] * p["cost_price"]

        if total_sold_dead == 0 and p["stock_qty"] > 0:
            dead_stock.append({
                "product_id": pid,
                "name": p["name"],
                "category": p["category"],
                "stock_qty": p["stock_qty"],
                "stock_value": round(stock_val, 2),
                "days_checked": dead_days,
                "units_sold": 0,
            })
        elif total_sold_slow > 0 and p["stock_qty"] > 0:
            avg_daily = total_sold_slow / slow_days
            # Slow-moving: selling less than 0.3 units per day on average
            if avg_daily < 0.3:
                slow_moving.append({
                    "product_id": pid,
                    "name": p["name"],
                    "category": p["category"],
                    "stock_qty": p["stock_qty"],
                    "stock_value": round(stock_val, 2),
                    "days_checked": slow_days,
                    "units_sold": total_sold_slow,
                    "avg_daily": round(avg_daily, 3),
                })

    dead_stock.sort(key=lambda x: x["stock_value"], reverse=True)
    slow_moving.sort(key=lambda x: x["avg_daily"])
    return {"dead_stock": dead_stock, "slow_moving": slow_moving}


# ── 6. Natural Language Inventory Actions ─────────────────────────────────

NLP_PATTERNS = [
    # Stock queries
    (r"(show|list|get|what).*(low\s*stock|running\s*out|stock\s*out)", "low_stock"),
    (r"(show|list|get|what).*(dead\s*stock|dead\s*inventory)", "dead_stock"),
    (r"(show|list|get|what).*(slow\s*mov)", "slow_moving"),
    (r"(show|list|get|what).*(critical|urgent)", "critical_stock"),
    (r"(show|list|get|what).*(anomal|fraud|suspicious)", "anomalies"),
    # Sales queries
    (r"(sales|revenue|total).*(today|this\s*day)", "sales_today"),
    (r"(sales|revenue|total).*(this\s*week|week)", "sales_week"),
    (r"(sales|revenue|total).*(this\s*month|month)", "sales_month"),
    # Demand
    (r"(top|best|most).*(sell|sold|popular|demand)", "top_selling"),
    (r"(demand|forecast).*(predict|next|tomorrow|future)", "demand_forecast"),
    # Actions
    (r"(restock|purchase\s*order|order|re-?order)", "restock"),
    (r"(check|run|show).*(health|status|overview)", "health_check"),
    (r"(warehouse|location|transfer)", "warehouse_info"),
    (r"(how\s*many|stock|quantity|count).+(\w+)", "product_stock_query"),
    # Catch-all help
    (r"(help|what\s*can|commands)", "help"),
]


def parse_natural_language(query, conn, settings):
    """
    Rule-based NLP parser. Matches the user's plain-English query to a
    structured action and executes it immediately, returning the result.
    """
    query_lower = query.strip().lower()
    matched_action = None

    for pattern, action in NLP_PATTERNS:
        if re.search(pattern, query_lower):
            matched_action = action
            break

    if not matched_action:
        # Try product name search fallback
        words = query_lower.split()
        if len(words) >= 1:
            matched_action = "product_search"

    result = {"action": matched_action, "query": query, "response": "", "data": None}

    if matched_action == "low_stock":
        rows = conn.execute(
            "SELECT id, name, stock_qty, reorder_level FROM products WHERE stock_qty <= reorder_level ORDER BY stock_qty ASC"
        ).fetchall()
        result["data"] = [dict(r) for r in rows]
        result["response"] = f"Found {len(rows)} product(s) at or below reorder level." if rows else "All products are above reorder levels. ✓"

    elif matched_action == "dead_stock":
        ds = detect_dead_slow_stock(conn, settings)
        result["data"] = ds["dead_stock"]
        result["response"] = f"Found {len(ds['dead_stock'])} dead stock item(s) with zero sales."

    elif matched_action == "slow_moving":
        ds = detect_dead_slow_stock(conn, settings)
        result["data"] = ds["slow_moving"]
        result["response"] = f"Found {len(ds['slow_moving'])} slow-moving product(s)."

    elif matched_action == "critical_stock":
        preds = get_stockout_predictions(conn)
        critical = [p for p in preds if p["urgency"] == "critical"]
        result["data"] = critical
        result["response"] = f"{len(critical)} product(s) are critically low and may stock out within 3 days!" if critical else "No products are in critical stock status. ✓"

    elif matched_action == "anomalies":
        anomalies = detect_anomalies(conn)
        result["data"] = anomalies
        result["response"] = f"Detected {len(anomalies)} anomaly/anomalies in recent activity." if anomalies else "No anomalies detected. Everything looks normal. ✓"

    elif matched_action == "sales_today":
        row = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS cnt FROM transactions WHERE date(created_at) = date('now', 'localtime')"
        ).fetchone()
        result["data"] = {"total": row["total"], "count": row["cnt"]}
        currency = settings.get("currency_symbol", "₹")
        result["response"] = f"Today's sales: {currency}{row['total']:,.2f} across {row['cnt']} transaction(s)."

    elif matched_action == "sales_week":
        row = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS cnt FROM transactions WHERE date(created_at) >= date('now', 'localtime', '-6 days')"
        ).fetchone()
        result["data"] = {"total": row["total"], "count": row["cnt"]}
        currency = settings.get("currency_symbol", "₹")
        result["response"] = f"This week's sales: {currency}{row['total']:,.2f} across {row['cnt']} transaction(s)."

    elif matched_action == "sales_month":
        row = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS cnt FROM transactions WHERE date(created_at) >= date('now', 'localtime', '-29 days')"
        ).fetchone()
        result["data"] = {"total": row["total"], "count": row["cnt"]}
        currency = settings.get("currency_symbol", "₹")
        result["response"] = f"This month's sales: {currency}{row['total']:,.2f} across {row['cnt']} transaction(s)."

    elif matched_action == "top_selling":
        rows = conn.execute("""
            SELECT ti.product_name, SUM(ti.quantity) AS total_qty, SUM(ti.line_total) AS total_revenue
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            WHERE date(t.created_at) >= date('now', 'localtime', '-29 days')
            GROUP BY ti.product_name ORDER BY total_revenue DESC LIMIT 10
        """).fetchall()
        result["data"] = [dict(r) for r in rows]
        result["response"] = f"Top {len(rows)} selling product(s) this month:" if rows else "No sales data available yet."

    elif matched_action == "demand_forecast":
        fc = get_demand_forecast(conn)[:10]
        result["data"] = fc
        result["response"] = f"Demand forecast for top {len(fc)} product(s):" if fc else "No products to forecast."

    elif matched_action == "restock":
        recs = get_restock_recommendations(conn, settings)
        result["data"] = recs
        result["response"] = f"{len(recs)} product(s) need restocking." if recs else "All products are adequately stocked. ✓"

    elif matched_action == "health_check":
        preds = get_stockout_predictions(conn)
        critical = sum(1 for p in preds if p["urgency"] == "critical")
        warning = sum(1 for p in preds if p["urgency"] == "warning")
        safe = sum(1 for p in preds if p["urgency"] == "safe")
        result["data"] = {"critical": critical, "warning": warning, "safe": safe, "total": len(preds)}
        result["response"] = f"Inventory health: {critical} critical, {warning} warning, {safe} safe out of {len(preds)} product(s)."

    elif matched_action == "warehouse_info":
        wh = conn.execute("SELECT * FROM warehouses ORDER BY is_default DESC, name ASC").fetchall()
        result["data"] = [dict(w) for w in wh]
        result["response"] = f"{len(wh)} warehouse(s) configured." if wh else "No warehouses configured yet. The system uses a single default location."

    elif matched_action == "product_search" or matched_action == "product_stock_query":
        rows = conn.execute(
            "SELECT id, name, stock_qty, unit_label, unit_price FROM products WHERE name LIKE ? ORDER BY name ASC LIMIT 10",
            (f"%{query_lower}%",)
        ).fetchall()
        result["data"] = [dict(r) for r in rows]
        result["response"] = f"Found {len(rows)} matching product(s)." if rows else "No products match your query."

    elif matched_action == "help":
        result["response"] = (
            "I understand these commands:\n"
            "• 'show low stock' — Products at/below reorder level\n"
            "• 'show dead stock' — Products with zero recent sales\n"
            "• 'show critical' — Products that will stock out in ≤3 days\n"
            "• 'show anomalies' — Suspicious patterns in sales/inventory\n"
            "• 'sales today / this week / this month' — Revenue summary\n"
            "• 'top selling products' — Best sellers by revenue\n"
            "• 'demand forecast' — Predicted demand per product\n"
            "• 'restock' — Products that need replenishment\n"
            "• 'check health' — Overall inventory health status\n"
            "• 'warehouse info' — Warehouse stock distribution\n"
            "• Any product name — Search for a product"
        )
        result["data"] = []

    else:
        result["response"] = "I'm not sure what you're asking. Type 'help' to see available commands."
        result["data"] = []

    return result


# ── 7. Multi-Warehouse Optimization ───────────────────────────────────────

def get_warehouse_optimization(conn):
    """
    Analyzes stock distribution across warehouses and suggests transfers
    to balance levels. If only one warehouse exists, returns basic info.
    """
    warehouses = conn.execute("SELECT * FROM warehouses ORDER BY is_default DESC").fetchall()
    if len(warehouses) < 2:
        return {
            "warehouses": [dict(w) for w in warehouses] if warehouses else [],
            "suggestions": [],
            "message": "Add multiple warehouses to enable stock transfer optimization."
        }

    # Get stock per warehouse per product
    stock_dist = pd.read_sql_query("""
        SELECT pw.warehouse_id, w.name AS warehouse_name,
               pw.product_id, p.name AS product_name,
               pw.stock_qty, p.reorder_level
        FROM product_warehouse pw
        JOIN warehouses w ON w.id = pw.warehouse_id
        JOIN products p ON p.id = pw.product_id
    """, conn)

    suggestions = []
    if not stock_dist.empty:
        for pid, group in stock_dist.groupby("product_id"):
            if len(group) < 2:
                continue
            mean_stock = group["stock_qty"].mean()
            reorder = group["reorder_level"].iloc[0]
            product_name = group["product_name"].iloc[0]

            overstocked = group[group["stock_qty"] > mean_stock * 1.5]
            understocked = group[group["stock_qty"] < reorder]

            for _, under in understocked.iterrows():
                for _, over in overstocked.iterrows():
                    transfer_qty = min(
                        over["stock_qty"] - mean_stock,
                        reorder - under["stock_qty"]
                    )
                    if transfer_qty > 0:
                        suggestions.append({
                            "product_id": int(pid),
                            "product_name": product_name,
                            "from_warehouse_id": int(over["warehouse_id"]),
                            "from_warehouse": over["warehouse_name"],
                            "to_warehouse_id": int(under["warehouse_id"]),
                            "to_warehouse": under["warehouse_name"],
                            "quantity": round(transfer_qty, 1),
                            "reason": f"Balance stock (from {over['stock_qty']:.0f} → {under['stock_qty']:.0f})"
                        })

    return {
        "warehouses": [dict(w) for w in warehouses],
        "suggestions": suggestions,
        "message": f"{len(suggestions)} transfer suggestion(s) to optimize stock distribution."
    }


# ── 8. Purchase Order Generation ──────────────────────────────────────────

def generate_purchase_orders(conn, settings):
    """
    Auto-generates purchase order drafts for products that need restocking,
    grouped by product category (acting as supplier proxy).
    Returns the created PO IDs.
    """
    from database import now_iso

    recs = get_restock_recommendations(conn, settings)
    if not recs:
        return {"created": 0, "po_ids": [], "message": "No products need restocking."}

    # Group by category
    grouped = defaultdict(list)
    for r in recs:
        grouped[r["category"]].append(r)

    po_prefix = settings.get("po_prefix", "PO")
    po_counter = int(settings.get("po_counter", "1"))
    created_pos = []

    for category, items in grouped.items():
        po_number = f"{po_prefix}-{po_counter:04d}"
        total_cost = sum(i["estimated_cost"] for i in items)
        now = now_iso()

        cur = conn.execute(
            """INSERT INTO purchase_orders (po_number, status, supplier_name, total_cost, notes, created_at, updated_at)
               VALUES (?, 'draft', ?, ?, ?, ?, ?)""",
            (po_number, f"{category} Supplier", round(total_cost, 2),
             f"Auto-generated by AI agent for {len(items)} product(s)", now, now)
        )
        po_id = cur.lastrowid

        for item in items:
            line_total = round(item["recommended_qty"] * item["unit_cost"], 2)
            conn.execute(
                """INSERT INTO purchase_order_items (po_id, product_id, product_name, quantity, unit_cost, line_total)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (po_id, item["product_id"], item["name"], item["recommended_qty"], item["unit_cost"], line_total)
            )

        created_pos.append({"po_id": po_id, "po_number": po_number, "category": category, "total_cost": round(total_cost, 2), "items": len(items)})
        po_counter += 1

    # Update counter
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('po_counter', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(po_counter),)
    )

    # Log the action
    conn.execute(
        "INSERT INTO ai_agent_log (action_type, summary, details, created_at) VALUES (?, ?, ?, ?)",
        ("po", f"Generated {len(created_pos)} purchase order(s)",
         json.dumps(created_pos), now_iso())
    )

    conn.commit()
    return {"created": len(created_pos), "po_ids": created_pos, "message": f"Created {len(created_pos)} purchase order draft(s)."}


# ── 9. Unified Intelligence Report ───────────────────────────────────────

def run_full_analysis(conn, settings):
    """
    Orchestrates all AI modules and returns a complete intelligence report.
    """
    # Core analyses
    stockout = get_stockout_predictions(conn)
    demand = get_demand_forecast(conn)
    restock = get_restock_recommendations(conn, settings)
    anomalies = detect_anomalies(conn)
    dead_slow = detect_dead_slow_stock(conn, settings)
    warehouses = get_warehouse_optimization(conn)

    # Summary counts
    critical_count = sum(1 for s in stockout if s["urgency"] == "critical")
    warning_count = sum(1 for s in stockout if s["urgency"] == "warning")
    safe_count = sum(1 for s in stockout if s["urgency"] == "safe")

    # Recent agent log
    log_rows = conn.execute(
        "SELECT * FROM ai_agent_log ORDER BY id DESC LIMIT 20"
    ).fetchall()

    # Active POs
    po_rows = conn.execute(
        "SELECT * FROM purchase_orders WHERE status IN ('draft', 'sent') ORDER BY id DESC LIMIT 10"
    ).fetchall()

    return {
        "summary": {
            "total_products": len(stockout),
            "critical": critical_count,
            "warning": warning_count,
            "safe": safe_count,
            "anomaly_count": len(anomalies),
            "dead_stock_count": len(dead_slow["dead_stock"]),
            "slow_moving_count": len(dead_slow["slow_moving"]),
            "restock_needed": len(restock),
            "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        },
        "stockout_predictions": stockout[:20],
        "demand_forecast": demand[:15],
        "restock_recommendations": restock[:20],
        "anomalies": anomalies[:20],
        "dead_stock": dead_slow["dead_stock"][:15],
        "slow_moving": dead_slow["slow_moving"][:15],
        "warehouses": warehouses,
        "purchase_orders": [dict(po) for po in po_rows],
        "agent_log": [dict(l) for l in log_rows],
    }
