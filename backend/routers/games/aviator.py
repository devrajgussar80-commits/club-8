"""Aviator: one shared crash round for every player, provably fair.

This is what separates a real crash game from the toy version: the round is
not private to a browser tab. Every player sees the same plane, the same
multiplier and the same crash, because the schedule and the crash point are
both computed here from a seed chain.

Provably fair, the standard commit/reveal:

    server_seed(n) = HMAC_SHA256(CHAIN_KEY, "aviator:{n}")
    crash(n)       = from the first 52 bits of that seed
    published      = sha256(server_seed(n))  -- during betting, before any bet
    revealed       = server_seed(n)          -- after the plane flies away

A player records the hash shown before betting, then checks it against the
seed revealed afterwards. The server cannot move the crash point once bets
are in, because the hash is already out.

House edge, and where it comes from: 1 round in 33 crashes instantly at
1.00x; every other round is a fair 1/u. So for any cash-out target T,

    P(crash >= T) = (32/33) / T   ->  RTP 96.97%, whatever target is chosen

which is the point of a crash game -- no target is better than any other.
"""

import hashlib
import hmac
import json
import math
import os
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from games_core import require_game_access, validate_stake

router = APIRouter(prefix="/api/games/aviator", tags=["games"])

GAME = "aviator"

# Round shape, in seconds.
BETTING_SECONDS = 6.0
CRASHED_SECONDS = 3.5
# Multiplier curve: m(t) = e^(GROWTH * t). 2x at ~2.5s, 10x at ~8.2s.
GROWTH = 0.28
MAX_MULTIPLIER = 1000.0
MAX_PANELS = 2

# The chain key must outlive a restart, otherwise every deploy replays the
# same rounds from round 0. Generated once and kept in the environment.
CHAIN_KEY = os.environ.get("AVIATOR_CHAIN_KEY", "").encode() or os.urandom(32)

_lock = threading.RLock()


# --------------------------------------------------------------- fairness

def server_seed(round_no: int) -> str:
    return hmac.new(CHAIN_KEY, f"aviator:{round_no}".encode(), hashlib.sha256).hexdigest()


def seed_hash(round_no: int) -> str:
    return hashlib.sha256(server_seed(round_no).encode()).hexdigest()


def crash_point(round_no: int) -> float:
    """Crash multiplier for a round, derived only from its seed."""
    seed = server_seed(round_no)
    # 52 bits keeps the value exactly representable as a float.
    roll = int(seed[:13], 16)
    denominator = float(1 << 52)
    if roll % 33 == 0:
        # The instant bust. This is the entire house edge -- the branch below
        # is a fair 1/u, so do not also scale it by 0.97 or the edge doubles.
        return 1.0
    unit = (roll + 1) / (denominator + 1)
    return min(MAX_MULTIPLIER, max(1.0, math.floor((1.0 / unit) * 100) / 100))


def flight_seconds(crash: float) -> float:
    """How long the plane stays up before reaching `crash`."""
    if crash <= 1.0:
        return 0.0
    return math.log(crash) / GROWTH


def multiplier_at(elapsed: float, crash: float) -> float:
    return min(crash, math.floor(math.exp(GROWTH * max(0.0, elapsed)) * 100) / 100)


# ------------------------------------------------------------ round clock

class _Round:
    """One shared round. `bets` is keyed by (user_id, panel)."""

    def __init__(self, number: int, betting_started: float):
        self.number = number
        self.betting_started = betting_started
        self.crash = crash_point(number)
        self.takeoff = betting_started + BETTING_SECONDS
        self.crashed_at = self.takeoff + flight_seconds(self.crash)
        self.ends_at = self.crashed_at + CRASHED_SECONDS
        self.bets = {}
        self.settled = False

    def phase(self, now: float) -> str:
        if now < self.takeoff:
            return "betting"
        if now < self.crashed_at:
            return "flying"
        return "crashed"

    def multiplier(self, now: float) -> float:
        if now < self.takeoff:
            return 1.0
        return multiplier_at(now - self.takeoff, self.crash)


_current = None
_history = []


def _advance(now: float) -> "_Round":
    """Return the live round, settling and rolling over any that have ended."""
    global _current
    if _current is None:
        _current = _Round(1, now)
        return _current

    while now >= _current.ends_at:
        _settle(_current)
        _current = _Round(_current.number + 1, _current.ends_at)
    if now >= _current.crashed_at:
        _settle(_current)
    return _current


def current_round() -> "_Round":
    with _lock:
        return _advance(time.time())


def _settle(rnd: "_Round") -> None:
    """Pay auto-cashouts that were reached, bust everything else. Idempotent."""
    if rnd.settled:
        return
    rnd.settled = True

    payouts = []
    for (user_id, _panel), bet in rnd.bets.items():
        if bet["cashed_at"]:
            continue  # already paid on the manual cash-out path
        target = bet["auto_cashout"]
        if target and target <= rnd.crash:
            bet["cashed_at"] = target
            bet["payout"] = round(bet["amount"] * target, 2)
            payouts.append((user_id, bet["payout"]))
        else:
            bet["payout"] = 0.0

    _record_round(rnd, payouts)

    _history.insert(0, {"round": rnd.number, "crash": rnd.crash, "seed": server_seed(rnd.number)})
    del _history[25:]


def _record_round(rnd: "_Round", payouts) -> None:
    """Credit auto-cashout winners and write every bet to the analytics ledger."""
    if not rnd.bets:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for user_id, amount in payouts:
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id)
            )
        for (user_id, panel), bet in rnd.bets.items():
            cursor.execute(
                """
                INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"AVI-{uuid.uuid4().hex[:10].upper()}",
                    GAME,
                    user_id,
                    bet["amount"],
                    bet.get("payout", 0.0),
                    json.dumps(
                        {
                            "round": rnd.number,
                            "panel": panel,
                            "crash": rnd.crash,
                            "cashed_at": bet["cashed_at"],
                            "auto": bool(bet["auto_cashout"]),
                        }
                    ),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------ routes

class BetRequest(BaseModel):
    amount: float
    panel: int = 0
    auto_cashout: float | None = None


class CashoutRequest(BaseModel):
    panel: int = 0


def _public_bets(rnd: "_Round", user_id: str):
    """The live bet feed. Other players are masked the way the market does it."""
    rows = []
    for (owner, panel), bet in rnd.bets.items():
        rows.append(
            {
                "player": bet["name"] if owner == user_id else _mask(bet["name"]),
                "mine": owner == user_id,
                "panel": panel,
                "amount": bet["amount"],
                "cashed_at": bet["cashed_at"],
                "payout": bet.get("payout", 0.0),
            }
        )
    rows.sort(key=lambda row: (-row["amount"]))
    return rows


def _mask(name: str) -> str:
    name = name or "Player"
    return f"{name[0]}***{name[-1]}" if len(name) > 1 else "P***r"


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    now = time.time()
    with _lock:
        rnd = _advance(now)
        phase = rnd.phase(now)
        mine = {
            panel: bet
            for (user_id, panel), bet in rnd.bets.items()
            if user_id == current_user["id"]
        }
        payload = {
            "round": rnd.number,
            "phase": phase,
            "multiplier": rnd.multiplier(now),
            # Lets the client run its own smooth clock between polls.
            "server_time": now,
            "takeoff_at": rnd.takeoff,
            "seconds_left": max(0.0, round(rnd.takeoff - now, 2)) if phase == "betting" else 0.0,
            "seed_hash": seed_hash(rnd.number),
            "crash": rnd.crash if phase == "crashed" else None,
            "seed": server_seed(rnd.number) if phase == "crashed" else None,
            "history": [
                {"round": item["round"], "crash": item["crash"], "seed": item["seed"]}
                for item in _history
            ],
            "bets": _public_bets(rnd, current_user["id"]),
            "my_bets": {
                str(panel): {
                    "amount": bet["amount"],
                    "auto_cashout": bet["auto_cashout"],
                    "cashed_at": bet["cashed_at"],
                    "payout": bet.get("payout", 0.0),
                }
                for panel, bet in mine.items()
            },
        }
    return payload


@router.post("/bet")
def place_bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    require_game_access(current_user)
    stake = validate_stake(req.amount)
    if req.panel not in range(MAX_PANELS):
        raise HTTPException(status_code=400, detail="Invalid bet panel.")
    if req.auto_cashout is not None and not (1.01 <= req.auto_cashout <= MAX_MULTIPLIER):
        raise HTTPException(status_code=400, detail="Auto cash-out must be at least 1.01x.")

    now = time.time()
    with _lock:
        rnd = _advance(now)
        if rnd.phase(now) != "betting":
            raise HTTPException(status_code=400, detail="Betting is closed for this round.")
        key = (current_user["id"], req.panel)
        if key in rnd.bets:
            raise HTTPException(status_code=400, detail="This panel already has a bet.")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            locked = cursor.execute(
                "SELECT balance FROM users WHERE id = ? FOR UPDATE", (current_user["id"],)
            ).fetchone()
            if not locked or float(locked["balance"]) < stake:
                raise HTTPException(status_code=400, detail="Insufficient balance.")
            balance = cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ? RETURNING balance",
                (stake, current_user["id"]),
            ).fetchone()["balance"]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        rnd.bets[key] = {
            "name": current_user.get("username") or "Player",
            "amount": stake,
            "auto_cashout": req.auto_cashout,
            "cashed_at": None,
            "payout": 0.0,
        }

    return {"status": "success", "round": rnd.number, "amount": stake, "balance": round(float(balance), 2)}


@router.post("/cancel")
def cancel_bet(req: CashoutRequest, current_user: dict = Depends(get_current_user)):
    """Take a queued bet back while the round has not taken off yet."""
    now = time.time()
    with _lock:
        rnd = _advance(now)
        key = (current_user["id"], req.panel)
        bet = rnd.bets.get(key)
        if not bet:
            raise HTTPException(status_code=400, detail="No bet on this panel.")
        if rnd.phase(now) != "betting":
            raise HTTPException(status_code=400, detail="The round has already taken off.")
        del rnd.bets[key]

    conn = get_db_connection()
    try:
        balance = conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ? RETURNING balance",
            (bet["amount"], current_user["id"]),
        ).fetchone()["balance"]
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "refunded": bet["amount"], "balance": round(float(balance), 2)}


@router.post("/cashout")
def cash_out(req: CashoutRequest, current_user: dict = Depends(get_current_user)):
    now = time.time()
    with _lock:
        rnd = _advance(now)
        key = (current_user["id"], req.panel)
        bet = rnd.bets.get(key)
        if not bet:
            raise HTTPException(status_code=400, detail="No bet on this panel.")
        if bet["cashed_at"]:
            raise HTTPException(status_code=400, detail="Already cashed out.")
        # The crash time is fixed by the seed, so a request that arrives late
        # is refused on the clock -- there is no window to cash out after it.
        if rnd.phase(now) != "flying":
            raise HTTPException(status_code=400, detail="The plane already flew away.")

        multiplier = rnd.multiplier(now)
        payout = round(bet["amount"] * multiplier, 2)
        bet["cashed_at"] = multiplier
        bet["payout"] = payout

    conn = get_db_connection()
    try:
        balance = conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ? RETURNING balance",
            (payout, current_user["id"]),
        ).fetchone()["balance"]
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "multiplier": multiplier,
        "payout": payout,
        "balance": round(float(balance), 2),
    }


@router.get("/fairness/{round_no}")
def fairness(round_no: int):
    """Let a player verify a finished round themselves."""
    live = current_round()
    if round_no >= live.number:
        raise HTTPException(status_code=400, detail="That round has not finished yet.")
    return {
        "round": round_no,
        "server_seed": server_seed(round_no),
        "seed_hash": seed_hash(round_no),
        "crash": crash_point(round_no),
    }
