"""
Trains a RandomForestRegressor on this store's own transaction history to
predict how many units of a product will sell on a given day. The same
model powers both demand forecasting and stock-out prediction elsewhere
in the app. No external service or API is used - training runs entirely
on-device against the local SQLite database.
"""
import json
from datetime import datetime

import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

from ml.features import build_feature_table, encode_categories, FEATURE_COLUMNS, TARGET_COLUMN
from ml.config import MODEL_PATH, META_PATH, MIN_TRAINING_ROWS, MIN_DISTINCT_DAYS


def _write_meta(status, **kwargs):
    meta = {"status": status, "trained_at": datetime.now().isoformat(timespec="seconds")}
    meta.update(kwargs)
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def train_model(conn):
    """
    Returns a dict describing what happened: either the model was trained
    successfully, or there wasn't enough data yet (in which case the
    prediction layer will use the heuristic fallback).
    """
    table = build_feature_table(conn)

    if table.empty:
        return _write_meta(
            "insufficient_data",
            reason="No sales recorded yet.",
            rows_available=0,
        )

    distinct_days = table["sale_date"].nunique()
    rows_available = len(table)

    if rows_available < MIN_TRAINING_ROWS or distinct_days < MIN_DISTINCT_DAYS:
        return _write_meta(
            "insufficient_data",
            reason=f"Only {distinct_days} day(s) of sales history recorded so far.",
            rows_available=rows_available,
            distinct_days=distinct_days,
            rows_needed=MIN_TRAINING_ROWS,
            days_needed=MIN_DISTINCT_DAYS,
        )

    table, categories = encode_categories(table)

    X = table[FEATURE_COLUMNS]
    y = table[TARGET_COLUMN]

    test_size = 0.2 if rows_available >= 60 else 0.1
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42
    )

    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    mae = None
    if len(X_test) > 0:
        preds = model.predict(X_test)
        mae = round(float(mean_absolute_error(y_test, preds)), 3)

    joblib.dump(model, MODEL_PATH)

    return _write_meta(
        "trained",
        rows_available=rows_available,
        distinct_days=distinct_days,
        mean_absolute_error=mae,
        products_covered=int(table["product_id"].nunique()),
        categories=categories,
    )
