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
from games_core import (
    close_round,
    hold_stake,
    load_round,
    open_round,
    save_round,
    settle_held,
)

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
# Rounds live in `open_rounds` (see games_core), not in memory: a restart used
# to destroy the round while its stake stayed debited. The lock only serialises
# this process's own concurrent requests for the same player.


@router.get("/config")
def config():
    return {"tiles": TILES, "min_mines": MIN_MINES, "max_mines": MAX_MINES}


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    """Any round this player still has open.

    Without this, reloading the page mid-round left the stake debited and the
    round stranded: the browser forgot the round id, and every new bet was
    refused with "Finish the current round first". The client calls this when
    the game opens and restores the board. Mine positions are never sent -- only
    what the player has already uncovered.
    """
    rnd = load_round(current_user["id"], GAME)
    if not rnd:
        return {"active": False}
    opened = sorted(rnd["opened"])
    multiplier = _multiplier(rnd["mine_count"], len(opened))
    return {
        "active": True,
        "round_id": rnd["id"],
        "stake": rnd["stake"],
        "mines": rnd["mine_count"],
        "opened": opened,
        "multiplier": multiplier,
        "cashout_value": round(rnd["stake"] * multiplier, 2),
    }


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    mines = max(MIN_MINES, min(MAX_MINES, int(req.mines or 3)))
    with _lock:
        if load_round(current_user["id"], GAME):
            raise HTTPException(status_code=400, detail="Finish the current round first.")
        held = hold_stake(current_user, req.amount)
        round_id = f"MN-{uuid.uuid4().hex[:10].upper()}"
        # JSON has no sets, so mine positions and opened tiles are stored as
        # lists and turned back into sets on load.
        open_round(
            current_user["id"], GAME, round_id, held["stake"],
            {"mines": sorted(_draw_mines(mines)), "mine_count": mines, "opened": []},
        )
    return {
        "status": "success",
        "round_id": round_id,
        "stake": held["stake"],
        "balance": held["balance"],
        "mines": mines,
        "multiplier": 1.0,
    }


def _active(user_id: str, round_id: str) -> dict:
    rnd = load_round(user_id, GAME, round_id)
    if not rnd:
        raise HTTPException(status_code=400, detail="No active round.")
    # Membership tests below expect sets.
    rnd["mines"] = set(rnd["mines"])
    rnd["opened"] = set(rnd["opened"])
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
            close_round(current_user["id"], GAME)
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
            close_round(current_user["id"], GAME)
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

        # Survived: persist the opened tile so a restart cannot rewind it.
        save_round(
            current_user["id"], GAME,
            {
                "mines": sorted(rnd["mines"]),
                "mine_count": rnd["mine_count"],
                "opened": sorted(rnd["opened"]),
            },
        )

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
        close_round(current_user["id"], GAME)

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
