# Club 8 maintenance map

This repository contains two deployable surfaces:

- Static frontend on Vercel: `https://club-8.vercel.app`
- FastAPI backend on PythonAnywhere: `https://ravenjin.pythonanywhere.com`

## Runtime files

| File | Responsibility |
| --- | --- |
| `index.html` | All user/admin page markup and modal shells |
| `style.css` | Shared responsive styling for every page |
| `js/app.js` | UI coordinator, navigation, API calls, forms, and rendering |
| `js/game-clock.js` | WinGo room durations, period generation, countdown clock |
| `js/state.js` | Browser state and local-storage persistence |
| `js/sound.js` | Web Audio effects |
| `js/config.js` | Backend base URL |
| `backend/` | The API service — see `backend/README.md` for its layout and env |
| `server.py` | Legacy single-file API, superseded by `backend/`. Still what PythonAnywhere runs |
| `game_engine.py` | Legacy copy of the round engine, paired with `server.py` |
| `database.py` | Legacy copy of the schema, paired with `server.py` |
| `auth.py` | Legacy copy of the password/JWT helpers, paired with `server.py` |
| `assets/slice-deposit-qr.png` | Slice payment QR artwork without account-holder text |

The four root-level Python files and `backend/` are two copies of the same API.
`backend/` is the maintained one: it is split into routers, has a test suite,
and validates admin input that the root copy accepts blindly. Change `backend/`
and treat the root files as frozen until the PythonAnywhere host is repointed
at `backend/main.py`.

## Important contracts

- Room IDs are `parity`, `sapre`, `bcone`, and `emerd`.
- Period format is `YYYYMMDD + room code + 8-digit epoch slot`.
- Room codes/durations live in `js/game-clock.js` and `game_engine.py`.
- Frontend routes `/login`, `/game`, and `/admin` are rewritten to the SPA by `vercel.json`.
- Production data files (`*.db`, `uploads/`) must never be included in a Vercel deployment.

## Historical audit (2026-07-30)

- No core App method or page was removed from the 2026-07-29 deployed snapshot.
- Mines, Chicken Road, and Aviator were added after that snapshot.
- `admin.js`, `gameEngine.js`, and `upiWallet.js` were never imported by any saved snapshot. Their live behavior already exists in `app.js`; the unused duplicates were removed.
- Old QR element IDs referenced by `app.js` never existed in the deployed markup. Those no-op DOM writes were removed.
- The PythonAnywhere WSGI process did not reliably keep the FastAPI background clock task alive. `game_engine.py` now advances lazily on status reads, while the frontend uses a deterministic clock as a display fallback.
- Direct Vercel routes previously returned 404; their rewrites now target the SPA root.

## Required production environment

`APP_ENV=production` makes the server refuse to start unless `JWT_SECRET_KEY`
and `ADMIN_API_KEY` are set, and it forces `ALLOW_DEMO_USER` off. Set all of
`JWT_SECRET_KEY`, `PASSWORD_SALT`, `ADMIN_API_KEY`, `FRONTEND_ORIGINS` and
`PUBLIC_API_URL` on the host before reloading.

There is no build-time admin key any more. `X-Admin-Key` is compared only
against `ADMIN_API_KEY`, so rotating that variable fully revokes old access.

## Game access

WinGo is open to every signed-in user and needs no deposit. One admin switch per
user unlocks all the premium arcade games together (Aviator, Chicken Road,
Mines); there is no per-game toggle. The switch can only be turned on once the
user has ≥ ₹300 of *approved* deposits.

Those premium games run entirely in the browser against a local balance ledger,
so the gate is enforced client-side in `isPremiumGamePage`/`canEnterPremiumGames`
and centrally in `switchSubPage`. `/api/game/bet` deliberately does **not**
check the switch — that endpoint serves WinGo. The frontend re-syncs
`/api/auth/me` on a poll and on window focus, so a grant applies without a
re-login.

## Passwords

Stored as PBKDF2-HMAC-SHA256 with a per-user random salt. Accounts predating
this still carry a legacy unsalted SHA-256 digest; those verify normally and are
rewritten to the new format on the next successful login.

## Payment and admin workflow

- The backend is the source of truth for deposit, withdrawal, and wallet balances.
- A deposit records the exact QR, amount, order ID, and 12-digit UTR. Admin approval credits the wallet once; duplicate processing is blocked.
- A withdrawal reserves funds immediately. Admin approval marks it paid; rejection refunds the reserved amount once.
- Admin payment controls can pause deposits or withdrawals and set the server-enforced minimum withdrawal.
- `GET /api/wallet/payment-qr` generates an exact-amount UPI QR on the server. Install `requirements.txt` on PythonAnywhere before reloading the web app.
- Manual UTR approval confirms an admin decision, not automatic bank settlement. Verify the payment in the receiving account before approving.

## Safe upgrade workflow

1. Change room timing only in `js/game-clock.js` and `backend/game_engine.py`.
2. Keep API route changes synchronized between `backend/routers/` and calls in `js/app.js`.
2b. Run `cd backend && python -m pytest` before deploying any API change.
3. Increment the query version on `js/app.js` in `index.html` after frontend logic changes.
4. Run a local static preview and verify:
   - home loads;
   - third lobby card opens WinGo;
   - timer decreases;
   - period increments by exactly one at a boundary;
   - first history period equals current period minus one;
   - `/login`, `/game`, and `/admin` render.
5. Deploy frontend with `vercel --prod`, then repeat the smoke test on the production alias.

## Generated/local-only files

The following are intentionally ignored or local-only:

- `.venv/`, `__pycache__/`
- `.env.local`
- `.vercel/`
- `*.db`
- `uploads/`
- preview logs and screenshots

Do not delete `color_prediction.db` or `uploads/` during routine cleanup; they may contain local user and QR data.
