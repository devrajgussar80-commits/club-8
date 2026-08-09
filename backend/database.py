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

import luck  # both are imported only for their default settings; neither
import settings_store  # imports this module

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


# Every column of qr_codes except the uploaded image itself.
#
# Never "SELECT *" from this table: image_data is raw bytes, and bytes in a
# JSON response cannot be encoded -- once an admin uploaded a QR, the dashboard
# returned 500 and the whole console came up empty. Callers get a has_image
# flag instead; the image is served by /api/qr-image/{id}.
QR_CODE_COLUMNS = (
    "id, name, note, qr_url, upi_id, min_amount, max_amount, is_active, "
    "created_at, last_used_at, (image_data IS NOT NULL) AS has_image, image_type"
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
        self._readonly = False

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
        try:
            if self._readonly:
                # Nothing to roll back: an autocommit connection never opened a
                # transaction. Skipping it saves a network round trip, and put
                # the connection back the way the pool handed it over.
                self._connection.autocommit = False
            else:
                # Roll back anything uncommitted so the connection goes back to
                # the pool clean instead of holding an idle-in-transaction lock.
                self._connection.rollback()
        finally:
            self._connection._club8_returned_at = time.monotonic()
            _pool().putconn(self._connection)


# How long a connection may sit unused before it has to prove it is alive.
# Neon only drops sockets after minutes of inactivity, so anything handed back
# within this window is trusted without a probe.
CHECK_AFTER_IDLE = 30.0


def _check_if_idle(connection) -> None:
    """Pool health check that skips the probe on a warm connection.

    `ConnectionPool.check_connection` runs a query on every single checkout,
    which is a full round trip to a database on another host -- on the game
    state endpoints, polled every couple of seconds, that probe was about half
    the response time. A connection returned to the pool a moment ago has not
    had time to die, so it is only re-probed once it has been sitting idle long
    enough for Neon to have suspended underneath it.
    """
    last = getattr(connection, "_club8_returned_at", None)
    if last is not None and (time.monotonic() - last) < CHECK_AFTER_IDLE:
        return
    ConnectionPool.check_connection(connection)


def _configure(connection) -> None:
    """Point a freshly opened connection at DB_SCHEMA.

    Set unconditionally, `public` included. It used to be skipped there on the
    grounds that public is already the default -- but the default is a
    property of the *server* connection, and behind Neon's pgbouncer those are
    handed round between clients. One process that runs `SET search_path` to
    something else (the test suite, pointed at a throwaway schema) leaves that
    setting on the server connection, and the next client to be given it
    inherits a search_path naming a schema that may not even exist any more.
    Every query then fails with "relation ... does not exist" on a database
    where the table is perfectly intact.

    Two lines to make the app state its own schema rather than inherit
    whatever the last tenant left behind.

    Tests still want the *direct* string (host without "-pooler") in
    TEST_DATABASE_URL: in the pooler's transaction mode a SET does not survive
    between transactions, so a throwaway schema cannot be held that way.
    """
    connection.execute(f'SET search_path TO "{DB_SCHEMA}"')
    connection.commit()


_POOL = None


def _pool() -> ConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = ConnectionPool(
            DATABASE_URL,
            # Opening a Neon connection costs a TLS handshake, about two
            # seconds. One request needs two of them (the auth lookup, then the
            # work), and the background round clock holds one while it settles,
            # so a pool that started at one had callers paying that handshake
            # mid-request. Keep a few warm instead.
            min_size=int(os.environ.get("DB_POOL_MIN", "4")),
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
            check=_check_if_idle,
            # Same reason: do not keep a connection alive for hours hoping it
            # survives, recycle it well before Neon's idle timeout.
            max_idle=300,
            open=True,
        )
    return _POOL


def get_db_connection(readonly: bool = False) -> _Connection:
    """Check a connection out of the pool.

    `readonly=True` runs it in autocommit. Psycopg opens a transaction on the
    first statement otherwise, which costs a BEGIN on the way in and a ROLLBACK
    on the way back to the pool -- two extra round trips to a database on
    another host, for a request that only ever reads. On the polled game state
    endpoints that was over half the response time.

    Only pass it when the work genuinely cannot write: without a transaction
    there is nothing to roll back, so a multi-statement write would be able to
    land half-applied.
    """
    conn = _Connection(_pool().getconn())
    if readonly:
        conn._readonly = True
        conn._connection.autocommit = True
    return conn


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

-- Open rounds for the step games (Chicken Road, Mines): games where the stake
-- is taken up front and the player cashes out later. These used to live in
-- process memory, so any restart -- a redeploy, or the free tier simply going
-- to sleep -- silently swallowed every in-flight stake: the money was debited
-- and the round it paid for no longer existed. One row per player per game.
CREATE TABLE IF NOT EXISTS open_rounds (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stake DOUBLE PRECISION NOT NULL,
    -- Game-specific secret + progress (mine positions, bust lane, opened tiles).
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_rounds_user_game
    ON open_rounds(user_id, game);

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
-- One row per APK fetch, so the dashboard can say how many people actually
-- took the app rather than just how many opened the page. Logged server side
-- in the download route: the file is fetched by a plain navigation, so no
-- script runs on the client to report it.
CREATE TABLE IF NOT EXISTS app_download_hits (
    id BIGSERIAL PRIMARY KEY,
    -- Both come from the download page when it is the referrer. A direct hit
    -- on the link -- shared into a chat, say -- has neither, and still counts.
    visitor_id TEXT,
    session_id TEXT,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_app_hits_time ON app_download_hits (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_hits_visitor ON app_download_hits (visitor_id);

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

-- The signup-bonus run (see backend/luck.py). Every normal account plays its
-- single-player rounds at the platform win rate until `luck_progress` -- the
-- signup bonus plus the net profit of those rounds -- first reaches its own
-- `luck_target`, drawn once from the configured band. NULL means "not drawn
-- yet"; registration fills both in, and any account older than this feature
-- gets them on its next round. `luck_done` is one-way on purpose: withdrawing
-- back down must not re-open a run that has already paid out.
ALTER TABLE users ADD COLUMN IF NOT EXISTS luck_target DOUBLE PRECISION;
ALTER TABLE users ADD COLUMN IF NOT EXISTS luck_progress DOUBLE PRECISION;
-- INTEGER, not BOOLEAN, to match game_access_enabled and its `= 1` reads.
ALTER TABLE users ADD COLUMN IF NOT EXISTS luck_done INTEGER DEFAULT 0;

-- Staff accounts, which sign in at /employee instead of the player app.
-- Kept separate from team_win_rate on purpose: that column says how often an
-- account wins, which is a knob an operator may well want at zero for a
-- recruiter who never plays. Conflating the two meant turning someone's win
-- rate off also took away their portal.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_employee INTEGER DEFAULT 0;
-- Everyone who already had a win rate was created through the team form, so
-- they are staff; this is the one-off backfill for accounts made before the
-- flag existed. Narrowed to rows still at 0 so it cannot undo a later change.
UPDATE users SET is_employee = 1 WHERE team_win_rate > 0 AND COALESCE(is_employee, 0) = 0;

-- Staff photo, in the database for the same reason as the QR images and the
-- cover art: the host wipes its filesystem on every deploy, so a file written
-- to disk survives until the next push and no longer.
ALTER TABLE users ADD COLUMN IF NOT EXISTS photo BYTEA;
ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_type TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_updated_at TIMESTAMPTZ;

-- Groups staff are organised into -- a shift, a city, a campaign. Its own
-- table rather than a text column on users so renaming a group renames it
-- everywhere at once, and so a group can exist before anyone is in it.
CREATE TABLE IF NOT EXISTS employee_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    -- Free text shown under the name in both consoles: what this group is for.
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_employee_groups_name ON employee_groups (LOWER(name));

-- SET NULL, never CASCADE: deleting a group must not delete the people in it.
-- They fall back to ungrouped, which is where they started.
ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id TEXT
    REFERENCES employee_groups(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_users_group ON users(group_id) WHERE group_id IS NOT NULL;
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

-- Admin-uploaded lobby artwork. The bundled SVG in assets/covers stays the
-- default; a row here overrides it for that game. Stored in the database
-- rather than on disk because the API runs on a host with an ephemeral
-- filesystem -- a file written at runtime is gone on the next deploy.
CREATE TABLE IF NOT EXISTS game_covers (
    game TEXT PRIMARY KEY,
    filename TEXT,
    content_type TEXT,
    data BYTEA NOT NULL,
    size_bytes INTEGER,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

-- What the lobby actually serves: the upload rescaled to tile size. The
-- originals are 4K and a couple of megabytes each, and eighteen of those is
-- tens of megabytes on a phone -- long enough that the bundled artwork sat on
-- screen first. `data` above keeps the original untouched, so this is only
-- ever a derived copy and can be rebuilt.
ALTER TABLE game_covers ADD COLUMN IF NOT EXISTS thumb BYTEA;
ALTER TABLE game_covers ADD COLUMN IF NOT EXISTS thumb_type TEXT;

-- Shared-round games that are not WinGo or Dice (Fish vs Tiger, Vortex).
-- One pair of tables keyed by `game` rather than a pair per title: these games
-- differ only in what a selection means and what it pays, so giving each its
-- own tables would duplicate the round clock and the settlement claim -- the
-- two places a bug would cost real money.
CREATE TABLE IF NOT EXISTS round_games (
    game TEXT NOT NULL,
    period TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    -- Whatever the game needs to redraw the result (cards, wheel segment).
    outcome JSONB,
    settled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (game, period)
);

CREATE TABLE IF NOT EXISTS round_bets (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    period TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    selection TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    status TEXT DEFAULT 'pending',
    payout DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_round_bets_open
    ON round_bets(game, period) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_round_bets_user
    ON round_bets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_round_games_settled
    ON round_games(game, period DESC) WHERE status = 'settled';

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
    # Withdrawals stay locked until an account has recharged this much. The
    # message is what the player is shown when they try before then; both are
    # editable from the dashboard's Controls tab.
    "withdrawal_min_deposit": "500",
    "withdrawal_locked_message": settings_store.WITHDRAWAL_LOCKED_MESSAGE,
    # The signup-bonus run. Seeded here so it is on for a fresh install and
    # editable from the dashboard afterwards; luck.DEFAULTS documents them.
    **luck.DEFAULTS,
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
