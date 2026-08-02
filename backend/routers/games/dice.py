"""Dice Roll, multiplayer: one shared 30-second round, like WinGo.

Every player bets on the same upcoming roll during the window; when it closes
the server rolls one face and settles every bet against it. Structurally this
is WinGo with six faces instead of ten numbers.

Money model, same as the other server games: the stake is debited when the bet
is placed (games_core.hold_stake, under a row lock), and winners are credited
at settlement. A restart cannot lose a debited stake or pay one twice because
bets and rounds live in Postgres, and a round is claimed for settlement with a
single `UPDATE ... WHERE status='open' RETURNING`.

Three ways to bet, each paying 98% back (the house edge), the same convention
as the single-player games and WinGo:

    number   one face            fair 6.00x -> 5.88x
    parity   odd / even          fair 2.00x -> 1.96x
    half     low 1-3 / high 4-6  fair 2.00x -> 1.96x

Admin steering reuses the per-game controls (Controls tab): manual + a face
1-6 forces the roll; in auto mode `house_bias`% of rounds are rolled to the
face that pays players the least, and bias 0 is a fair random roll.
"""

import math
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from game_controls import check_playable, get_controls
from games_core import hold_stake, secure_unit

router = APIRouter(prefix="/api/games/dice", tags=["games"])

GAME = "dice"
FACES = [1, 2, 3, 4, 5, 6]
NUMBER_PAYS = 5.88
EVEN_MONEY_PAYS = 1.96

DURATION = 30      # seconds per round
FREEZE = 3         # betting shuts this many seconds before the roll
PERIOD_CODE = "D30"

# One tick at a time: every state/bet read advances the clock, and without this
# two concurrent requests could both try to settle the same closed round.
_tick_lock = threading.Lock()


def _slot(now: float) -> int:
    return int(now // DURATION)


def _period(slot: int) -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    return f"{date_part}{PERIOD_CODE}{slot % 100000000:08d}"


def _covers(bet_type: str, value: str) -> set:
    if bet_type == "number":
        return {int(value)} if str(value).isdigit() and int(value) in FACES else set()
    if bet_type == "parity":
        return {1, 3, 5} if value == "odd" else {2, 4, 6} if value == "even" else set()
    if bet_type == "half":
        return {1, 2, 3} if value == "low" else {4, 5, 6} if value == "high" else set()
    return set()


def payout_multiple(bet_type: str) -> float:
    return NUMBER_PAYS if bet_type == "number" else EVEN_MONEY_PAYS


def _decide_face(bets, controls) -> int:
    """The rolled face. Manual forces it; auto is fair unless house_bias steers
    a share of rounds to the least-paying face."""
    forced = controls.get("forced") or ""
    if controls.get("mode") == "manual" and forced.isdigit() and int(forced) in FACES:
        return int(forced)

    fair = FACES[int(secure_unit() * len(FACES))]
    bias = controls.get("house_bias", 0)
    if bets and bias > 0 and secure_unit() * 100 < bias:
        liabilities = {
            face: round(
                sum(
                    float(b["amount"]) * payout_multiple(b["bet_type"])
                    for b in bets
                    if face in _covers(b["bet_type"], b["selection"])
                ),
                2,
            )
            for face in FACES
        }
        lowest = min(liabilities.values())
        candidates = [f for f, total in liabilities.items() if total == lowest]
        return candidates[int(secure_unit() * len(candidates))]
    return fair


def _settle_round(conn, period: str) -> None:
    """Roll and pay out one closed round. Idempotent: the claim below runs once."""
    claimed = conn.execute(
        "UPDATE dice_rounds SET status = 'settled', settled_at = NOW() "
        "WHERE period = ? AND status = 'open' RETURNING period",
        (period,),
    ).fetchone()
    if not claimed:
        return  # already settled, or another request claimed it first

    bets = conn.execute(
        "SELECT * FROM dice_bets WHERE period = ? AND status = 'pending'", (period,)
    ).fetchall()
    controls = get_controls(conn, GAME)
    face = _decide_face(bets, controls)

    for bet in bets:
        won = face in _covers(bet["bet_type"], bet["selection"])
        payout = round(float(bet["amount"]) * payout_multiple(bet["bet_type"]), 2) if won else 0.0
        conn.execute(
            "UPDATE dice_bets SET status = ?, payout = ? WHERE id = ?",
            ("won" if won else "lost", payout, bet["id"]),
        )
        if payout > 0:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (payout, bet["user_id"]),
            )
        # Feed the shared analytics ledger the same way every other game does.
        conn.execute(
            """
            INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"DIC-{uuid.uuid4().hex[:10].upper()}",
                GAME,
                bet["user_id"],
                float(bet["amount"]),
                payout,
                f'{{"face": {face}, "bet": "{bet["bet_type"]}:{bet["selection"]}", "won": {str(won).lower()}}}',
            ),
        )

    conn.execute("UPDATE dice_rounds SET face = ? WHERE period = ?", (face, period))


def _tick(conn) -> str:
    """Advance the clock: settle any closed rounds, return the current period."""
    with _tick_lock:
        now = time.time()
        current = _period(_slot(now))
        previous = _period(_slot(now) - 1)
        # Keep history continuous: guarantee a row for the round that just
        # closed even if nobody bet in it, so it still gets a rolled face.
        conn.execute(
            "INSERT INTO dice_rounds (period, status) VALUES (?, 'open') "
            "ON CONFLICT (period) DO NOTHING",
            (previous,),
        )
        due = conn.execute(
            "SELECT period FROM dice_rounds WHERE status = 'open' AND period <> ?",
            (current,),
        ).fetchall()
        for row in due:
            _settle_round(conn, row["period"])
        conn.commit()
        return current


def _round_view(now: float) -> dict:
    remaining = max(0, math.ceil((_slot(now) + 1) * DURATION - now))
    return {
        "period": _period(_slot(now)),
        "seconds_left": remaining,
        "betting_open": remaining > FREEZE,
    }


class BetRequest(BaseModel):
    bet_type: str
    selection: str
    amount: float


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        current = _tick(conn)
        now = time.time()
        view = _round_view(now)

        my_bets = conn.execute(
            "SELECT bet_type, selection, amount FROM dice_bets "
            "WHERE period = ? AND user_id = ? ORDER BY created_at",
            (current, current_user["id"]),
        ).fetchall()

        history = conn.execute(
            "SELECT period, face FROM dice_rounds "
            "WHERE status = 'settled' AND face IS NOT NULL ORDER BY period DESC LIMIT 15",
        ).fetchall()

        # The just-settled round, so the client can show the reveal.
        last = history[0] if history else None
    finally:
        conn.close()

    return {
        **view,
        "faces": FACES,
        "pays": {"number": NUMBER_PAYS, "parity": EVEN_MONEY_PAYS, "half": EVEN_MONEY_PAYS},
        "balance": round(float(current_user["balance"]), 2),
        "my_bets": [dict(b) for b in my_bets],
        "last_result": (
            {
                "period": last["period"],
                "face": last["face"],
                "parity": "odd" if last["face"] % 2 else "even",
                "half": "low" if last["face"] <= 3 else "high",
            }
            if last
            else None
        ),
        "history": [
            {"period": h["period"], "face": h["face"]} for h in history
        ],
    }


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    if not _covers(req.bet_type, str(req.selection)):
        raise HTTPException(status_code=400, detail=f"Invalid bet: {req.bet_type} {req.selection}")

    conn = get_db_connection()
    try:
        current = _tick(conn)
        now = time.time()
        if _round_view(now)["seconds_left"] <= FREEZE:
            raise HTTPException(
                status_code=400, detail="Betting is closed for this round. Wait for the next one."
            )
        # Enforce enabled + per-game stake limits before any money moves.
        check_playable(conn, GAME, round(float(req.amount or 0), 2))
        # The round row must exist before its bets reference it.
        conn.execute(
            "INSERT INTO dice_rounds (period, status) VALUES (?, 'open') "
            "ON CONFLICT (period) DO NOTHING",
            (current,),
        )
        conn.commit()
    finally:
        conn.close()

    # Debit the stake under a row lock; rejects an overdraw with 400.
    held = hold_stake(current_user, req.amount)

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO dice_bets
                (id, period, user_id, user_name, bet_type, selection, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"DB-{uuid.uuid4().hex[:12].upper()}",
                current,
                current_user["id"],
                current_user.get("username"),
                req.bet_type,
                str(req.selection),
                held["stake"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "period": current,
        "bet_type": req.bet_type,
        "selection": str(req.selection),
        "amount": held["stake"],
        "balance": held["balance"],
    }


@router.get("/my-bets")
def my_bets(current_user: dict = Depends(get_current_user)):
    """Recent settled bets for this player, so the UI can show win/loss."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT b.period, b.bet_type, b.selection, b.amount, b.status, b.payout, r.face
            FROM dice_bets b
            LEFT JOIN dice_rounds r ON r.period = b.period
            WHERE b.user_id = ?
            ORDER BY b.created_at DESC LIMIT 20
            """,
            (current_user["id"],),
        ).fetchall()
    finally:
        conn.close()
    return {"bets": [dict(r) for r in rows]}
