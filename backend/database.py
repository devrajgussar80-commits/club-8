"""Postgres (Neon) database layer and schema for the Club 8 API.

This replaced SQLite. Two shims keep the ~60 existing call sites unchanged:

* `Row` supports both `row["col"]` and `row[0]`, like `sqlite3.Row` did.
* `execute()` rewrites `?` placeholders to psycopg's `%s`.

Connections come from a pool. Neon is serverless, so every fresh connect pays
a TLS handshake and the free tier caps concurrent connections -- opening one
per request without pooling is what makes an app there feel slow.
"""

import os
import time

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
            # Long enough to survive a Neon cold start, short enough that a
            # genuinely wrong URL still fails instead of hanging forever.
            # Neon suspends an idle compute and the first connection after
            # that takes ~25-30s to wake it; a 10s budget here meant the app
            # crashed on boot and the first dashboard click timed out every
            # time the database had been quiet for a few minutes.
            timeout=45,
            # No "options=-c search_path" here: Neon's pgbouncer pooler rejects
            # the `options` startup parameter outright ("unsupported startup
            # parameter"), which shows up as a pool timeout rather than a clear
            # error. The schema is set per connection in _configure instead.
            kwargs={"row_factory": _row_factory, "connect_timeout": 30},
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

-- Multiplayer Dice: one shared 30-second round, like WinGo. Bets land in
-- dice_bets during the window; at close the server rolls one face and settles
-- every bet against it. Kept in Postgres (not memory) so a restart mid-round
-- neither loses a debited stake nor pays one twice.
CREATE TABLE IF NOT EXISTS dice_rounds (
    period TEXT PRIMARY KEY,
    face INTEGER,                    -- rolled face 1-6, NULL until settled
    status TEXT DEFAULT 'open',      -- open -> settled
    created_at TIMESTAMPTZ DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dice_bets (
    id TEXT PRIMARY KEY,
    period TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name TEXT,
    bet_type TEXT NOT NULL,          -- number | parity | half
    selection TEXT NOT NULL,         -- 1-6 | odd/even | low/high
    amount DOUBLE PRECISION NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending -> won | lost
    payout DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dice_bets_period ON dice_bets(period);
CREATE INDEX IF NOT EXISTS idx_dice_bets_user ON dice_bets(user_id, created_at DESC);

-- The Android APK, uploaded from the admin dashboard and served from here.
-- Kept in Postgres (not on disk) for the same reason as the QR images: the
-- host wipes its filesystem on every deploy. A single row, id = 'current',
-- holds whatever the admin last uploaded.
CREATE TABLE IF NOT EXISTS app_downloads (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    version TEXT,
    content_type TEXT,
    size_bytes BIGINT,
    data BYTEA NOT NULL,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Referrals. `referral_code` is repurposed to be each user's OWN unique
-- share code (backfilled below); `referred_by` records the code they entered
-- at signup. The join between them lives in the referrals table so a reward
-- has one row with one status, rather than being inferred from two columns.
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by TEXT;
-- Team accounts: 0 is a normal player. Above 0 is the target win rate (%), so
-- ~this fraction of their losing single-player rounds are re-drawn as genuine
-- wins. Only single-player games can honour it -- shared-round games (WinGo,
-- multiplayer Dice) deal one result to everyone.
ALTER TABLE users ADD COLUMN IF NOT EXISTS team_win_rate DOUBLE PRECISION DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code
    ON users(referral_code) WHERE referral_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS referrals (
    id TEXT PRIMARY KEY,
    referrer_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- A user can be referred by at most one person, ever.
    referred_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    referred_name TEXT,
    referred_phone TEXT,
    -- signed_up  : joined with the code, no qualifying deposit yet
    -- deposited  : referred user's first deposit approved -> reward is claimable
    -- approved   : admin released the reward, referrer credited (terminal)
    -- rejected   : admin declined the reward (terminal)
    status TEXT DEFAULT 'signed_up',
    reward DOUBLE PRECISION DEFAULT 50,
    qualified_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status);

-- Uploaded QR images live in the database, not on the host's filesystem.
-- Render (and most PaaS free tiers) wipe the disk on every deploy, which
-- would silently take the deposit QR down and break payments until an admin
-- noticed and re-uploaded it. Postgres is the one place that survives.
ALTER TABLE qr_codes ADD COLUMN IF NOT EXISTS image_data BYTEA;
ALTER TABLE qr_codes ADD COLUMN IF NOT EXISTS image_type TEXT;

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

-- Anonymous visitor tracking. A row appears the moment someone lands on the
-- site, long before they have an account, so `user_id` stays NULL until they
-- register or log in -- that transition is exactly what the funnel measures.
CREATE TABLE IF NOT EXISTS visitor_sessions (
    -- Both ids come from the browser: `id` is per visit (sessionStorage),
    -- `visitor_id` survives across visits (localStorage), which is what makes
    -- a returning stranger recognisable without an account.
    id TEXT PRIMARY KEY,
    visitor_id TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    ip TEXT,
    user_agent TEXT,
    browser TEXT,
    os TEXT,
    device TEXT,
    referrer TEXT,
    landing_path TEXT,
    last_path TEXT,
    page_views INTEGER DEFAULT 0,
    -- Seconds the tab was actually VISIBLE, summed from client heartbeats.
    -- Wall-clock between first and last event would count a tab left open in
    -- the background as engagement, which is the number people misread most.
    active_seconds INTEGER DEFAULT 0,
    -- browsing | registered | logged_in | abandoned
    outcome TEXT DEFAULT 'browsing',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS visitor_events (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES visitor_sessions(id) ON DELETE CASCADE,
    visitor_id TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT,
    meta JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visitor_sessions_seen
    ON visitor_sessions(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitor_sessions_visitor
    ON visitor_sessions(visitor_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitor_events_session
    ON visitor_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_visitor_events_visitor
    ON visitor_events(visitor_id, created_at DESC);

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


# Boot happens once; a suspended Neon compute can take the best part of a
# minute to wake, and how long varies with how long it has been idle. Retrying
# is what makes that a slow start instead of a failed deploy -- chasing it by
# raising the timeout just moves the cliff.
BOOT_ATTEMPTS = 3
BOOT_BACKOFF_SECONDS = 5


def init_db():
    conn = None
    last_error = None
    for attempt in range(1, BOOT_ATTEMPTS + 1):
        try:
            conn = get_db_connection()
            break
        except PoolTimeout as exc:
            last_error = exc
            if attempt < BOOT_ATTEMPTS:
                print(
                    f"Postgres not ready (attempt {attempt}/{BOOT_ATTEMPTS}); "
                    f"retrying in {BOOT_BACKOFF_SECONDS}s — this is normal for a "
                    f"Neon compute waking from idle."
                )
                time.sleep(BOOT_BACKOFF_SECONDS)

    if conn is None:
        # Every attempt waited out a full cold start, so this really is a wrong
        # or unreachable URL rather than a sleeping compute. Say so, rather
        # than letting a bare PoolTimeout reach the deploy logs.
        raise RuntimeError(
            "Could not reach Postgres. Check DATABASE_URL — it should be the "
            "pooled Neon string ending in ?sslmode=require."
        ) from last_error

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

    # Give every existing user an own referral code and migrate legacy rows.
    # Imported here, not at module top, to avoid a circular import.
    import referrals_core

    referrals_core.migrate_and_backfill(conn)
    conn.close()


# Re-exported so callers can catch a duplicate-key conflict without importing
# psycopg themselves. `rounds.period` uses it to claim a round exactly once.
UniqueViolation = psycopg.errors.UniqueViolation


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
