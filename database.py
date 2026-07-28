"""
Local SQLite data layer for the billing system.
No network calls are made from this module. The database file lives on disk
next to the application and is created automatically on first run.
"""
import sqlite3
import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "store.db")

# New columns to add to the products table (for migration on existing DBs)
PRODUCTS_NEW_COLUMNS = [
    ("brand",            "TEXT DEFAULT ''"),
    ("sub_category",     "TEXT DEFAULT ''"),
    ("description",      "TEXT DEFAULT ''"),
    ("image_url",        "TEXT DEFAULT ''"),
    ("expiry_date",      "TEXT DEFAULT ''"),
    ("mfg_date",         "TEXT DEFAULT ''"),
    ("batch_number",     "TEXT DEFAULT ''"),
    ("serial_number",    "TEXT DEFAULT ''"),
    ("weight",           "TEXT DEFAULT ''"),
    ("volume",           "TEXT DEFAULT ''"),
    ("mrp",              "REAL DEFAULT 0"),
    ("manufacturer",     "TEXT DEFAULT ''"),
    ("country_of_origin","TEXT DEFAULT ''"),
    ("ingredients",      "TEXT DEFAULT ''"),
    ("nutritional_info", "TEXT DEFAULT ''"),
    ("dimensions",       "TEXT DEFAULT ''"),
    ("color",            "TEXT DEFAULT ''"),
    ("size_label",       "TEXT DEFAULT ''"),
    ("warranty_info",    "TEXT DEFAULT ''"),
    ("supplier_details", "TEXT DEFAULT ''"),
    ("barcode_raw",      "TEXT DEFAULT ''"),
    ("source_db",        "TEXT DEFAULT ''"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT 'General',
    code_value      TEXT UNIQUE,
    code_type       TEXT DEFAULT 'CODE128',
    unit_price      REAL NOT NULL DEFAULT 0,
    cost_price      REAL NOT NULL DEFAULT 0,
    stock_qty       REAL NOT NULL DEFAULT 0,
    unit_label      TEXT DEFAULT 'pcs',
    reorder_level   REAL NOT NULL DEFAULT 5,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT UNIQUE NOT NULL,
    created_at      TEXT NOT NULL,
    subtotal        REAL NOT NULL,
    discount        REAL NOT NULL DEFAULT 0,
    tax             REAL NOT NULL DEFAULT 0,
    total            REAL NOT NULL,
    payment_method  TEXT DEFAULT 'Cash',
    customer_name   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name    TEXT NOT NULL,
    quantity        REAL NOT NULL,
    unit_price      REAL NOT NULL,
    line_total      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS stock_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    change_qty      REAL NOT NULL,
    reason          TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Multi-warehouse support
CREATE TABLE IF NOT EXISTS warehouses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    location    TEXT DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_warehouse (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER REFERENCES products(id) ON DELETE CASCADE,
    warehouse_id  INTEGER REFERENCES warehouses(id) ON DELETE CASCADE,
    stock_qty     REAL NOT NULL DEFAULT 0,
    UNIQUE(product_id, warehouse_id)
);

CREATE TABLE IF NOT EXISTS warehouse_transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER REFERENCES products(id),
    from_warehouse  INTEGER REFERENCES warehouses(id),
    to_warehouse    INTEGER REFERENCES warehouses(id),
    quantity        REAL NOT NULL,
    reason          TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Purchase order tracking
CREATE TABLE IF NOT EXISTS purchase_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number       TEXT UNIQUE NOT NULL,
    status          TEXT DEFAULT 'draft',
    supplier_name   TEXT DEFAULT '',
    total_cost      REAL DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id           INTEGER REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id),
    product_name    TEXT NOT NULL,
    quantity        REAL NOT NULL,
    unit_cost       REAL NOT NULL,
    line_total      REAL NOT NULL
);

-- AI agent action log
CREATE TABLE IF NOT EXISTS ai_agent_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    summary     TEXT NOT NULL,
    details     TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Barcode lookup cache for offline mode
CREATE TABLE IF NOT EXISTS barcode_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode     TEXT UNIQUE NOT NULL,
    data        TEXT NOT NULL,
    source      TEXT DEFAULT '',
    cached_at   TEXT NOT NULL
);

-- Expiry alert tracking
CREATE TABLE IF NOT EXISTS expiry_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    expiry_date TEXT NOT NULL,
    status      TEXT DEFAULT 'fresh',
    notified    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- User authentication details
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT DEFAULT '',
    role            TEXT DEFAULT 'admin',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_items_product ON transaction_items(product_id);
CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_log_created ON ai_agent_log(created_at);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);
CREATE INDEX IF NOT EXISTS idx_barcode_cache ON barcode_cache(barcode);
CREATE INDEX IF NOT EXISTS idx_expiry_alerts_product ON expiry_alerts(product_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""

DEFAULT_SETTINGS = {
    "business_name": "Your Store",
    "business_address": "",
    "business_phone": "",
    "currency_symbol": "\u20b9",
    "default_tax_rate": "5",
    "low_stock_default": "5",
    "preferred_code_type": "CODE128",
    "invoice_prefix": "INV",
    "invoice_counter": "1000",
    "lead_time_days": "3",
    "safety_stock_multiplier": "1.5",
    "dead_stock_days": "60",
    "slow_moving_days": "30",
    "po_prefix": "PO",
    "po_counter": "1",
}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_products_columns(conn):
    """Safely add new columns to products table if they don't exist yet."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    for col_name, col_def in PRODUCTS_NEW_COLUMNS:
        if col_name not in existing:
            try:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass


def seed_default_admin(conn):
    """Ensure at least one admin account exists in local database and data/users.json."""
    from werkzeug.security import generate_password_hash
    user = conn.execute("SELECT * FROM users LIMIT 1").fetchone()
    if not user:
        pwd_hash = generate_password_hash("admin123")
        created = now_iso()
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin", pwd_hash, "Administrator", "admin", created),
        )
        conn.commit()
    sync_users_to_json(conn)


def sync_users_to_json(conn):
    """Sync user accounts to local JSON database (data/users.json) for redundancy."""
    try:
        json_path = os.path.join(BASE_DIR, "data", "users.json")
        users = conn.execute("SELECT id, username, full_name, role, created_at FROM users").fetchall()
        user_list = [dict(u) for u in users]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(user_list, f, indent=2)
    except Exception:
        pass


def get_user_by_username(conn, username):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()


def get_user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(conn, username, password, full_name="", role="admin"):
    from werkzeug.security import generate_password_hash
    pwd_hash = generate_password_hash(password)
    created = now_iso()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (username.strip().lower(), pwd_hash, full_name.strip(), role, created),
    )
    conn.commit()
    sync_users_to_json(conn)
    return cursor.lastrowid


def init_db():
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    _migrate_products_columns(conn)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    conn.commit()
    seed_default_admin(conn)
    conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def get_expiry_status(expiry_date_str):
    """Return (status_label, days_remaining) for a given expiry date string."""
    if not expiry_date_str:
        return None, None
    try:
        from datetime import date
        expiry = datetime.strptime(expiry_date_str[:10], "%Y-%m-%d").date()
        today = date.today()
        days = (expiry - today).days
        if days < 0:
            return "expired", days
        elif days <= 30:
            return "near_expiry", days
        else:
            return "fresh", days
    except Exception:
        return None, None
