"""
SQLite Database Layer & Models for Color Prediction Platform
"""

import sqlite3
import os
import json
from datetime import datetime

DB_FILE = os.environ.get(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(__file__), "color_prediction.db"),
)

def get_db_connection():
    db_directory = os.path.dirname(os.path.abspath(DB_FILE))
    os.makedirs(db_directory, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Users Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        phone TEXT UNIQUE NOT NULL,
        username TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        balance REAL DEFAULT 1000.0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        referral_code TEXT,
        game_access_enabled INTEGER DEFAULT 0
    );
    """)

    user_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
    if "game_access_enabled" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN game_access_enabled INTEGER DEFAULT 0")
        # Preserve access for accounts that existed before the recharge gate was introduced.
        cursor.execute("UPDATE users SET game_access_enabled = 1")

    # 2. Rounds Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rounds (
        period TEXT PRIMARY KEY,
        room TEXT NOT NULL,
        winning_number INTEGER,
        winning_color TEXT,
        winning_size TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Bets Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bets (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        period TEXT NOT NULL,
        select_type TEXT NOT NULL,
        selection TEXT NOT NULL,
        amount REAL NOT NULL,
        multiplier INTEGER NOT NULL,
        total_stake REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        payout REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    # 4. UPI Deposits Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS upi_deposits (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        amount REAL NOT NULL,
        utr TEXT UNIQUE NOT NULL,
        receipt_url TEXT,
        status TEXT DEFAULT 'pending',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    # 5. UPI Withdrawals Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS upi_withdrawals (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        amount REAL NOT NULL,
        upi_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    # 6. QR Codes Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qr_codes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        note TEXT,
        qr_url TEXT NOT NULL,
        upi_id TEXT,
        min_amount REAL DEFAULT 100,
        max_amount REAL DEFAULT 50000,
        is_active INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Backward-compatible migrations for databases created by older builds.
    qr_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(qr_codes)").fetchall()}
    for column_name, definition in {
        "upi_id": "TEXT",
        "min_amount": "REAL DEFAULT 100",
        "max_amount": "REAL DEFAULT 50000",
        "is_active": "INTEGER DEFAULT 0",
    }.items():
        if column_name not in qr_columns:
            cursor.execute(f"ALTER TABLE qr_codes ADD COLUMN {column_name} {definition}")

    deposit_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(upi_deposits)").fetchall()}
    for column_name, definition in {
        "qr_id": "TEXT",
        "order_id": "TEXT",
        "processed_at": "TIMESTAMP",
        "admin_note": "TEXT",
    }.items():
        if column_name not in deposit_columns:
            cursor.execute(f"ALTER TABLE upi_deposits ADD COLUMN {column_name} {definition}")

    withdrawal_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(upi_withdrawals)").fetchall()}
    for column_name, definition in {
        "processed_at": "TIMESTAMP",
        "admin_note": "TEXT",
    }.items():
        if column_name not in withdrawal_columns:
            cursor.execute(f"ALTER TABLE upi_withdrawals ADD COLUMN {column_name} {definition}")

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_upi_deposits_order_id ON upi_deposits(order_id) WHERE order_id IS NOT NULL")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_upi_deposits_user_time ON upi_deposits(user_id, timestamp DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_upi_withdrawals_user_time ON upi_withdrawals(user_id, timestamp DESC)")

    # 7. System Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    conn.commit()

    # Seed Default Data if empty
    # Seed Admin QR codes
    cursor.execute("SELECT COUNT(*) FROM qr_codes")
    if cursor.fetchone()[0] == 0:
        default_qrs = [
            ("QR-101", "Primary PhonePe UPI", "Scan using PhonePe or Google Pay", "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=adminphonepe@upi&pn=ColorPredictPrimary"),
            ("QR-102", "Fast Paytm QR Code", "Fast instant auto-crediting QR", "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=adminpaytm@upi&pn=ColorPredictFast")
        ]
        for qid, qname, qnote, qurl in default_qrs:
            cursor.execute("INSERT INTO qr_codes (id, name, note, qr_url) VALUES (?, ?, ?, ?)", (qid, qname, qnote, qurl))
        conn.commit()

    # One-time project migration: install the owner-provided Slice QR as active.
    slice_qr = cursor.execute("SELECT id FROM qr_codes WHERE id = 'QR-SLICE'").fetchone()
    if not slice_qr:
        cursor.execute("UPDATE qr_codes SET is_active = 0")
        cursor.execute(
            """INSERT INTO qr_codes
            (id, name, note, qr_url, upi_id, min_amount, max_amount, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                "QR-SLICE",
                "Slice UPI",
                "Pay the exact order amount",
                "https://club-8.vercel.app/assets/slice-deposit-qr.png",
                "neogen@slc",
                100,
                50000,
            ),
        )
        conn.commit()

    # Keep exactly one QR active. The newest admin upload becomes active later.
    cursor.execute("SELECT COUNT(*) FROM qr_codes WHERE is_active = 1")
    if cursor.fetchone()[0] == 0:
        first_qr = cursor.execute("SELECT id FROM qr_codes ORDER BY created_at DESC LIMIT 1").fetchone()
        if first_qr:
            cursor.execute("UPDATE qr_codes SET is_active = 1 WHERE id = ?", (first_qr["id"],))
            conn.commit()

    # Seed System Settings
    settings = {
        "prediction_mode": "auto_least", # 'auto_least' | 'manual' | 'random'
        "forced_number": "7",
        "bot_simulator_enabled": "true",
        "admin_upi_id": "adminpay@upi",
        "deposits_enabled": "true",
        "withdrawals_enabled": "true",
        "withdrawal_min": "200"
    }

    for k, v in settings.items():
        cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()

    # Seed Demo Player User if empty. The placeholder hash matches no password;
    # 'demo_pass_hash' used to be accepted as a login bypass for this account.
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO users (id, phone, username, password_hash, balance, status, referral_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('USR9842', '+91 98765 43210', 'Lucky Player', '!no-login', 1000.0, 'active', 'REF9842'))
        conn.commit()

    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
