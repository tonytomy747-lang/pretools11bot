"""
Tiny storage layer.

Works with either:
  * SQLite   -> default, zero config (file: shop.db). Great for local / VPS.
  * Postgres -> set DATABASE_URL (Supabase, Neon, Render...). Use this on
                hosts with an ephemeral filesystem (Render free tier).
"""

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.getenv("SQLITE_PATH", "shop.db")

_lock = threading.RLock()
_conn = None

if IS_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    if IS_PG:
        c = psycopg2.connect(DATABASE_URL, connect_timeout=15)
        c.autocommit = True
        return c
    c = sqlite3.connect(SQLITE_PATH, check_same_thread=False, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _fix(sql: str) -> str:
    return sql.replace("?", "%s") if IS_PG else sql


def execute(sql, params=(), fetch=None):
    """fetch: None | 'one' | 'all'. Reconnects once on a dropped connection."""
    global _conn
    last_err = None
    for attempt in range(2):
        try:
            with _lock:
                if _conn is None:
                    _conn = _connect()
                cur = (
                    _conn.cursor(cursor_factory=RealDictCursor)
                    if IS_PG
                    else _conn.cursor()
                )
                cur.execute(_fix(sql), tuple(params))
                if fetch == "one":
                    row = cur.fetchone()
                    out = dict(row) if row else None
                elif fetch == "all":
                    out = [dict(r) for r in cur.fetchall()]
                else:
                    out = None
                if not IS_PG:
                    _conn.commit()
                cur.close()
                return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            try:
                if _conn:
                    _conn.close()
            except Exception:
                pass
            _conn = None
            if attempt == 0:
                time.sleep(0.6)
                continue
            raise last_err


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    price REAL DEFAULT 0,
    photo_file_id TEXT,
    delivery_content TEXT,
    stock INTEGER DEFAULT -1,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    product_id INTEGER,
    product_title TEXT,
    price REAL,
    network TEXT,
    address TEXT,
    txid TEXT,
    status TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    is_blocked INTEGER DEFAULT 0,
    balance REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS topups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    amount REAL,
    network TEXT,
    address TEXT,
    txid TEXT,
    status TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    status TEXT DEFAULT 'open',
    last_msg_preview TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER,
    sender TEXT,
    body TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    max_uses INTEGER DEFAULT -1,
    used_count INTEGER DEFAULT 0,
    max_uses_per_user INTEGER DEFAULT 1,
    min_order REAL DEFAULT 0,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupon_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id INTEGER,
    user_id INTEGER,
    order_id INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_id INTEGER,
    created_at TEXT,
    UNIQUE(user_id, product_id)
);
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT,
    detail TEXT,
    created_at TEXT
);
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    category_id INTEGER,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    price DOUBLE PRECISION DEFAULT 0,
    photo_file_id TEXT,
    delivery_content TEXT,
    stock INTEGER DEFAULT -1,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT,
    product_id INTEGER,
    product_title TEXT,
    price DOUBLE PRECISION,
    network TEXT,
    address TEXT,
    txid TEXT,
    status TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    is_blocked INTEGER DEFAULT 0,
    balance DOUBLE PRECISION DEFAULT 0
);
CREATE TABLE IF NOT EXISTS topups (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT,
    amount DOUBLE PRECISION,
    network TEXT,
    address TEXT,
    txid TEXT,
    status TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT,
    status TEXT DEFAULT 'open',
    last_msg_preview TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER,
    sender TEXT,
    body TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupons (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    max_uses INTEGER DEFAULT -1,
    used_count INTEGER DEFAULT 0,
    max_uses_per_user INTEGER DEFAULT 1,
    min_order DOUBLE PRECISION DEFAULT 0,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupon_uses (
    id SERIAL PRIMARY KEY,
    coupon_id INTEGER,
    user_id BIGINT,
    order_id INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS favorites (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    product_id INTEGER,
    created_at TEXT,
    UNIQUE(user_id, product_id)
);
CREATE TABLE IF NOT EXISTS admin_log (
    id SERIAL PRIMARY KEY,
    admin_id BIGINT,
    action TEXT,
    detail TEXT,
    created_at TEXT
);
"""


def _sqlite_has_column(table, col):
    rows = execute(f"PRAGMA table_info({table})", (), "all")
    return any(r["name"] == col for r in rows)


def _pg_has_column(table, col):
    row = execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s" if IS_PG else
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        (table, col),
        "one",
    )
    return bool(row)


def init():
    schema = SCHEMA_PG if IS_PG else SCHEMA_SQLITE
    for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
        execute(stmt)
    execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
    execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)")
    execute("CREATE INDEX IF NOT EXISTS idx_topups_user ON topups(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
    execute("CREATE INDEX IF NOT EXISTS idx_tmsg_ticket ON ticket_messages(ticket_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_coupon_uses_coupon ON coupon_uses(coupon_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_coupon_uses_user ON coupon_uses(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_admin_log_admin ON admin_log(admin_id)")

    def _has_col(table, col):
        return (
            _sqlite_has_column(table, col) if not IS_PG
            else _pg_has_column(table, col)
        )

    def _add_col(table, col, col_type):
        if not _has_col(table, col):
            execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    real = "DOUBLE PRECISION" if IS_PG else "REAL"

    # migration: older DBs created before these columns existed
    _add_col("users", "balance", f"{real} DEFAULT 0")
    _add_col("users", "referred_by", "BIGINT" if IS_PG else "INTEGER")
    _add_col("users", "ref_bonus_paid", "INTEGER DEFAULT 0")
    _add_col("orders", "discount", f"{real} DEFAULT 0")
    _add_col("orders", "coupon_code", "TEXT")
    _add_col("products", "icon", "TEXT")
    execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")


# ---------------------------------------------------------------- settings
def get_setting(key, default=None):
    row = execute("SELECT value FROM settings WHERE key = ?", (key,), "one")
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    if execute("SELECT 1 FROM settings WHERE key = ?", (key,), "one"):
        execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    else:
        execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def del_setting(key):
    execute("DELETE FROM settings WHERE key = ?", (key,))


# ---------------------------------------------------------------- users
def upsert_user(user_id, username, first_name):
    if execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,), "one"):
        execute(
            "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
            (username, first_name, user_id),
        )
    else:
        execute(
            "INSERT INTO users (user_id, username, first_name, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, now()),
        )


def all_user_ids():
    return [r["user_id"] for r in execute("SELECT user_id FROM users", (), "all")]


def count_users():
    return execute("SELECT COUNT(*) AS c FROM users", (), "one")["c"]


def is_blocked(user_id):
    row = execute(
        "SELECT is_blocked FROM users WHERE user_id = ?", (user_id,), "one"
    )
    return bool(row and row["is_blocked"])


def set_blocked(user_id, blocked: bool):
    execute(
        "UPDATE users SET is_blocked = ? WHERE user_id = ?",
        (1 if blocked else 0, user_id),
    )


# ---------------------------------------------------------------- referrals
def set_referrer_if_unset(user_id, referrer_id):
    """Record who referred this user, but only the first time (new users only)."""
    if user_id == referrer_id:
        return False
    with _lock:
        row = execute(
            "UPDATE users SET referred_by = ? "
            "WHERE user_id = ? AND referred_by IS NULL",
            (referrer_id, user_id),
        )
    return get_referrer(user_id) == referrer_id


def get_referrer(user_id):
    row = execute(
        "SELECT referred_by FROM users WHERE user_id = ?", (user_id,), "one"
    )
    return row["referred_by"] if row else None


def count_referrals(user_id):
    return execute(
        "SELECT COUNT(*) AS c FROM users WHERE referred_by = ?",
        (user_id,), "one",
    )["c"]


def mark_ref_bonus_paid(user_id):
    """Atomically mark this referred user's signup bonus as paid. True if applied
    (i.e. this call is the one that gets to pay it)."""
    with _lock:
        row = execute(
            "UPDATE users SET ref_bonus_paid = 1 "
            "WHERE user_id = ? AND COALESCE(ref_bonus_paid, 0) = 0 "
            "RETURNING user_id",
            (user_id,), "one",
        )
        return row is not None


# ---------------------------------------------------------------- favorites
def add_favorite(user_id, product_id):
    if IS_PG:
        execute(
            "INSERT INTO favorites (user_id, product_id, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT (user_id, product_id) DO NOTHING",
            (user_id, product_id, now()),
        )
    else:
        execute(
            "INSERT OR IGNORE INTO favorites (user_id, product_id, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, product_id, now()),
        )


def remove_favorite(user_id, product_id):
    execute(
        "DELETE FROM favorites WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    )


def is_favorite(user_id, product_id):
    return bool(execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND product_id = ?",
        (user_id, product_id), "one",
    ))


def list_favorites(user_id, limit=100, offset=0):
    return execute(
        """
        SELECT p.* FROM favorites f
        JOIN products p ON p.id = f.product_id
        WHERE f.user_id = ?
        ORDER BY f.id DESC LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset), "all",
    )


def count_favorites(user_id):
    return execute(
        "SELECT COUNT(*) AS c FROM favorites WHERE user_id = ?",
        (user_id,), "one",
    )["c"]


# ---------------------------------------------------------------- admin log
def log_admin_action(admin_id, action, detail=""):
    execute(
        "INSERT INTO admin_log (admin_id, action, detail, created_at) "
        "VALUES (?, ?, ?, ?)",
        (admin_id, action, detail, now()),
    )


def list_admin_log(limit=20, offset=0):
    return execute(
        "SELECT * FROM admin_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset), "all",
    )


def count_admin_log():
    return execute("SELECT COUNT(*) AS c FROM admin_log", (), "one")["c"]


# ---------------------------------------------------------------- categories
def add_category(name):
    row = execute(
        "INSERT INTO categories (name, sort_order, created_at) VALUES (?, ?, ?) "
        "RETURNING id",
        (name, 0, now()),
        "one",
    )
    return row["id"]


def list_categories():
    return execute(
        "SELECT * FROM categories ORDER BY sort_order ASC, id ASC", (), "all"
    )


def get_category(cid):
    return execute("SELECT * FROM categories WHERE id = ?", (cid,), "one")


def rename_category(cid, name):
    execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))


def delete_category(cid):
    execute("UPDATE products SET category_id = NULL WHERE category_id = ?", (cid,))
    execute("DELETE FROM categories WHERE id = ?", (cid,))


def categories_with_counts():
    return execute(
        """
        SELECT c.id, c.name,
               (SELECT COUNT(*) FROM products p
                 WHERE p.category_id = c.id AND p.is_active = 1) AS n
        FROM categories c
        ORDER BY c.sort_order ASC, c.id ASC
        """,
        (),
        "all",
    )


# ---------------------------------------------------------------- products
def add_product(title, description, price, photo_file_id, delivery_content,
                category_id, stock=-1):
    row = execute(
        """
        INSERT INTO products
            (category_id, title, description, price, photo_file_id,
             delivery_content, stock, is_active, sort_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        RETURNING id
        """,
        (category_id, title, description, price, photo_file_id,
         delivery_content, stock, now()),
        "one",
    )
    return row["id"]


def update_product(pid, **fields):
    allowed = {
        "category_id", "title", "description", "price", "photo_file_id",
        "delivery_content", "stock", "is_active", "sort_order", "icon",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(pid)
    execute(f"UPDATE products SET {', '.join(sets)} WHERE id = ?", vals)


def get_product(pid):
    return execute("SELECT * FROM products WHERE id = ?", (pid,), "one")


def delete_product(pid):
    execute("DELETE FROM products WHERE id = ?", (pid,))


SORT_OPTIONS = {
    "default": "sort_order ASC, id DESC",
    "price_asc": "price ASC, id DESC",
    "price_desc": "price DESC, id DESC",
    "newest": "id DESC",
}


def list_products(category_id=None, only_active=True, limit=100, offset=0,
                   search=None, sort="default"):
    where, params = [], []
    if only_active:
        where.append("is_active = 1")
    if category_id is not None:
        where.append("category_id = ?")
        params.append(category_id)
    if search:
        where.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = SORT_OPTIONS.get(sort, SORT_OPTIONS["default"])
    params += [limit, offset]
    return execute(
        f"SELECT * FROM products {clause} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        params,
        "all",
    )


def count_products(category_id=None, only_active=True, search=None):
    where, params = [], []
    if only_active:
        where.append("is_active = 1")
    if category_id is not None:
        where.append("category_id = ?")
        params.append(category_id)
    if search:
        where.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT COUNT(*) AS c FROM products {clause}", params, "one"
    )["c"]


# ---------------------------------------------------------------- orders
def create_order(user_id, username, product, network, address, price=None,
                  discount=0, coupon_code=None):
    """price overrides product['price'] when a discount has been applied."""
    final_price = product["price"] if price is None else price
    row = execute(
        """
        INSERT INTO orders
            (user_id, username, product_id, product_title, price, network,
             address, txid, status, note, created_at, updated_at,
             discount, coupon_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'awaiting_payment', NULL, ?, ?, ?, ?)
        RETURNING id
        """,
        (user_id, username, product["id"], product["title"], final_price,
         network, address, now(), now(), discount, coupon_code),
        "one",
    )
    return row["id"]


def get_order(oid):
    return execute("SELECT * FROM orders WHERE id = ?", (oid,), "one")


def set_order_txid(oid, txid):
    execute(
        "UPDATE orders SET txid = ?, status = 'pending', updated_at = ? "
        "WHERE id = ?",
        (txid, now(), oid),
    )


def set_order_status(oid, status, note=None):
    execute(
        "UPDATE orders SET status = ?, note = ?, updated_at = ? WHERE id = ?",
        (status, note, now(), oid),
    )


def set_order_status_from(oid, expected_statuses, status, note=None):
    """Compare-and-swap: only transitions if current status is in expected_statuses.

    Returns True if the update was applied (prevents double-approve/reject races).
    """
    placeholders = ", ".join(["?"] * len(expected_statuses))
    with _lock:
        row = execute(
            f"UPDATE orders SET status = ?, note = ?, updated_at = ? "
            f"WHERE id = ? AND status IN ({placeholders}) RETURNING id",
            (status, note, now(), oid, *expected_statuses),
            "one",
        )
        return row is not None


def txid_exists(txid):
    return bool(
        execute("SELECT 1 FROM orders WHERE txid = ?", (txid,), "one")
    )


def list_orders(status=None, user_id=None, limit=10, offset=0):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    return execute(
        f"SELECT * FROM orders {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params,
        "all",
    )


def count_orders(status=None, user_id=None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT COUNT(*) AS c FROM orders {clause}", params, "one"
    )["c"]


def search_orders(term, limit=10, offset=0):
    """Search orders by TXID (partial) or exact buyer user_id."""
    term = term.strip()
    where, params = ["(txid LIKE ? OR CAST(user_id AS TEXT) = ?)"], [f"%{term}%", term]
    clause = "WHERE " + " AND ".join(where)
    params += [limit, offset]
    return execute(
        f"SELECT * FROM orders {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params, "all",
    )


def count_search_orders(term):
    term = term.strip()
    row = execute(
        "SELECT COUNT(*) AS c FROM orders WHERE (txid LIKE ? OR CAST(user_id AS TEXT) = ?)",
        (f"%{term}%", term), "one",
    )
    return row["c"]


def revenue():
    row = execute(
        "SELECT COALESCE(SUM(price), 0) AS s FROM orders WHERE status = 'paid'",
        (),
        "one",
    )
    return float(row["s"] or 0)


def user_spent(user_id):
    row = execute(
        "SELECT COALESCE(SUM(price), 0) AS s FROM orders "
        "WHERE status = 'paid' AND user_id = ?",
        (user_id,),
        "one",
    )
    return float(row["s"] or 0)


# ---------------------------------------------------------------- coupons
def add_coupon(code, kind, amount, max_uses=-1, max_uses_per_user=1,
               min_order=0, expires_at=None):
    row = execute(
        """
        INSERT INTO coupons
            (code, kind, amount, max_uses, used_count, max_uses_per_user,
             min_order, expires_at, is_active, created_at)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?, 1, ?)
        RETURNING id
        """,
        (code.strip().upper(), kind, amount, max_uses, max_uses_per_user,
         min_order, expires_at, now()),
        "one",
    )
    return row["id"]


def get_coupon(code):
    return execute(
        "SELECT * FROM coupons WHERE code = ?", (code.strip().upper(),), "one"
    )


def get_coupon_by_id(cid):
    return execute("SELECT * FROM coupons WHERE id = ?", (cid,), "one")


def list_coupons(limit=50, offset=0):
    return execute(
        "SELECT * FROM coupons ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset), "all",
    )


def count_coupons():
    return execute("SELECT COUNT(*) AS c FROM coupons", (), "one")["c"]


def set_coupon_active(cid, active: bool):
    execute("UPDATE coupons SET is_active = ? WHERE id = ?",
            (1 if active else 0, cid))


def delete_coupon(cid):
    execute("DELETE FROM coupons WHERE id = ?", (cid,))


def user_coupon_use_count(coupon_id, user_id):
    return execute(
        "SELECT COUNT(*) AS c FROM coupon_uses WHERE coupon_id = ? AND user_id = ?",
        (coupon_id, user_id), "one",
    )["c"]


def redeem_coupon(coupon_id, user_id, order_id):
    """Atomically bump used_count and record the use. Caller must have already
    validated eligibility; this only guards the global max_uses race."""
    with _lock:
        c = get_coupon_by_id(coupon_id)
        if c["max_uses"] >= 0:
            row = execute(
                "UPDATE coupons SET used_count = used_count + 1 "
                "WHERE id = ? AND used_count < max_uses RETURNING id",
                (coupon_id,), "one",
            )
            if row is None:
                return False
        else:
            execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ?",
                (coupon_id,),
            )
        execute(
            "INSERT INTO coupon_uses (coupon_id, user_id, order_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (coupon_id, user_id, order_id, now()),
        )
        return True


def validate_coupon(code, user_id, order_amount):
    """Returns (coupon_row, error_message). coupon_row is None on error."""
    c = get_coupon(code)
    if not c or not c["is_active"]:
        return None, "Coupon not found."
    if c["expires_at"] and c["expires_at"] < now():
        return None, "This coupon has expired."
    if c["max_uses"] >= 0 and c["used_count"] >= c["max_uses"]:
        return None, "This coupon has reached its usage limit."
    if c["min_order"] and order_amount < c["min_order"]:
        return None, f"This coupon requires a minimum order of {c['min_order']:.2f}."
    if c["max_uses_per_user"] >= 0:
        used = user_coupon_use_count(c["id"], user_id)
        if used >= c["max_uses_per_user"]:
            return None, "You have already used this coupon."
    return c, None


def coupon_discount(coupon, order_amount):
    if coupon["kind"] == "percent":
        return round(order_amount * coupon["amount"] / 100, 2)
    return min(round(coupon["amount"], 2), order_amount)


# ---------------------------------------------------------------- balance
def get_balance(user_id):
    row = execute("SELECT balance FROM users WHERE user_id = ?", (user_id,), "one")
    return float(row["balance"] or 0) if row else 0.0


def adjust_balance(user_id, delta):
    """Atomically add delta (can be negative) to a user's balance."""
    with _lock:
        execute(
            "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id = ?",
            (delta, user_id),
        )
    return get_balance(user_id)


def deduct_balance_if_enough(user_id, amount):
    """Atomically deduct amount only if balance >= amount. Returns True if applied."""
    with _lock:
        row = execute(
            "UPDATE users SET balance = balance - ? "
            "WHERE user_id = ? AND COALESCE(balance, 0) >= ? "
            "RETURNING balance",
            (amount, user_id, amount),
            "one",
        )
        return row is not None


def restock_one(product_id):
    """Atomically add 1 back to stock (undo a reservation). No-op if unlimited."""
    with _lock:
        execute(
            "UPDATE products SET stock = stock + 1 "
            "WHERE id = ? AND stock >= 0",
            (product_id,),
        )


def decrement_stock_if_available(product_id):
    """Atomically decrement stock by 1 if stock > 0 or unlimited (<0).

    Returns True if the sale may proceed (stock decremented or unlimited).
    """
    with _lock:
        row = execute(
            "UPDATE products SET stock = stock - 1 "
            "WHERE id = ? AND stock > 0 RETURNING id",
            (product_id,),
            "one",
        )
        if row is not None:
            return True
        p = get_product(product_id)
        return bool(p and p["stock"] < 0)


# ---------------------------------------------------------------- topups
def create_topup(user_id, username, amount, network, address):
    row = execute(
        """
        INSERT INTO topups
            (user_id, username, amount, network, address, txid, status, note,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, 'awaiting_payment', NULL, ?, ?)
        RETURNING id
        """,
        (user_id, username, amount, network, address, now(), now()),
        "one",
    )
    return row["id"]


def get_topup(tid):
    return execute("SELECT * FROM topups WHERE id = ?", (tid,), "one")


def set_topup_txid(tid, txid):
    execute(
        "UPDATE topups SET txid = ?, status = 'pending', updated_at = ? "
        "WHERE id = ?",
        (txid, now(), tid),
    )


def set_topup_status(tid, status, note=None):
    execute(
        "UPDATE topups SET status = ?, note = ?, updated_at = ? WHERE id = ?",
        (status, note, now(), tid),
    )


def set_topup_status_from(tid, expected_statuses, status, note=None):
    """Compare-and-swap: only transitions if current status is in expected_statuses.

    Returns True if the update was applied (prevents double-approve/reject races).
    """
    placeholders = ", ".join(["?"] * len(expected_statuses))
    with _lock:
        row = execute(
            f"UPDATE topups SET status = ?, note = ?, updated_at = ? "
            f"WHERE id = ? AND status IN ({placeholders}) RETURNING id",
            (status, note, now(), tid, *expected_statuses),
            "one",
        )
        return row is not None


def topup_txid_exists(txid):
    return bool(execute("SELECT 1 FROM topups WHERE txid = ?", (txid,), "one"))


def list_topups(status=None, user_id=None, limit=10, offset=0):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    return execute(
        f"SELECT * FROM topups {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params,
        "all",
    )


def count_topups(status=None, user_id=None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT COUNT(*) AS c FROM topups {clause}", params, "one"
    )["c"]


def search_topups(term, limit=10, offset=0):
    """Search top-ups by TXID (partial) or exact user_id."""
    term = term.strip()
    where, params = ["(txid LIKE ? OR CAST(user_id AS TEXT) = ?)"], [f"%{term}%", term]
    clause = "WHERE " + " AND ".join(where)
    params += [limit, offset]
    return execute(
        f"SELECT * FROM topups {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params, "all",
    )


def count_search_topups(term):
    term = term.strip()
    row = execute(
        "SELECT COUNT(*) AS c FROM topups WHERE (txid LIKE ? OR CAST(user_id AS TEXT) = ?)",
        (f"%{term}%", term), "one",
    )
    return row["c"]


# ---------------------------------------------------------------- tickets
def create_ticket(user_id, username):
    row = execute(
        """
        INSERT INTO tickets (user_id, username, status, last_msg_preview,
                              created_at, updated_at)
        VALUES (?, ?, 'open', NULL, ?, ?)
        RETURNING id
        """,
        (user_id, username, now(), now()),
        "one",
    )
    return row["id"]


def get_ticket(tid):
    return execute("SELECT * FROM tickets WHERE id = ?", (tid,), "one")


def get_open_ticket(user_id):
    return execute(
        "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
        "one",
    )


def close_ticket(tid):
    execute(
        "UPDATE tickets SET status = 'closed', updated_at = ? WHERE id = ?",
        (now(), tid),
    )


def touch_ticket(tid, preview):
    execute(
        "UPDATE tickets SET last_msg_preview = ?, updated_at = ? WHERE id = ?",
        (preview[:120], now(), tid),
    )


def list_tickets(status=None, limit=10, offset=0):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    return execute(
        f"SELECT * FROM tickets {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params,
        "all",
    )


def count_tickets(status=None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT COUNT(*) AS c FROM tickets {clause}", params, "one"
    )["c"]


def add_ticket_message(ticket_id, sender, body):
    execute(
        "INSERT INTO ticket_messages (ticket_id, sender, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (ticket_id, sender, body, now()),
    )
    touch_ticket(ticket_id, body)


def list_ticket_messages(ticket_id, limit=20):
    rows = execute(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (ticket_id, limit),
        "all",
    )
    return list(reversed(rows))
