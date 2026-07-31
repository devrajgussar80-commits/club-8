"""WinGo round status, bet placement and history.

WinGo is deliberately open to every signed-in user. The admin access switch
gates only the premium arcade games, which run client-side.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

import config
from database import get_db_connection
from deps import get_current_user
from game_engine import python_engine
from schemas import BetRequest
from settings_store import get_approved_deposit_total

router = APIRouter(prefix="/api/game", tags=["game"])


def _validate_selection(req: BetRequest) -> None:
    if req.select_type == "size" and req.selection not in config.BET_SIZES:
        raise HTTPException(status_code=400, detail="Invalid Big/Small selection.")
    if req.select_type == "color" and req.selection not in config.BET_COLORS:
        raise HTTPException(status_code=400, detail="Invalid color selection.")
    if req.select_type == "number":
        try:
            selected_number = int(req.selection)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid number selection.")
        if selected_number < 0 or selected_number > 9:
            raise HTTPException(status_code=400, detail="Number must be between 0 and 9.")


@router.get("/status")
def get_game_status(room: str = "parity", current_user: dict = Depends(get_current_user)):
    try:
        room_status = python_engine.get_status(room)
    except KeyError:
        raise HTTPException(status_code=404, detail="Game room not found")

    game_access_enabled = bool(current_user.get("game_access_enabled", 0)) if current_user else False
    approved_total = 0.0
    if current_user:
        conn = get_db_connection()
        approved_total = get_approved_deposit_total(conn, current_user["id"])
        conn.close()

    return {
        **room_status,
        "active_room": room,
        "user_balance": current_user["balance"] if current_user else 0,
        "game_access_enabled": game_access_enabled,
        "approved_deposit_total": approved_total,
        "game_access_min_deposit": config.GAME_ACCESS_MIN_DEPOSIT,
        "has_approved_min_deposit": approved_total >= config.GAME_ACCESS_MIN_DEPOSIT,
    }


@router.post("/bet")
def place_bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    _validate_selection(req)

    try:
        room_status = python_engine.get_status(req.room)
    except KeyError:
        raise HTTPException(status_code=404, detail="Game room not found")

    if not python_engine.is_bet_open(req.room, req.period):
        raise HTTPException(
            status_code=400,
            detail="This round is closed. Please place the bet in the next round.",
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    total_stake = round(req.amount * req.multiplier, 2)

    # Re-read the balance under a row lock. The snapshot on current_user is
    # stale, so checking it alone let concurrent bets overdraw the wallet.
    # FOR UPDATE holds the user row until commit, so a second bet arriving in
    # parallel waits here and then sees the debited balance.
    latest = cursor.execute(
        "SELECT balance FROM users WHERE id = ? FOR UPDATE", (current_user["id"],)
    ).fetchone()
    if not latest or float(latest["balance"]) < total_stake:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient balance.")

    cursor.execute(
        "UPDATE users SET balance = balance - ? WHERE id = ?", (total_stake, current_user["id"])
    )

    bet_id = f"BET-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute(
        """
        INSERT INTO bets
            (id, user_id, period, select_type, selection, amount, multiplier, total_stake, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            bet_id,
            current_user["id"],
            room_status["period"],
            req.select_type,
            req.selection,
            req.amount,
            req.multiplier,
            total_stake,
        ),
    )

    conn.commit()
    conn.close()

    return {"status": "success", "bet_id": bet_id, "total_stake": total_stake}


@router.get("/history")
def get_game_history(room: str = "parity"):
    if room not in python_engine.rooms:
        raise HTTPException(status_code=404, detail="Game room not found")
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM rounds WHERE status = 'completed' AND room = ? ORDER BY period DESC LIMIT 500",
        (room,),
    ).fetchall()
    conn.close()
    return {"history": [dict(r) for r in rows]}


@router.get("/my-bets")
def get_my_bets(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM bets WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return {"bets": [dict(r) for r in rows]}
