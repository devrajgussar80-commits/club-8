# Club 8 API

Self-contained FastAPI service for the Club 8 platform. The static frontend
(`index.html`, `js/`, `style.css` in the repo root) is deployed separately on
Vercel and talks to this API over the base URL in `js/config.js`.

This directory supersedes the root-level `server.py` / `database.py` /
`game_engine.py` / `auth.py`, which are the older single-file deployment.

## Layout

| File | Responsibility |
| --- | --- |
| `main.py` | App factory: CORS, cache headers, router mounts, round-clock lifespan |
| `config.py` | Every environment variable and shared constant, read once |
| `schemas.py` | Pydantic request bodies; constraint-only validation |
| `deps.py` | `get_current_user` and `require_admin` dependencies |
| `settings_store.py` | Reads/writes for the `system_settings` table and deposit totals |
| `database.py` | Postgres pool, `?`->`%s` shim, schema, indexes and seed data |
| `auth.py` | PBKDF2 password hashing and JWT issue/verify |
| `game_engine.py` | Round clock, result selection and payout resolution |
| `routers/pages.py` | `/api/health` and the SPA fallbacks |
| `routers/auth.py` | Register, login, profile |
| `routers/game.py` | WinGo status, bets, history |
| `routers/wallet.py` | Deposit orders, UTR submission, withdrawals, UPI QR |
| `routers/admin.py` | Dashboard, moderation queues, settings, QR management |
| `tests/` | pytest suite; each test gets its own Postgres schema |

Routers import top-level modules (`import config`, `from deps import ...`)
rather than using relative imports, because the app runs with this directory on
`sys.path` (`uvicorn --app-dir backend`), not as an installed package.

## Run locally

```bash
python -m pip install -r backend/requirements-dev.txt
```

```bash
python -m uvicorn main:app --app-dir backend --port 8080 --reload
```

The API is then on `http://localhost:8080`, and because `SERVE_FRONTEND`
defaults to `true` it also serves the repo-root frontend at `/`. Set
`SERVE_FRONTEND=false` when the frontend is deployed separately.

## Tests

```bash
cd backend && python -m pytest
```

Every test runs in its own throwaway Postgres schema, created before the test
and dropped after, so tests never touch real tables. Set `TEST_DATABASE_URL`
to a Neon **branch** (Branches -> New in the Neon dashboard); without it the
suite skips rather than risk running against production. `tests/conftest.py` sets the
environment *before* importing the app, because `config`, `auth` and `database`
all read `os.environ` at module scope.

A handful of game tests skip if they land inside the five-second freeze window
at the end of a round — the clock is real time, not mocked.

## Environment

Copy `.env.example` and fill it in. In production
(`APP_ENV=production`) the process refuses to start without `JWT_SECRET_KEY`,
`PASSWORD_SALT` or `ADMIN_API_KEY`, and `ALLOW_DEMO_USER` is forced off.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | *(required)* | Neon **pooled** connection string |
| `TEST_DATABASE_URL` | empty | Neon branch for pytest. Use the **direct** string (host *without* `-pooler`) — the pooler drops the per-test `search_path` |
| `DB_SCHEMA` | `public` | Postgres schema; tests override it per test |
| `APP_ENV` | `development` | `production` enables the required-secret checks |
| `JWT_SECRET_KEY` | dev placeholder | Required in production |
| `PASSWORD_SALT` | dev placeholder | Only used by the legacy SHA-256 verify path |
| `ADMIN_API_KEY` | empty | Required in production; admin routes 503 without it |
| `FRONTEND_ORIGINS` | localhost:3000/8080 | Comma-separated CORS allowlist |
| `PUBLIC_API_URL` | empty | Origin used to build uploaded QR links |
| `SERVE_FRONTEND` | `true` | Serve the repo-root static files from this process |
| `ALLOW_DEMO_USER` | `false` | Hands a real account to anonymous callers; dev only |
| `UPLOAD_DIR` | `backend/uploads` | |

## Auth

Two independent paths reach the admin routes:

- **Session token** — `POST /api/admin/login` with an account whose `is_admin`
  flag is set, then `Authorization: Bearer <token>`. This is what the dashboard
  uses.
- **Shared key** — `X-Admin-Key`, compared against `ADMIN_API_KEY`, or against
  the hash in `system_settings.admin_api_key_hash` once
  `POST /api/admin/rotate-access-key` has been called. Rotation revokes the
  environment key immediately, so keep the new value somewhere recoverable.

Player passwords are PBKDF2-HMAC-SHA256 with a per-user random salt. Accounts
predating that scheme carry an unsalted SHA-256 digest; those still verify and
are rewritten to PBKDF2 on the next successful login.

## Money invariants

These are what the tests in `tests/test_admin.py` and `tests/test_wallet.py`
exist to protect:

- A deposit credits the wallet exactly once, on admin approval. Re-approving a
  processed deposit is a 400, not a second credit.
- A withdrawal reserves the balance at submission time. Approval marks it paid
  and keeps the funds out; rejection refunds exactly once.
- A UTR can only ever be submitted once, across all users.
- A deposit is validated against the QR the player was actually shown, so
  rotation or an admin disabling that QR mid-payment cannot void real money.
- Game access can only be enabled once a user has ≥ ₹300 of *approved*
  deposits.

Manual UTR approval records an admin decision, not bank settlement. Verify the
payment in the receiving account before approving.

## Game rooms

Room IDs are `parity` (30s), `sapre` (1m), `bcone` (3m) and `emerd` (5m).
Durations live in `game_engine.ROOM_CONFIG` and must stay in sync with
`js/game-clock.js`. Period format is `YYYYMMDD` + room code + an 8-digit epoch
slot.

The round clock runs as a lifespan task, but `get_status` also advances it
lazily on every read because some WSGI hosts never let the task run. Both paths
take the same lock, and `rounds.period` is a primary key, so a round cannot be
resolved — or paid out — twice.

## Deploy

The Render Blueprint is `render.yaml` at the **repo root** — Render reads it
only from there. It builds from `backend/requirements.txt` and starts
`uvicorn main:app --app-dir backend`. Set `DATABASE_URL`, `FRONTEND_ORIGINS`
and `PUBLIC_API_URL` in the dashboard; the rest of the secrets are generated.

Data lives in Neon, so there is no mounted disk and a redeploy cannot wipe the
database. One thing still writes to local disk: admin-uploaded QR images under
`UPLOAD_DIR`. Those are lost on redeploy. Either add a Render disk for them, or
use the "add QR by https:// URL" path in the admin panel and host the image
elsewhere.
