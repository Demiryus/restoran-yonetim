import sqlite3
import os
from pathlib import Path

_raw = os.getenv("DB_PATH", "restoran.db")
DATABASE_PATH = str(Path(_raw))
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()

    # Step 1: Base tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS receipts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT,
            telegram_username TEXT,
            photo_path       TEXT,
            store_name       TEXT,
            receipt_date     TEXT,
            total_amount     REAL DEFAULT 0,
            tax_amount       REAL DEFAULT 0,
            currency         TEXT DEFAULT 'CAD',
            type             TEXT DEFAULT 'expense',
            raw_ai_response  TEXT,
            created_at       TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS receipt_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id   INTEGER NOT NULL,
            item_name    TEXT,
            category     TEXT,
            quantity     REAL DEFAULT 0,
            unit         TEXT,
            unit_price   REAL DEFAULT 0,
            total_price  REAL DEFAULT 0,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS stock (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name        TEXT UNIQUE NOT NULL,
            category         TEXT,
            current_quantity REAL DEFAULT 0,
            unit             TEXT,
            min_quantity     REAL DEFAULT 0,
            last_updated     TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS income (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            amount      REAL NOT NULL,
            description TEXT,
            currency    TEXT DEFAULT 'CAD',
            income_date TEXT DEFAULT (date('now','localtime')),
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS manual_expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            amount       REAL NOT NULL,
            description  TEXT,
            category     TEXT DEFAULT 'other',
            expense_date TEXT DEFAULT (date('now','localtime')),
            tax_amount   REAL DEFAULT 0,
            is_recurring INTEGER DEFAULT 0,
            recur_day    INTEGER,
            created_at   TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            category      TEXT UNIQUE NOT NULL,
            monthly_limit REAL NOT NULL,
            scope         TEXT DEFAULT 'receipt',
            updated_at    TEXT DEFAULT (datetime('now','localtime'))
        );
    """)

    # Step 2: Migrate existing tables — add new columns safely
    migrations = [
        ("receipts",         "parse_status", "TEXT DEFAULT 'success'"),
        ("receipts",         "parse_error",  "TEXT"),
        ("receipts",         "tax_amount",   "REAL DEFAULT 0"),
    ]
    for table, col, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass

    # Step 3: Indexes
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_receipts_created   ON receipts(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_receipts_status    ON receipts(parse_status);
        CREATE INDEX IF NOT EXISTS idx_receipt_items_rid  ON receipt_items(receipt_id);
        CREATE INDEX IF NOT EXISTS idx_receipt_items_name ON receipt_items(item_name);
        CREATE INDEX IF NOT EXISTS idx_ri_item_lower      ON receipt_items(lower(item_name));
        CREATE INDEX IF NOT EXISTS idx_stock_name         ON stock(item_name);
        CREATE INDEX IF NOT EXISTS idx_income_date        ON income(income_date DESC);
        CREATE INDEX IF NOT EXISTS idx_manual_exp_date    ON manual_expenses(expense_date DESC);
        CREATE INDEX IF NOT EXISTS idx_manual_exp_cat     ON manual_expenses(category);
    """)

    conn.commit()
    conn.close()
    print("Veritabani hazir.")
