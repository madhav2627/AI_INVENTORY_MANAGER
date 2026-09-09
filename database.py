"""
Local SQLite data layer for the billing system.
No network calls are made from this module. The database file lives on disk
next to the application and is created automatically on first run.
"""
import sqlite3
import os
import json
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# SQLite remains the offline/local option.  Vercel deployments must use a
# managed PostgreSQL database because /tmp is discarded between function runs.
# Prioritize Neon connection pooler to eliminate TCP/TLS handshake latency in serverless.
DATABASE_URL = None
for _env_key in ("POSTGRES_URL", "DATABASE_URL", "POSTGRES_PRISMA_URL"):
    _val = os.environ.get(_env_key)
    if _val and "-pooler." in _val:
        DATABASE_URL = _val
        break
if not DATABASE_URL:
    DATABASE_URL = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")

USING_POSTGRES = bool(DATABASE_URL)
DB_PATH = None if USING_POSTGRES else os.path.join(BASE_DIR, "data", "store.db")

if os.environ.get("VERCEL") and not USING_POSTGRES:
    raise RuntimeError(
        "Vercel needs a persistent PostgreSQL database. Ensure the Neon database integration "
        "is connected so DATABASE_URL or POSTGRES_URL is present in project environment variables."
    )

if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    IntegrityError = sqlite3.IntegrityError


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
    user_id         INTEGER NOT NULL DEFAULT 1,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT 'General',
    code_value      TEXT,
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
    user_id         INTEGER NOT NULL DEFAULT 1,
    invoice_no      TEXT NOT NULL,
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
    key             TEXT NOT NULL,
    value           TEXT,
    user_id         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (key, user_id)
);

CREATE TABLE IF NOT EXISTS stock_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL DEFAULT 1,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    change_qty      REAL NOT NULL,
    reason          TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Multi-warehouse support
CREATE TABLE IF NOT EXISTS warehouses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL,
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
    user_id         INTEGER NOT NULL DEFAULT 1,
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
    user_id         INTEGER NOT NULL DEFAULT 1,
    po_number       TEXT NOT NULL,
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
    user_id     INTEGER NOT NULL DEFAULT 1,
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
    user_id     INTEGER NOT NULL DEFAULT 1,
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


def _postgres_sql(sql):
    """Translate the small SQLite dialect surface used by this application."""
    sql = sql.replace("INSERT OR IGNORE INTO users", "INSERT INTO users")
    if "INSERT INTO users" in sql and "ON CONFLICT" not in sql and "INSERT OR IGNORE" not in sql:
        # Only restore_users_from_json relies on a duplicate being harmless.
        if "password_hash, full_name, role, created_at" in sql and "VALUES" in sql:
            sql += " ON CONFLICT (username) DO NOTHING"
    sql = sql.replace(
        "INSERT OR IGNORE INTO settings (key, value, user_id)",
        "INSERT INTO settings (key, value, user_id)",
    )
    if "INSERT INTO settings (key, value, user_id)" in sql and "ON CONFLICT" not in sql:
        if "VALUES" in sql:
            sql += " ON CONFLICT (key, user_id) DO NOTHING"
    if "INSERT OR REPLACE INTO settings" in sql:
        sql = sql.replace("INSERT OR REPLACE INTO settings", "INSERT INTO settings")
        sql += " ON CONFLICT (key, user_id) DO UPDATE SET value = EXCLUDED.value"
    if "INSERT OR REPLACE INTO expiry_alerts" in sql:
        sql = sql.replace("INSERT OR REPLACE INTO expiry_alerts", "INSERT INTO expiry_alerts")
        sql += (" ON CONFLICT (user_id, product_id) DO UPDATE SET "
                "expiry_date = EXCLUDED.expiry_date, status = EXCLUDED.status, "
                "notified = EXCLUDED.notified, created_at = EXCLUDED.created_at")

    # The app stores timestamps as ISO text. PostgreSQL can cast them to dates,
    # but SQLite's relative-date syntax needs translating.
    sql = sql.replace("date('now', 'localtime')", "CURRENT_DATE")
    sql = sql.replace("date('now','localtime')", "CURRENT_DATE")
    sql = re.sub(
        r"date\('now',\s*'localtime',\s*'-(\d+) days'\)",
        lambda m: f"CURRENT_DATE - INTERVAL '{m.group(1)} days'",
        sql,
    )
    sql = re.sub(
        r"date\('now',\s*'localtime',\s*'-\{([^}]+)\} days'\)",
        r"CURRENT_DATE - INTERVAL '\1 days'",
        sql,
    )
    sql = re.sub(r"date\(([^)]+)\)", r"CAST(\1 AS DATE)", sql)
    return sql.replace("?", "%s")


class PostgresRow:
    """A row wrapper that behaves identically to sqlite3.Row for dict, key, and sequence access."""
    __slots__ = ("_data", "_index_map")

    def __init__(self, description, row_tuple):
        self._data = row_tuple
        if description:
            self._index_map = {
                (col.name if hasattr(col, "name") else col[0]): i
                for i, col in enumerate(description)
            }
        else:
            self._index_map = {}

    def __getitem__(self, key):
        if isinstance(key, str):
            idx = self._index_map.get(key)
            if idx is None:
                idx = self._index_map.get(key.lower())
            if idx is None:
                raise KeyError(key)
            return self._data[idx]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def keys(self):
        return self._index_map.keys()

    def get(self, key, default=None):
        idx = self._index_map.get(key)
        if idx is None:
            idx = self._index_map.get(key.lower())
        return self._data[idx] if idx is not None else default

    def __contains__(self, key):
        return key in self._index_map or (isinstance(key, str) and key.lower() in self._index_map)


class PostgresCursor:
    """sqlite3-like cursor used to keep the route layer and pandas database-agnostic."""
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        statement = _postgres_sql(sql)
        is_insert = statement.lstrip().upper().startswith("INSERT INTO")
        table_match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)", statement, re.IGNORECASE)
        has_numeric_id = bool(table_match and table_match.group(1).lower() != "settings")
        if is_insert and has_numeric_id and "RETURNING" not in statement.upper():
            statement += " RETURNING id"
        self._cursor.execute(statement, params or ())
        if is_insert and has_numeric_id:
            row = self._cursor.fetchone()
            if row is not None:
                self.lastrowid = row[0] if isinstance(row, (tuple, list)) else (row.get("id") if hasattr(row, "get") else row[0])
            else:
                self.lastrowid = None
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._cursor.description:
            return PostgresRow(self._cursor.description, row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        if self._cursor.description:
            desc = self._cursor.description
            return [PostgresRow(desc, r) for r in rows]
        return rows

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=None):
        cursor = PostgresCursor(self._connection.cursor())
        return cursor.execute(sql, params)

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def executescript(self, script):
        with self._connection.cursor() as cursor:
            for statement in script.split(";"):
                if statement.strip():
                    cursor.execute(statement)

    def commit(self):
        self._connection.commit()

    def close(self):
        try:
            from flask import has_request_context
            if has_request_context():
                # In active HTTP request context, keep connection open until request completes
                return
        except Exception:
            pass
        try:
            self._connection.close()
        except Exception:
            pass

    def force_close(self):
        try:
            self._connection.close()
        except Exception:
            pass


def get_connection():
    try:
        from flask import g, has_request_context
        if has_request_context():
            if hasattr(g, '_db_conn') and g._db_conn is not None:
                conn = g._db_conn
                is_closed = getattr(conn, 'closed', False) or getattr(getattr(conn, '_connection', None), 'closed', False)
                if not is_closed:
                    return conn
            if USING_POSTGRES:
                conn = PostgresConnection(psycopg.connect(DATABASE_URL))
            else:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
            g._db_conn = conn
            return conn
    except Exception:
        pass

    if USING_POSTGRES:
        return PostgresConnection(psycopg.connect(DATABASE_URL))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


TABLES_WITH_USER_ID = [
    "products", "transactions", "settings", "stock_adjustments",
    "warehouses", "warehouse_transfers", "purchase_orders",
    "ai_agent_log", "expiry_alerts"
]

def _migrate_user_id_columns(conn):
    """Safely add user_id column to existing tables for multi-tenant data isolation."""
    if USING_POSTGRES:
        stmts = [
            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS user_id INTEGER NOT NULL DEFAULT 1"
            for table_name in TABLES_WITH_USER_ID
        ] + [
            "CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_txn_user ON transactions(user_id)"
        ]
        conn.executescript("; ".join(stmts))
        return
    for table_name in TABLES_WITH_USER_ID:
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
            if "user_id" not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_user ON transactions(user_id)")
    except Exception:
        pass


def _migrate_products_columns(conn):
    """Safely add new columns to products table if they don't exist yet."""
    if USING_POSTGRES:
        stmts = [
            f"ALTER TABLE products ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
            for col_name, col_def in PRODUCTS_NEW_COLUMNS
        ]
        conn.executescript("; ".join(stmts))
        return
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
    restore_users_from_json(conn)
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
        users = conn.execute("SELECT id, username, password_hash, full_name, role, created_at FROM users").fetchall()
        user_list = [dict(u) for u in users]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(user_list, f, indent=2)
    except Exception:
        pass


def restore_users_from_json(conn):
    """Restore registered users from JSON backup if missing in SQLite."""
    try:
        json_path = os.path.join(BASE_DIR, "data", "users.json")
        if not os.path.exists(json_path):
            return
        with open(json_path, "r", encoding="utf-8") as f:
            user_list = json.load(f)
        for u in user_list:
            if u.get("username") and u.get("password_hash"):
                conn.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                    (u["username"].strip().lower(), u["password_hash"], u.get("full_name", ""), u.get("role", "admin"), u.get("created_at", now_iso()))
                )
        conn.commit()
    except Exception:
        pass


def get_user_by_username(conn, username):
    if not username:
        return None
    cleaned = str(username).strip().lower()
    return conn.execute("SELECT * FROM users WHERE LOWER(username) = ?", (cleaned,)).fetchone()


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
    schema = SCHEMA
    if USING_POSTGRES:
        schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    conn.executescript(schema)
    _migrate_user_id_columns(conn)
    _migrate_products_columns(conn)
    if USING_POSTGRES:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_expiry_alerts_user_product "
            "ON expiry_alerts(user_id, product_id)"
        )
    restore_users_from_json(conn)
    if USING_POSTGRES:
        stmts = [
            f"INSERT INTO settings (key, value, user_id) VALUES ('{k}', '{v}', 1) ON CONFLICT (key, user_id) DO NOTHING"
            for k, v in DEFAULT_SETTINGS.items()
        ]
        conn.executescript("; ".join(stmts))
    else:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, user_id) VALUES (?, ?, 1)", (key, value)
            )
    conn.commit()
    seed_default_admin(conn)
    conn.close()


_initialized = False


def ensure_initialized():
    """Initialise once per process; fast probe avoids redundant DDL round-trips on warm/cold starts."""
    global _initialized
    if not _initialized:
        if USING_POSTGRES:
            try:
                conn = get_connection()
                # Fast probe: if 'products' exists, schema is already provisioned
                conn.execute("SELECT 1 FROM products LIMIT 1")
                conn.close()
                _initialized = True
                return
            except Exception:
                pass
        init_db()
        _initialized = True


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
