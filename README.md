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
| `server.py` | FastAPI routes, CORS, auth dependencies, upload serving |
| `game_engine.py` | Server round resolution and payout processing |
| `database.py` | SQLite schema and seed data |
| `auth.py` | Password/JWT helpers |
| `assets/slice-deposit-qr.png` | Slice payment QR artwork without account-holder text |

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

## Payment and admin workflow

- The backend is the source of truth for deposit, withdrawal, and wallet balances.
- A deposit records the exact QR, amount, order ID, and 12-digit UTR. Admin approval credits the wallet once; duplicate processing is blocked.
- A withdrawal reserves funds immediately. Admin approval marks it paid; rejection refunds the reserved amount once.
- Admin payment controls can pause deposits or withdrawals and set the server-enforced minimum withdrawal.
- `GET /api/wallet/payment-qr` generates an exact-amount UPI QR on the server. Install `requirements.txt` on PythonAnywhere before reloading the web app.
- Manual UTR approval confirms an admin decision, not automatic bank settlement. Verify the payment in the receiving account before approving.

## Safe upgrade workflow

1. Change room timing only in `js/game-clock.js` and `game_engine.py`.
2. Keep API route changes synchronized between `server.py` and calls in `js/app.js`.
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
