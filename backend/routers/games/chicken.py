"""Chicken Road: cross the lanes, cash out before you get hit.

Server-authoritative. The old build ran entirely in the browser and moved a
local balance, so a player could bet money they did not have and "win" money
that never touched the wallet. Here the stake is debited on the server, the
bust lane is drawn on the server with the OS CSPRNG, and each jump is revealed
by the server -- the browser only animates what it is told.

Fairness / house edge: each lane is survived with probability `safe`, and the
multiplier grows by `growth` per lane. A fair game would grow by 1/safe; growth
is set a little below that, and the gap is the house edge:

    easy    p=.82  fair 1.22  growth 1.18
    medium  p=.70  fair 1.43  growth 1.34
    hard    p=.58  fair 1.72  growth 1.58
    hardcore p=.46 fair 2.17  growth 1.92
"""

import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import luck
from database import get_db_connection
from deps import get_current_user
from game_controls import check_playable, get_controls
from games_core import (
    close_round,
    hold_stake,
    load_round,
    open_round,
    player_run,
    save_round,
    secure_below,
    settle_held,
)

router = APIRouter(prefix="/api/games/chicken", tags=["games"])

GAME = "chicken"
LANES = 9
MODES = {
    "easy": {"safe": 0.82, "growth": 1.18},
    "medium": {"safe": 0.70, "growth": 1.34},
    "hard": {"safe": 0.58, "growth": 1.58},
    "hardcore": {"safe": 0.46, "growth": 1.92},
}

# Rounds are persisted in `open_rounds` (see games_core), not held in memory:
# a restart used to destroy the round while its stake stayed debited. The lock
# only serialises this process's own requests for the same player.
_lock = threading.RLock()


def _multiplier(mode: str, lane: int, cap: float = 0.0) -> float:
    """Multiplier after `lane` safe jumps. lane 0 (no jumps) is 1.0.

    `cap` is the game's max_win control, a multiple of the stake, so clamping
    the multiplier itself is exactly the cap -- and it clamps the number on
    screen at the same time. Hardcore compounds to 285x over nine lanes, which
    is the sort of return a cap exists to stop; the ladder simply stops
    climbing once it reaches the ceiling.
    """
    value = round(MODES[mode]["growth"] ** lane, 2)
    return min(value, cap) if cap > 0 else value


def _draw_bust_lane(mode: str, start: int = 0) -> int:
    """First lane at or after `start` that the chicken fails on. LANES means
    it clears every lane. Drawn once at bet time so the outcome is fixed
    before any jump -- the reveal cannot be steered by how the player taps.

    `start` is only ever above 0 when a bust is being re-drawn into a
    survival, and then the lanes already crossed keep the result they had."""
    safe = MODES[mode]["safe"]
    for lane in range(start, LANES):
        if not secure_below(safe):
            return lane
    return LANES


class BetRequest(BaseModel):
    amount: float
    mode: str = "easy"


class RoundRef(BaseModel):
    # Guards against a stale tab acting on a round that already ended.
    round_id: str


@router.get("/config")
def config():
    conn = get_db_connection()
    try:
        cap = get_controls(conn, GAME)["max_win"]
    finally:
        conn.close()
    return {
        "lanes": LANES,
        "max_win": cap,
        "modes": {
            name: {
                "safe": m["safe"],
                "growth": m["growth"],
                # The ladder the client draws is the capped one, so a player
                # is never shown a rung the cash-out will not honour.
                "multipliers": [_multiplier(name, lane + 1, cap) for lane in range(LANES)],
            }
            for name, m in MODES.items()
        },
    }


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    """Any round this player still has open.

    Same reason as the mines endpoint: a page reload used to strand a debited
    stake, because the browser lost the round id while the server still held
    the round. The bust lane is never sent -- only how far the player has got.
    """
    rnd = load_round(current_user["id"], GAME)
    if not rnd:
        return {"active": False}
    lane = rnd["lane"]
    multiplier = _multiplier(rnd["mode"], lane, _cap(rnd))
    return {
        "active": True,
        "round_id": rnd["id"],
        "mode": rnd["mode"],
        "stake": rnd["stake"],
        "lane": lane,
        "multiplier": multiplier,
        "cashout_value": round(rnd["stake"] * multiplier, 2),
    }


def _cap(rnd: dict) -> float:
    """The cap this round was opened under. Rounds opened before the control
    existed carry none, and finish on the ladder they started on."""
    return float(rnd.get("cap") or 0)


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    mode = req.mode if req.mode in MODES else "easy"

    # The game's own switches: offline, and the per-game stake limits. Checked
    # before hold_stake, so a refused round never touches the wallet.
    conn = get_db_connection()
    try:
        controls = check_playable(conn, GAME, round(float(req.amount or 0), 2))
    finally:
        conn.close()

    with _lock:
        if load_round(current_user["id"], GAME):
            raise HTTPException(status_code=400, detail="Finish the current round first.")

        held = hold_stake(current_user, req.amount)  # debits, or raises 400

        round_id = f"CR-{uuid.uuid4().hex[:10].upper()}"
        open_round(
            current_user["id"], GAME, round_id, held["stake"],
            {
                "mode": mode,
                "bust_lane": _draw_bust_lane(mode),
                "lane": 0,  # safe jumps completed so far
                # Pinned to the round rather than re-read on every jump: a cap
                # the admin changes mid-round must not move the ladder under
                # a player who is already several lanes along it.
                "cap": controls["max_win"],
            },
        )
    return {
        "status": "success",
        "round_id": round_id,
        "mode": mode,
        "stake": held["stake"],
        "balance": held["balance"],
        "lane": 0,
        "multiplier": 1.0,
        "max_win": controls["max_win"],
    }


def _active(user_id: str, round_id: str) -> dict:
    rnd = load_round(user_id, GAME, round_id)
    if not rnd:
        raise HTTPException(status_code=400, detail="No active round.")
    return rnd


@router.post("/jump")
def jump(req: RoundRef, current_user: dict = Depends(get_current_user)):
    with _lock:
        rnd = _active(current_user["id"], req.round_id)
        attempt_lane = rnd["lane"]  # 0-indexed lane being attempted now
        rescued = bool(rnd.get("rescued"))

        if attempt_lane == rnd["bust_lane"] and not rescued:
            # The player's win rate gets one go at this per round: the bust is
            # re-drawn somewhere further down the road and the jump lands.
            # It is not a win yet -- from here they still have to bank it.
            if luck.rescues(player_run(current_user)):
                rnd["bust_lane"] = _draw_bust_lane(rnd["mode"], attempt_lane + 1)
                rescued = True

        if attempt_lane == rnd["bust_lane"]:
            # Hit. Stake was already taken; settle a zero payout and close out.
            stake = rnd["stake"]
            mode = rnd["mode"]
            close_round(current_user["id"], GAME)
            settled = settle_held(
                current_user["id"], GAME, stake, 0.0,
                {"mode": mode, "result": "hit", "lane": attempt_lane + 1},
            )
            return {
                "status": "success",
                "result": "hit",
                "lane": attempt_lane + 1,
                "multiplier": 0.0,
                "payout": 0.0,
                "balance": settled["balance"],
            }

        rnd["lane"] += 1
        lane = rnd["lane"]
        multiplier = _multiplier(rnd["mode"], lane, _cap(rnd))

        # Cleared the final lane -> auto cash out at the top multiplier.
        if lane >= LANES:
            stake = rnd["stake"]
            mode = rnd["mode"]
            close_round(current_user["id"], GAME)
            settled = settle_held(
                current_user["id"], GAME, stake, stake * multiplier,
                {"mode": mode, "result": "cleared", "lane": lane, "multiplier": multiplier},
            )
            return {
                "status": "success",
                "result": "cleared",
                "lane": lane,
                "multiplier": multiplier,
                "payout": settled["payout"],
                "balance": settled["balance"],
            }

        # Survived: persist the new lane so a restart cannot rewind progress.
        save_round(
            current_user["id"], GAME,
            {
                "mode": rnd["mode"],
                "bust_lane": rnd["bust_lane"],
                "lane": lane,
                "cap": _cap(rnd),
                "rescued": rescued,
            },
        )

    return {
        "status": "success",
        "result": "safe",
        "lane": lane,
        "multiplier": multiplier,
        "cashout_value": round(rnd["stake"] * multiplier, 2),
    }


@router.post("/cashout")
def cashout(req: RoundRef, current_user: dict = Depends(get_current_user)):
    with _lock:
        rnd = _active(current_user["id"], req.round_id)
        lane = rnd["lane"]
        if lane <= 0:
            raise HTTPException(status_code=400, detail="Jump at least once before cashing out.")
        stake = rnd["stake"]
        mode = rnd["mode"]
        multiplier = _multiplier(mode, lane, _cap(rnd))
        close_round(current_user["id"], GAME)

    settled = settle_held(
        current_user["id"], GAME, stake, stake * multiplier,
        {"mode": mode, "result": "cashout", "lane": lane, "multiplier": multiplier},
    )
    return {
        "status": "success",
        "result": "cashout",
        "lane": lane,
        "multiplier": multiplier,
        "payout": settled["payout"],
        "balance": settled["balance"],
    }
