import sqlite3
from datetime import datetime

DATABASE_PATH = "restoran.db"

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS receipts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT,
            telegram_username TEXT,
            photo_path      TEXT,
            store_name      TEXT,
            receipt_date    TEXT,
            total_amount    REAL DEFAULT 0,
            currency        TEXT DEFAULT 'CAD',
            type            TEXT DEFAULT 'expense',
            raw_ai_response TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
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
    """)
    conn.commit()
    conn.close()
    print("Veritabani hazir.")
