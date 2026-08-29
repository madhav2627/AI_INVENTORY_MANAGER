import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_VERCEL = bool(os.environ.get("VERCEL"))

if IS_VERCEL:
    MODEL_PATH = "/tmp/demand_model.pkl"
    META_PATH = "/tmp/model_meta.json"
    
    # Try to copy packaged metadata/models to /tmp if they aren't there yet
    orig_model = os.path.join(BASE_DIR, "ml", "demand_model.pkl")
    orig_meta = os.path.join(BASE_DIR, "ml", "model_meta.json")
    try:
        if os.path.exists(orig_model) and not os.path.exists(MODEL_PATH):
            shutil.copy2(orig_model, MODEL_PATH)
        if os.path.exists(orig_meta) and not os.path.exists(META_PATH):
            shutil.copy2(orig_meta, META_PATH)
    except Exception:
        pass
else:
    MODEL_PATH = os.path.join(BASE_DIR, "ml", "demand_model.pkl")
    META_PATH = os.path.join(BASE_DIR, "ml", "model_meta.json")


# Below these thresholds there simply isn't enough sales history for a
# trained model to be trustworthy, so the app uses a transparent moving
# average heuristic instead and is upfront with the user about it.
MIN_TRAINING_ROWS = 40
MIN_DISTINCT_DAYS = 14
MIN_PRODUCTS_WITH_HISTORY = 1

FORECAST_HORIZON_DAYS = 30
