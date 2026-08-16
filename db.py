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

    # migration: older DBs created before `balance` existed on users
    has_balance = (
        _sqlite_has_column("users", "balance") if not IS_PG
        else _pg_has_column("users", "balance")
    )
    if not has_balance:
        col_type = "DOUBLE PRECISION" if IS_PG else "REAL"
        execute(f"ALTER TABLE users ADD COLUMN balance {col_type} DEFAULT 0")


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
        "delivery_content", "stock", "is_active", "sort_order",
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


def list_products(category_id=None, only_active=True, limit=100, offset=0):
    where, params = [], []
    if only_active:
        where.append("is_active = 1")
    if category_id is not None:
        where.append("category_id = ?")
        params.append(category_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    return execute(
        f"SELECT * FROM products {clause} "
        f"ORDER BY sort_order ASC, id DESC LIMIT ? OFFSET ?",
        params,
        "all",
    )


def count_products(category_id=None, only_active=True):
    where, params = [], []
    if only_active:
        where.append("is_active = 1")
    if category_id is not None:
        where.append("category_id = ?")
        params.append(category_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT COUNT(*) AS c FROM products {clause}", params, "one"
    )["c"]


# ---------------------------------------------------------------- orders
def create_order(user_id, username, product, network, address):
    row = execute(
        """
        INSERT INTO orders
            (user_id, username, product_id, product_title, price, network,
             address, txid, status, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'awaiting_payment', NULL, ?, ?)
        RETURNING id
        """,
        (user_id, username, product["id"], product["title"], product["price"],
         network, address, now(), now()),
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
