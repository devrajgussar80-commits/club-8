"""Mines: open safe tiles on a 5x5 grid, cash out before hitting a mine.

Server-authoritative, for the same reason as Chicken Road: the old build never
touched the wallet at all, so it was not a real-money game. Here the stake is
debited on the server, the mine positions are drawn on the server, and each
tile reveal is checked on the server.

Payout is the fair multiplier with a house edge applied. After opening `k`
safe tiles out of a grid of 25 with `m` mines, the fair multiplier is the
inverse of the probability of having survived that far:

    fair(k) = C(25, k) / C(25 - m, k)

We pay RTP (97%) of that, so every mine count has the same house edge instead
of it drifting with difficulty.
"""

import secrets
import threading
import uuid
from math import comb

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps import get_current_user
from games_core import hold_stake, settle_held

router = APIRouter(prefix="/api/games/mines", tags=["games"])

GAME = "mines"
TILES = 25
MIN_MINES = 1
MAX_MINES = 24
RTP = 0.97


def _multiplier(mines: int, opened: int) -> float:
    """Fair multiplier for `opened` safe tiles, scaled by RTP. 0 opened = 1.0."""
    if opened <= 0:
        return 1.0
    safe_tiles = TILES - mines
    if opened > safe_tiles:
        opened = safe_tiles
    fair = comb(TILES, opened) / comb(safe_tiles, opened)
    return round(fair * RTP, 4)


def _draw_mines(count: int) -> set:
    """`count` distinct mine positions in [0, 25), drawn from the OS CSPRNG.
    sample() picks without replacement, so every layout is equally likely."""
    return set(secrets.SystemRandom().sample(range(TILES), count))


class BetRequest(BaseModel):
    amount: float
    mines: int = 3


class RevealRequest(BaseModel):
    round_id: str
    tile: int


class RoundRef(BaseModel):
    round_id: str


_lock = threading.RLock()
_rounds = {}  # user_id -> active round


@router.get("/config")
def config():
    return {"tiles": TILES, "min_mines": MIN_MINES, "max_mines": MAX_MINES}


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    mines = max(MIN_MINES, min(MAX_MINES, int(req.mines or 3)))
    with _lock:
        if _rounds.get(current_user["id"]):
            raise HTTPException(status_code=400, detail="Finish the current round first.")
        held = hold_stake(current_user, req.amount)
        round_id = f"MN-{uuid.uuid4().hex[:10].upper()}"
        _rounds[current_user["id"]] = {
            "id": round_id,
            "stake": held["stake"],
            "mines": _draw_mines(mines),
            "mine_count": mines,
            "opened": set(),
        }
    return {
        "status": "success",
        "round_id": round_id,
        "stake": held["stake"],
        "balance": held["balance"],
        "mines": mines,
        "multiplier": 1.0,
    }


def _active(user_id: str, round_id: str) -> dict:
    rnd = _rounds.get(user_id)
    if not rnd or rnd["id"] != round_id:
        raise HTTPException(status_code=400, detail="No active round.")
    return rnd


@router.post("/reveal")
def reveal(req: RevealRequest, current_user: dict = Depends(get_current_user)):
    with _lock:
        rnd = _active(current_user["id"], req.round_id)
        tile = req.tile
        if not (0 <= tile < TILES):
            raise HTTPException(status_code=400, detail="Invalid tile.")
        if tile in rnd["opened"]:
            raise HTTPException(status_code=400, detail="Tile already opened.")

        if tile in rnd["mines"]:
            # Boom. Stake already taken; close with a zero payout and reveal the
            # full mine layout so the client can show where they were.
            stake = rnd["stake"]
            layout = sorted(rnd["mines"])
            opened = len(rnd["opened"])
            del _rounds[current_user["id"]]
            settled = settle_held(
                current_user["id"], GAME, stake, 0.0,
                {"result": "boom", "tile": tile, "opened": opened, "mines": layout},
            )
            return {
                "status": "success",
                "result": "boom",
                "tile": tile,
                "mines": layout,
                "payout": 0.0,
                "balance": settled["balance"],
            }

        rnd["opened"].add(tile)
        opened = len(rnd["opened"])
        multiplier = _multiplier(rnd["mine_count"], opened)
        safe_tiles = TILES - rnd["mine_count"]

        # Opened every safe tile -> auto cash out at the top.
        if opened >= safe_tiles:
            stake = rnd["stake"]
            layout = sorted(rnd["mines"])
            del _rounds[current_user["id"]]
            settled = settle_held(
                current_user["id"], GAME, stake, stake * multiplier,
                {"result": "cleared", "opened": opened, "multiplier": multiplier},
            )
            return {
                "status": "success",
                "result": "cleared",
                "tile": tile,
                "opened": opened,
                "multiplier": multiplier,
                "mines": layout,
                "payout": settled["payout"],
                "balance": settled["balance"],
            }

    return {
        "status": "success",
        "result": "safe",
        "tile": tile,
        "opened": opened,
        "multiplier": multiplier,
        "cashout_value": round(rnd["stake"] * multiplier, 2),
    }


@router.post("/cashout")
def cashout(req: RoundRef, current_user: dict = Depends(get_current_user)):
    with _lock:
        rnd = _active(current_user["id"], req.round_id)
        opened = len(rnd["opened"])
        if opened <= 0:
            raise HTTPException(status_code=400, detail="Open at least one tile before cashing out.")
        stake = rnd["stake"]
        multiplier = _multiplier(rnd["mine_count"], opened)
        layout = sorted(rnd["mines"])
        del _rounds[current_user["id"]]

    settled = settle_held(
        current_user["id"], GAME, stake, stake * multiplier,
        {"result": "cashout", "opened": opened, "multiplier": multiplier},
    )
    return {
        "status": "success",
        "result": "cashout",
        "opened": opened,
        "multiplier": multiplier,
        "mines": layout,
        "payout": settled["payout"],
        "balance": settled["balance"],
    }
