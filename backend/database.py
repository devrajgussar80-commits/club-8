"""Postgres (Neon) database layer and schema for the Club 8 API.

This replaced SQLite. Two shims keep the ~60 existing call sites unchanged:

* `Row` supports both `row["col"]` and `row[0]`, like `sqlite3.Row` did.
* `execute()` rewrites `?` placeholders to psycopg's `%s`.

Connections come from a pool. Neon is serverless, so every fresh connect pays
a TLS handshake and the free tier caps concurrent connections -- opening one
per request without pooling is what makes an app there feel slow.
"""

import os

import env_file  # noqa: F401  -- loads .env.local before DATABASE_URL is read

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

# Neon gives two connection strings. Use the POOLED one (host contains
# "-pooler") so Neon's own pgbouncer sits in front of this pool.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Tests point this at a throwaway schema so they never touch real tables.
DB_SCHEMA = os.environ.get("DB_SCHEMA", "public")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy the pooled connection string from the "
        "Neon dashboard into your .env.local (or the host's environment)."
    )


class Row(dict):
    """Mapping row that also allows positional access.

    `SELECT COUNT(*)` call sites read `row[0]`, while everything else reads
    `row["column"]`. sqlite3.Row supported both; plain dicts do not.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    make_dict = dict_row(cursor)

    def make_row(values):
        return Row(make_dict(values))

    return make_row


def _translate(sql: str, params):
    """Rewrite `?` placeholders to `%s`.

    psycopg only treats `%` as special when parameters are supplied, so the
    escaping is skipped for parameterless statements -- otherwise a literal
    `%%` would survive into the query text.
    """
    if params is None:
        return sql
    return sql.replace("%", "%%").replace("?", "%s")


class _Cursor:
    """Thin wrapper giving psycopg's cursor the sqlite3 call shape."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        self._cursor.execute(_translate(sql, params), params)
        # sqlite3 returns the cursor so `.execute(...).fetchall()` chains.
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _Connection:
    """Pooled connection that is released, not closed, on `close()`."""

    def __init__(self, connection):
        self._connection = connection
        self._closed = False

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def cursor(self):
        return _Cursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        # Roll back anything uncommitted so the connection goes back to the
        # pool clean instead of holding an idle-in-transaction lock.
        try:
            self._connection.rollback()
        finally:
            _pool().putconn(self._connection)


def _configure(connection) -> None:
    """Point a freshly opened connection at DB_SCHEMA.

    Production runs on `public`, so this is a no-op there. Tests set a
    throwaway schema, and for those TEST_DATABASE_URL must be Neon's *direct*
    string (host without "-pooler") -- in the pooler's transaction mode a SET
    does not survive between transactions.
    """
    if DB_SCHEMA != "public":
        connection.execute(f'SET search_path TO "{DB_SCHEMA}"')
        connection.commit()


_POOL = None


def _pool() -> ConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_MAX", "10")),
            # Fail fast on a wrong DATABASE_URL. The default waits 30s per
            # call, which on a bad deploy just looks like the app hanging.
            timeout=10,
            # No "options=-c search_path" here: Neon's pgbouncer pooler rejects
            # the `options` startup parameter outright ("unsupported startup
            # parameter"), which shows up as a pool timeout rather than a clear
            # error. The schema is set per connection in _configure instead.
            kwargs={"row_factory": _row_factory, "connect_timeout": 10},
            configure=_configure,
            # Neon scales the compute to zero when idle and drops the sockets
            # with it. Without this check the pool hands out a dead connection
            # and the next query fails with "server closed the connection
            # unexpectedly"; with it, the pool discards and reconnects.
            check=ConnectionPool.check_connection,
            # Same reason: do not keep a connection alive for hours hoping it
            # survives, recycle it well before Neon's idle timeout.
            max_idle=300,
            open=True,
        )
    return _POOL


def get_db_connection() -> _Connection:
    return _Connection(_pool().getconn())


def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    balance DOUBLE PRECISION DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    referral_code TEXT,
    -- Kept as INTEGER rather than BOOLEAN so the existing `= 1` comparisons
    -- across the routers keep working unchanged.
    game_access_enabled INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rounds (
    period TEXT PRIMARY KEY,
    room TEXT NOT NULL,
    winning_number INTEGER,
    winning_color TEXT,
    winning_size TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period TEXT NOT NULL,
    select_type TEXT NOT NULL,
    selection TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    multiplier INTEGER NOT NULL,
    total_stake DOUBLE PRECISION NOT NULL,
    status TEXT DEFAULT 'pending',
    payout DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS upi_deposits (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    utr TEXT UNIQUE NOT NULL,
    receipt_url TEXT,
    status TEXT DEFAULT 'pending',
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    qr_id TEXT,
    order_id TEXT,
    processed_at TIMESTAMPTZ,
    admin_note TEXT
);

CREATE TABLE IF NOT EXISTS upi_withdrawals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    upi_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    admin_note TEXT
);

CREATE TABLE IF NOT EXISTS qr_codes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT,
    qr_url TEXT NOT NULL,
    upi_id TEXT,
    min_amount DOUBLE PRECISION DEFAULT 100,
    max_amount DOUBLE PRECISION DEFAULT 50000,
    is_active INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deposit_orders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    qr_id TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    consumed INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Every settled arcade round, one row. This is the only source the admin
-- games analytics reads from, so each game router must write here even when
-- the payout is zero -- otherwise the game looks like it earns nothing.
CREATE TABLE IF NOT EXISTS game_rounds (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stake DOUBLE PRECISION NOT NULL,
    payout DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- Whatever the game needs to redraw the round (reels, wheel pocket, lane).
    outcome JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- One draw per calendar date. `winning_ticket` stays NULL until the admin
-- picks it, which is what "open" means for ticket sales.
CREATE TABLE IF NOT EXISTS lottery_draws (
    draw_date DATE PRIMARY KEY,
    status TEXT DEFAULT 'open',
    winning_ticket INTEGER,
    drawn_at TIMESTAMPTZ,
    ticket_price DOUBLE PRECISION DEFAULT 100,
    prize_amount DOUBLE PRECISION DEFAULT 1000
);

CREATE TABLE IF NOT EXISTS lottery_tickets (
    id TEXT PRIMARY KEY,
    draw_date DATE NOT NULL REFERENCES lottery_draws(draw_date) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    ticket_number INTEGER NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    -- pending -> approved (admin confirmed the UPI payment) | rejected
    status TEXT DEFAULT 'pending',
    utr TEXT,
    qr_id TEXT,
    -- Set once the admin has topped the winner's wallet up, so the prize
    -- cannot be paid twice by clicking the button again.
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- A ticket number belongs to exactly one player per draw, and only an
-- approved (paid) ticket reserves it -- rejected ones free the number again.
CREATE UNIQUE INDEX IF NOT EXISTS idx_lottery_ticket_claim
    ON lottery_tickets(draw_date, ticket_number) WHERE status <> 'rejected';
CREATE INDEX IF NOT EXISTS idx_lottery_tickets_user ON lottery_tickets(user_id);
-- The same UPI reference cannot pay for two tickets.
CREATE UNIQUE INDEX IF NOT EXISTS idx_lottery_tickets_utr
    ON lottery_tickets(utr) WHERE utr IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_game_rounds_game_time ON game_rounds(game, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_game_rounds_user ON game_rounds(user_id, created_at DESC);

-- One deposit per order. This is what stops a player submitting the same
-- order twice and getting credited twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_upi_deposits_order_id
    ON upi_deposits(order_id) WHERE order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_upi_deposits_user_time
    ON upi_deposits(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_upi_withdrawals_user_time
    ON upi_withdrawals(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_bets_period ON bets(period);
"""

DEFAULT_QRS = [
    (
        "QR-101",
        "Primary UPI QR",
        "Scan with any UPI app",
        "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=admin@upi&pn=Club8Primary",
    ),
    (
        "QR-102",
        "Secondary UPI QR",
        "Fast instant auto-crediting QR",
        "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=admin2@upi&pn=Club8Fast",
    ),
]

DEFAULT_SETTINGS = {
    "prediction_mode": "auto_least",  # 'auto_least' | 'manual' | 'random'
    "forced_number": "7",
    "deposits_enabled": "true",
    "withdrawals_enabled": "true",
    "withdrawal_min": "200",
}


def init_db():
    try:
        conn = get_db_connection()
    except PoolTimeout as exc:
        # Almost always a wrong or unreachable DATABASE_URL. Say so, rather
        # than letting a bare PoolTimeout reach the deploy logs.
        raise RuntimeError(
            "Could not reach Postgres. Check DATABASE_URL — it should be the "
            "pooled Neon string ending in ?sslmode=require."
        ) from exc

    cursor = conn.cursor()

    if DB_SCHEMA != "public":
        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')

    cursor.execute(SCHEMA)

    # Seed default QR codes
    if cursor.execute("SELECT COUNT(*) FROM qr_codes").fetchone()[0] == 0:
        for qid, qname, qnote, qurl in DEFAULT_QRS:
            cursor.execute(
                "INSERT INTO qr_codes (id, name, note, qr_url) VALUES (?, ?, ?, ?)",
                (qid, qname, qnote, qurl),
            )

    # Keep exactly one QR active. Newest admin upload becomes active later.
    if cursor.execute("SELECT COUNT(*) FROM qr_codes WHERE is_active = 1").fetchone()[0] == 0:
        first_qr = cursor.execute(
            "SELECT id FROM qr_codes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if first_qr:
            cursor.execute("UPDATE qr_codes SET is_active = 1 WHERE id = ?", (first_qr["id"],))

    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
            (key, value),
        )

    conn.commit()
    conn.close()


# Re-exported so callers can catch a duplicate-key conflict without importing
# psycopg themselves. `rounds.period` uses it to claim a round exactly once.
UniqueViolation = psycopg.errors.UniqueViolation


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
