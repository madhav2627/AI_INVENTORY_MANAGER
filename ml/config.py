import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "ml", "demand_model.pkl")
META_PATH = os.path.join(BASE_DIR, "ml", "model_meta.json")

# Below these thresholds there simply isn't enough sales history for a
# trained model to be trustworthy, so the app uses a transparent moving
# average heuristic instead and is upfront with the user about it.
MIN_TRAINING_ROWS = 40
MIN_DISTINCT_DAYS = 14
MIN_PRODUCTS_WITH_HISTORY = 1

FORECAST_HORIZON_DAYS = 30
