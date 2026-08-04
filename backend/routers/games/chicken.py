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

from deps import get_current_user
from games_core import (
    close_round,
    hold_stake,
    load_round,
    open_round,
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


def _multiplier(mode: str, lane: int) -> float:
    """Multiplier after `lane` safe jumps. lane 0 (no jumps) is 1.0."""
    return round(MODES[mode]["growth"] ** lane, 2)


def _draw_bust_lane(mode: str) -> int:
    """First lane the chicken fails on, drawn up front. LANES means it clears
    every lane. Drawn once at bet time so the outcome is fixed before any jump
    -- the reveal cannot be steered by how the player taps."""
    safe = MODES[mode]["safe"]
    for lane in range(LANES):
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
    return {
        "lanes": LANES,
        "modes": {
            name: {
                "safe": m["safe"],
                "growth": m["growth"],
                "multipliers": [_multiplier(name, lane + 1) for lane in range(LANES)],
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
    multiplier = _multiplier(rnd["mode"], lane)
    return {
        "active": True,
        "round_id": rnd["id"],
        "mode": rnd["mode"],
        "stake": rnd["stake"],
        "lane": lane,
        "multiplier": multiplier,
        "cashout_value": round(rnd["stake"] * multiplier, 2),
    }


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    mode = req.mode if req.mode in MODES else "easy"
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
        multiplier = _multiplier(rnd["mode"], lane)

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
            {"mode": rnd["mode"], "bust_lane": rnd["bust_lane"], "lane": lane},
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
        multiplier = _multiplier(mode, lane)
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
