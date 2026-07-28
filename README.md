# Offline Billing & Inventory System

A self-contained billing, inventory, and stock-intelligence system that runs
entirely on your own machine. There is no external API, no cloud service,
and no internet connection required at any point after installation —
everything (scanning, code generation, forecasting, receipts) runs locally.

## What it does

- **Billing counter** — scan items with a USB barcode scanner or your
  laptop/webcam camera, or search and tap to add them to a sale. Handles
  discounts, tax, multiple payment methods, and prints a receipt.
- **Inventory management** — add, edit, and track stock levels, reorder
  thresholds, categories, and per-item cost/selling price.
- **Barcode & QR generator** — generate Code128, EAN-13, or QR codes for
  any item, preview and print them, and optionally assign them straight to
  a product.
- **Stock alerts** — a low-stock list based on your reorder levels, plus a
  machine-learning powered prediction of which items are likely to run out
  soon based on actual sales velocity.
- **Demand forecasting** — a RandomForest model trained on your own sales
  history predicts daily demand per product and estimates days-to-stockout.
  Until there's enough history (about two weeks of sales) it falls back to
  a transparent moving-average estimate and tells you it's doing so.
- **Reports** — revenue trend, top products, category breakdown, CSV export.
- **Settings** — business details, currency, tax rate, and default reorder
  level, all used across the app and on printed receipts.

## Requirements

- Python 3.10–3.12 (3.12 recommended)
- A modern browser (Chrome, Edge, Firefox) for camera-based scanning
- Windows, macOS, or Linux

## Getting started (Windows)

1. Double-click `run_windows.bat`.
2. The first run sets up a local Python environment and installs the
   required packages (this needs an internet connection once, to fetch
   the packages listed in `requirements.txt`). Every run after that is
   fully offline.
3. Your browser opens automatically to `http://127.0.0.1:5000`.

## Getting started (manual / macOS / Linux)

```bash
python -m venv venv
source venv/bin/activate      # venv\Scripts\activate on Windows
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

## Camera scanning notes

Camera-based scanning uses your browser's own camera access (no cloud
decoding service) via a locally bundled scanning library. Most browsers
only allow camera access on `localhost` or over HTTPS, which is why the
app is accessed at `127.0.0.1` — this works without any extra
configuration. A USB barcode scanner works immediately with no setup:
it types the code directly into the scan field followed by Enter, exactly
like a keyboard.

## How the forecasting model works

Every completed sale is recorded per product per day. Once at least
14 distinct days of history and 40 data points exist, `RandomForestRegressor`
(scikit-learn) is trained on rolling averages, lag features, day-of-week,
and price to predict next-day demand per product. That prediction is used
both to forecast demand and to estimate days-to-stockout
(`current stock ÷ predicted daily demand`). You can trigger a retrain any
time from the Alerts page as more sales come in — the model file is stored
locally at `ml/demand_model.pkl` and is never uploaded anywhere.

## Data storage

Everything is stored in a single local SQLite file at `data/store.db`,
created automatically the first time you run the app. Back it up by
copying that one file.

## Project structure

```
app.py                  Flask application and all routes
database.py              SQLite schema and connection helper
codegen.py                Barcode / QR image generation (offline)
ml/                       Demand forecasting model (training + prediction)
static/css/style.css      Design system
static/js/                Billing, scanning, barcode, and reports logic
static/vendor/            Locally bundled scanning + charting libraries
static/fonts/             Locally bundled type (no Google Fonts CDN)
templates/                Page templates
data/                     SQLite database (created on first run)
```
