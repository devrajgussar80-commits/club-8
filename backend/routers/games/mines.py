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
    settle_held,
)

router = APIRouter(prefix="/api/games/mines", tags=["games"])

GAME = "mines"
TILES = 25
MIN_MINES = 1
MAX_MINES = 24
RTP = 0.97


def _multiplier(mines: int, opened: int, cap: float = 0.0) -> float:
    """Fair multiplier for `opened` safe tiles, scaled by RTP. 0 opened = 1.0.

    `cap` is the game's max_win control, a multiple of the stake, so clamping
    the multiplier is the cap -- and it clamps the figure on screen with it.
    Uncapped this curve is unbounded: 24 mines pays 25x on the first tile and
    a clean 5x5 board at 3 mines pays over 2,000x, which is not a return a
    small stake should be able to reach.
    """
    if opened <= 0:
        return 1.0
    safe_tiles = TILES - mines
    if opened > safe_tiles:
        opened = safe_tiles
    fair = comb(TILES, opened) / comb(safe_tiles, opened)
    value = round(fair * RTP, 4)
    return min(value, cap) if cap > 0 else value


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


def _cap(rnd: dict) -> float:
    """The cap this round was opened under. Rounds opened before the control
    existed carry none, and finish on the curve they started on."""
    return float(rnd.get("cap") or 0)


@router.get("/config")
def config():
    conn = get_db_connection()
    try:
        cap = get_controls(conn, GAME)["max_win"]
    finally:
        conn.close()
    return {"tiles": TILES, "min_mines": MIN_MINES, "max_mines": MAX_MINES, "max_win": cap}


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
    multiplier = _multiplier(rnd["mine_count"], len(opened), _cap(rnd))
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
        held = hold_stake(current_user, req.amount)
        round_id = f"MN-{uuid.uuid4().hex[:10].upper()}"
        # JSON has no sets, so mine positions and opened tiles are stored as
        # lists and turned back into sets on load.
        open_round(
            current_user["id"], GAME, round_id, held["stake"],
            {
                "mines": sorted(_draw_mines(mines)),
                "mine_count": mines,
                "opened": [],
                # Pinned to the round rather than re-read on every tile: a cap
                # the admin changes mid-round must not move the curve under a
                # player who is already several tiles into it.
                "cap": controls["max_win"],
            },
        )
    return {
        "status": "success",
        "round_id": round_id,
        "stake": held["stake"],
        "balance": held["balance"],
        "mines": mines,
        "multiplier": 1.0,
        "max_win": controls["max_win"],
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

        rescued = bool(rnd.get("rescued"))
        if tile in rnd["mines"] and not rescued:
            # The player's win rate gets one go at this per round: the mine is
            # moved to a tile still face down and this one comes up safe. It
            # is not a win yet -- from here they still have to cash out.
            free = [
                t for t in range(TILES)
                if t != tile and t not in rnd["mines"] and t not in rnd["opened"]
            ]
            if free and luck.rescues(player_run(current_user)):
                rnd["mines"].discard(tile)
                rnd["mines"].add(free[secrets.randbelow(len(free))])
                rescued = True

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
        multiplier = _multiplier(rnd["mine_count"], opened, _cap(rnd))
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
                "cap": _cap(rnd),
                "rescued": rescued,
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
        multiplier = _multiplier(rnd["mine_count"], opened, _cap(rnd))
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
