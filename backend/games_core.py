"""Shared money and RNG plumbing for the arcade games.

Every game gets its own router file holding only that game's rules. The parts
that must never diverge between games -- who is allowed to play, how a stake
is debited, how a payout is credited, and the fact that both happen inside one
locked transaction -- live here and here only.

The outcome is always drawn on the server with `secrets`. The browser never
sends a result, it only sends a stake; it receives the outcome and animates it.
"""

import json
import secrets
import uuid

from fastapi import HTTPException

import config
from database import get_db_connection
from settings_store import get_approved_deposit_total

# A round can never stake more than this, whatever the client posts.
MAX_STAKE = 100_000.0
MIN_STAKE = 1.0


# ---------------------------------------------------------------- randomness

def secure_unit() -> float:
    """Uniform float in [0, 1) from the OS CSPRNG."""
    return secrets.randbits(53) / float(1 << 53)


def secure_below(chance: float) -> bool:
    return secure_unit() < chance


def weighted_pick(weights: dict):
    """Pick a key from {key: weight}. Weights need not sum to anything."""
    total = sum(weights.values())
    roll = secure_unit() * total
    for key, weight in weights.items():
        roll -= weight
        if roll <= 0:
            return key
    return next(iter(weights))


# ------------------------------------------------------------------ eligibility

def require_game_access(user: dict) -> None:
    """Premium arcade gate: admin switch, or enough approved deposits.

    WinGo stays open to everyone; these games do not. Checked server-side so
    flipping a localStorage flag in the browser does not buy entry.
    """
    if user.get("game_access_enabled"):
        return

    conn = get_db_connection()
    try:
        approved = get_approved_deposit_total(conn, user["id"])
    finally:
        conn.close()

    if approved < config.GAME_ACCESS_MIN_DEPOSIT:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Game access locked. Deposit at least "
                f"₹{config.GAME_ACCESS_MIN_DEPOSIT:.0f} to unlock the arcade."
            ),
        )


def validate_stake(amount) -> float:
    try:
        stake = round(float(amount), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid stake.")
    if stake < MIN_STAKE or stake > MAX_STAKE:
        raise HTTPException(
            status_code=400, detail=f"Stake must be between ₹{MIN_STAKE:.0f} and ₹{MAX_STAKE:.0f}."
        )
    return stake


# ------------------------------------------------------------------- settlement

def play_round(user: dict, game: str, stake: float, resolve):
    """Debit `stake`, run `resolve()`, credit the payout, log the round.

    `resolve(stake)` receives the *validated* stake -- never the raw request
    body -- and returns `(payout, outcome_dict)`. It is called after the
    balance check, so a rejected bet never consumes a draw.

    The user row is held with FOR UPDATE for the whole transaction: two spins
    firing at once queue up instead of both reading the same pre-debit balance
    and overdrawing the wallet.
    """
    require_game_access(user)
    stake = validate_stake(stake)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        locked = cursor.execute(
            "SELECT balance FROM users WHERE id = ? FOR UPDATE", (user["id"],)
        ).fetchone()
        if not locked or float(locked["balance"]) < stake:
            raise HTTPException(status_code=400, detail="Insufficient balance.")

        payout, outcome = resolve(stake)
        payout = round(max(0.0, float(payout)), 2)

        # One statement, so the row never sits debited-but-not-credited.
        new_balance = cursor.execute(
            "UPDATE users SET balance = balance - ? + ? WHERE id = ? RETURNING balance",
            (stake, payout, user["id"]),
        ).fetchone()["balance"]

        round_id = f"{game.upper()[:3]}-{uuid.uuid4().hex[:10].upper()}"
        cursor.execute(
            """
            INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (round_id, game, user["id"], stake, payout, json.dumps(outcome)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "success",
        "round_id": round_id,
        "stake": stake,
        "payout": payout,
        "profit": round(payout - stake, 2),
        "balance": round(float(new_balance), 2),
        **outcome,
    }


# ----------------------------------------------------- step games (chicken, mines)
#
# Crash-style games where the stake is taken up front and the player cashes out
# later (or busts). They cannot use play_round, which settles in one shot, so
# they debit with hold_stake and later credit with settle_held. Both take the
# user row's FOR UPDATE lock, so the wallet can never be overdrawn or paid twice
# by two requests racing -- which is exactly the bug these replaced, where the
# game trusted a client-side balance and let bets ride on money that was not
# there.


def hold_stake(user: dict, stake: float) -> dict:
    """Debit the stake to open a round. Rejects if the wallet cannot cover it."""
    require_game_access(user)
    stake = validate_stake(stake)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        locked = cursor.execute(
            "SELECT balance FROM users WHERE id = ? FOR UPDATE", (user["id"],)
        ).fetchone()
        if not locked or float(locked["balance"]) < stake:
            raise HTTPException(status_code=400, detail="Insufficient balance.")
        new_balance = cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? RETURNING balance",
            (stake, user["id"]),
        ).fetchone()["balance"]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"stake": stake, "balance": round(float(new_balance), 2)}


def settle_held(user_id: str, game: str, stake: float, payout: float, outcome: dict) -> dict:
    """Credit the payout (0 on a bust) and record the finished round."""
    payout = round(max(0.0, float(payout)), 2)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # The stake was already taken by hold_stake, so only the payout moves.
        new_balance = cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ? RETURNING balance",
            (payout, user_id),
        ).fetchone()["balance"]
        cursor.execute(
            """
            INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{game.upper()[:3]}-{uuid.uuid4().hex[:10].upper()}",
                game,
                user_id,
                stake,
                payout,
                json.dumps(outcome),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"payout": payout, "balance": round(float(new_balance), 2)}
