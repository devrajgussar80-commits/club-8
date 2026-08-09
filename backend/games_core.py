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
import luck
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

def require_game_access(user: dict, conn=None, settings: dict = None) -> None:
    """Premium arcade gate: admin switch, signup-bonus run, or enough deposits.

    WinGo stays open to everyone; these games do not. Checked server-side so
    flipping a localStorage flag in the browser does not buy entry.

    An account still on its signup-bonus run is let in without a deposit --
    the bonus is what that run is played with, and a wallet holding ₹100 can
    never clear a ₹300 deposit threshold on its own. Once the run finishes the
    deposit gate is back, which is the point at which a player who wants to
    keep going has to fund the account.
    """
    if user.get("game_access_enabled"):
        return

    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    try:
        if luck.Run(user, settings or luck.load_settings(conn)).open:
            return
        approved = get_approved_deposit_total(conn, user["id"])
    finally:
        if should_close:
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

def play_round(user: dict, game: str, stake: float, resolve, redraw_win=None, redraw_loss=None):
    """Debit `stake`, run `resolve()`, credit the payout, log the round.

    `resolve(stake)` receives the *validated* stake -- never the raw request
    body -- and returns `(payout, outcome_dict)`. It is called after the
    balance check, so a rejected bet never consumes a draw.

    `redraw_win` and `redraw_loss`, if given, produce a genuine winning or
    losing `(payout, outcome)` for the game. They are what let the player's
    win rate (see luck.py) hold a round to a result the natural draw did not
    give, while keeping the reels, the pocket or the lane on screen honest
    about what the wallet just did.

    The user row is held with FOR UPDATE for the whole transaction: two spins
    firing at once queue up instead of both reading the same pre-debit balance
    and overdrawing the wallet. The account's run advances inside that same
    transaction, so a round can never move the money without booking it.
    """
    stake = validate_stake(stake)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        settings = luck.load_settings(conn)
        require_game_access(user, conn=conn, settings=settings)

        locked = cursor.execute(
            f"SELECT balance, {luck.COLUMNS} FROM users WHERE id = ? FOR UPDATE",
            (user["id"],),
        ).fetchone()
        if not locked or float(locked["balance"]) < stake:
            raise HTTPException(status_code=400, detail="Insufficient balance.")

        run = luck.Run(locked, settings)
        run.start()

        payout, outcome = resolve(stake)
        payout, outcome = luck.steer(
            run, stake, (round(max(0.0, float(payout)), 2), outcome), redraw_win, redraw_loss
        )
        payout = round(max(0.0, float(payout)), 2)
        run.advance(stake, payout)

        # One statement, so the row never sits debited-but-not-credited.
        new_balance = cursor.execute(
            f"UPDATE users SET balance = balance - ? + ?, {luck.SET_COLUMNS} "
            "WHERE id = ? RETURNING balance",
            (stake, payout, *run.row, user["id"]),
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


def player_run(user: dict) -> luck.Run:
    """This account's run, for the step games that settle outside play_round.

    They ask only on a bust -- once per round at most -- so this stays off the
    per-tap path that `load_round` already pays for.
    """
    conn = get_db_connection(readonly=True)
    try:
        return luck.Run(user, luck.load_settings(conn))
    finally:
        conn.close()


def hold_stake(user: dict, stake: float) -> dict:
    """Debit the stake to open a round. Rejects if the wallet cannot cover it."""
    stake = validate_stake(stake)

    conn = get_db_connection()
    require_game_access(user, conn=conn)
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


# --------------------------------------------------- open rounds (persisted)
#
# Chicken Road and Mines take the stake up front and pay out later, so an
# unfinished round has real money attached to it. Keeping those rounds in
# process memory meant a restart -- a redeploy, or the free tier going to
# sleep -- destroyed the round while the debit stayed, and the player simply
# lost the stake. They live in `open_rounds` instead.


def open_round(user_id: str, game: str, round_id: str, stake: float, state: dict) -> None:
    """Record a newly opened round. One per player per game."""
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO open_rounds (id, game, user_id, stake, state)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id, game) DO UPDATE SET
                id = EXCLUDED.id, stake = EXCLUDED.stake,
                state = EXCLUDED.state, created_at = NOW()
            """,
            (round_id, game, user_id, stake, json.dumps(state)),
        )
        conn.commit()
    finally:
        conn.close()


def load_round(user_id: str, game: str, round_id: str | None = None) -> dict | None:
    """The player's open round for this game, or None.

    When `round_id` is given it must match, so a stale tab cannot act on a
    round that has since been replaced.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, stake, state FROM open_rounds WHERE user_id = ? AND game = ?",
            (user_id, game),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if round_id is not None and row["id"] != round_id:
        return None
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return {"id": row["id"], "stake": float(row["stake"]), **state}


def save_round(user_id: str, game: str, state: dict) -> None:
    """Persist progress inside an open round (a safe jump, an opened tile)."""
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE open_rounds SET state = ? WHERE user_id = ? AND game = ?",
            (json.dumps(state), user_id, game),
        )
        conn.commit()
    finally:
        conn.close()


def close_round(user_id: str, game: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "DELETE FROM open_rounds WHERE user_id = ? AND game = ?", (user_id, game)
        )
        conn.commit()
    finally:
        conn.close()


def settle_held(user_id: str, game: str, stake: float, payout: float, outcome: dict) -> dict:
    """Credit the payout (0 on a bust) and record the finished round."""
    payout = round(max(0.0, float(payout)), 2)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Step games are single-player, so what they win and lose counts
        # towards the account's run exactly as a one-shot round does. Read
        # under the same lock that is about to move the balance.
        run = None
        if luck.counts(game):
            locked = cursor.execute(
                f"SELECT {luck.COLUMNS} FROM users WHERE id = ? FOR UPDATE", (user_id,)
            ).fetchone()
            run = luck.Run(locked, luck.load_settings(conn))
            run.advance(stake, payout)

        # The stake was already taken by hold_stake, so only the payout moves.
        new_balance = cursor.execute(
            "UPDATE users SET balance = balance + ?"
            + (f", {luck.SET_COLUMNS}" if run else "")
            + " WHERE id = ? RETURNING balance",
            (payout, *(run.row if run else ()), user_id),
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
