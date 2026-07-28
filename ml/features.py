"""
Builds the daily product-sales table that the demand model is trained on.
Everything runs locally against the SQLite file, entirely offline.
"""
import pandas as pd
import numpy as np


def load_daily_sales(conn):
    """Returns one row per (product_id, date) with quantity sold that day."""
    query = """
        SELECT ti.product_id,
               p.name,
               p.category,
               p.unit_price,
               p.stock_qty,
               p.reorder_level,
               date(t.created_at) AS sale_date,
               SUM(ti.quantity) AS qty_sold
        FROM transaction_items ti
        JOIN transactions t ON t.id = ti.transaction_id
        JOIN products p ON p.id = ti.product_id
        WHERE ti.product_id IS NOT NULL
        GROUP BY ti.product_id, date(t.created_at)
        ORDER BY ti.product_id, sale_date
    """
    df = pd.read_sql_query(query, conn)
    if df.empty:
        return df
    df["sale_date"] = pd.to_datetime(df["sale_date"])
    return df


def build_feature_table(conn, min_history_days=1):
    """
    Expands each product's sales into a continuous daily calendar (filling
    no-sale days with 0) and engineers rolling/lag features used for both
    demand forecasting and stock-out prediction.
    """
    df = load_daily_sales(conn)
    if df.empty:
        return pd.DataFrame()

    frames = []
    for pid, g in df.groupby("product_id"):
        g = g.set_index("sale_date").sort_index()
        full_range = pd.date_range(g.index.min(), g.index.max(), freq="D")
        g = g.reindex(full_range)
        g["product_id"] = pid
        g["name"] = g["name"].ffill().bfill()
        g["category"] = g["category"].ffill().bfill()
        g["unit_price"] = g["unit_price"].ffill().bfill()
        g["stock_qty"] = g["stock_qty"].ffill().bfill()
        g["reorder_level"] = g["reorder_level"].ffill().bfill()
        g["qty_sold"] = g["qty_sold"].fillna(0)
        g.index.name = "sale_date"
        g = g.reset_index()
        frames.append(g)

    full = pd.concat(frames, ignore_index=True)
    full = full.sort_values(["product_id", "sale_date"])

    full["day_of_week"] = full["sale_date"].dt.dayofweek
    full["day_of_month"] = full["sale_date"].dt.day
    full["is_weekend"] = (full["day_of_week"] >= 5).astype(int)

    grouped = full.groupby("product_id")["qty_sold"]
    full["lag_1"] = grouped.shift(1).fillna(0)
    full["lag_7"] = grouped.shift(7).fillna(0)
    full["rolling_avg_3"] = grouped.shift(1).rolling(3, min_periods=1).mean().fillna(0)
    full["rolling_avg_7"] = grouped.shift(1).rolling(7, min_periods=1).mean().fillna(0)
    full["rolling_avg_14"] = grouped.shift(1).rolling(14, min_periods=1).mean().fillna(0)

    return full


def encode_categories(df, known_categories=None):
    """
    Maps category names to stable integer codes. If known_categories is
    given (from a previously trained model), unseen categories map to -1
    instead of silently reshuffling every other code.
    """
    if known_categories is None:
        known_categories = sorted(df["category"].dropna().unique().tolist())
    lookup = {name: i for i, name in enumerate(known_categories)}
    df = df.copy()
    df["category_code"] = df["category"].map(lookup).fillna(-1).astype(int)
    return df, known_categories


FEATURE_COLUMNS = [
    "day_of_week",
    "day_of_month",
    "is_weekend",
    "lag_1",
    "lag_7",
    "rolling_avg_3",
    "rolling_avg_7",
    "rolling_avg_14",
    "unit_price",
    "category_code",
    "product_id",
]

TARGET_COLUMN = "qty_sold"
