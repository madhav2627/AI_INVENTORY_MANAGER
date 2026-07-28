"""
Produces two things per product, using whichever method is currently
trustworthy given how much sales history exists:
  - predicted average demand per day, going forward
  - an estimated number of days until stock runs out

If the RandomForest model has enough history to be trained, its
predictions are used. Otherwise a simple, transparent moving-average
heuristic is used instead, and the app tells the user which method is
active so nothing is presented as more certain than it is.
"""
import json
import os
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd

from ml.features import build_feature_table, encode_categories, FEATURE_COLUMNS
from ml.config import MODEL_PATH, META_PATH, FORECAST_HORIZON_DAYS


def load_meta():
    if not os.path.exists(META_PATH):
        return {"status": "untrained"}
    with open(META_PATH) as f:
        return json.load(f)


def _heuristic_demand(conn):
    """
    Fallback used before there's enough history for the model: average
    daily sales over whatever history exists (minimum 1 day), per product.
    """
    query = """
        SELECT p.id AS product_id, p.name, p.stock_qty, p.reorder_level,
               COALESCE(SUM(ti.quantity), 0) AS total_sold,
               MIN(date(t.created_at)) AS first_sale,
               MAX(date(t.created_at)) AS last_sale
        FROM products p
        LEFT JOIN transaction_items ti ON ti.product_id = p.id
        LEFT JOIN transactions t ON t.id = ti.transaction_id
        GROUP BY p.id
    """
    df = pd.read_sql_query(query, conn)
    results = {}
    for _, row in df.iterrows():
        if row["first_sale"] and row["last_sale"]:
            days_span = max(
                1,
                (pd.to_datetime(row["last_sale"]) - pd.to_datetime(row["first_sale"])).days + 1,
            )
        else:
            days_span = 1
        avg_daily = row["total_sold"] / days_span if row["total_sold"] else 0.0
        results[int(row["product_id"])] = {
            "predicted_daily_demand": round(float(avg_daily), 2),
            "method": "heuristic",
        }
    return results


def _model_demand(conn, meta):
    model = joblib.load(MODEL_PATH)
    table = build_feature_table(conn)
    if table.empty:
        return {}

    categories = meta.get("categories")
    table, _ = encode_categories(table, known_categories=categories)

    latest = table.sort_values("sale_date").groupby("product_id").tail(1).copy()

    tomorrow_dow = (datetime.now().weekday() + 1) % 7
    tomorrow_dom = (datetime.now() + timedelta(days=1)).day

    latest["day_of_week"] = tomorrow_dow
    latest["day_of_month"] = tomorrow_dom
    latest["is_weekend"] = int(tomorrow_dow >= 5)
    latest["lag_1"] = latest["qty_sold"]
    latest["rolling_avg_3"] = (
        table.sort_values("sale_date").groupby("product_id")["qty_sold"]
        .apply(lambda s: s.tail(3).mean())
        .reindex(latest["product_id"]).values
    )
    latest["rolling_avg_7"] = (
        table.sort_values("sale_date").groupby("product_id")["qty_sold"]
        .apply(lambda s: s.tail(7).mean())
        .reindex(latest["product_id"]).values
    )

    preds = model.predict(latest[FEATURE_COLUMNS])
    results = {}
    for pid, pred in zip(latest["product_id"], preds):
        results[int(pid)] = {
            "predicted_daily_demand": round(max(float(pred), 0.0), 2),
            "method": "ml_model",
        }
    return results


def get_intelligence(conn):
    """
    Returns { product_id: {predicted_daily_demand, method, days_to_stockout,
    forecast_30day, stock_qty, reorder_level, name} } for every product.
    """
    meta = load_meta()
    demand_by_product = {}

    if meta.get("status") == "trained" and os.path.exists(MODEL_PATH):
        try:
            demand_by_product = _model_demand(conn, meta)
        except Exception:
            demand_by_product = {}

    if not demand_by_product:
        demand_by_product = _heuristic_demand(conn)

    products = conn.execute(
        "SELECT id, name, stock_qty, reorder_level FROM products"
    ).fetchall()

    output = {}
    for p in products:
        info = demand_by_product.get(
            p["id"], {"predicted_daily_demand": 0.0, "method": "heuristic"}
        )
        daily_demand = info["predicted_daily_demand"]
        if daily_demand > 0:
            days_to_stockout = round(p["stock_qty"] / daily_demand, 1)
        else:
            days_to_stockout = None

        output[p["id"]] = {
            "name": p["name"],
            "stock_qty": p["stock_qty"],
            "reorder_level": p["reorder_level"],
            "predicted_daily_demand": daily_demand,
            "forecast_30day": round(daily_demand * FORECAST_HORIZON_DAYS, 1),
            "days_to_stockout": days_to_stockout,
            "method": info["method"],
        }
    return output


def get_model_status():
    meta = load_meta()
    return meta
