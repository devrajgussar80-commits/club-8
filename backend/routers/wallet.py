"""Deposit orders, UTR submission, withdrawals and the server-rendered UPI QR.

The backend is the source of truth for balances. A deposit only credits the
wallet when an admin approves it; a withdrawal reserves funds immediately and
refunds them once on rejection.
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

import config
from database import get_db_connection
from deps import get_current_user
from game_engine import python_engine
from schemas import DepositOrderRequest, DepositRequest, WithdrawRequest
from settings_store import get_bool_setting, get_setting, get_wallet_settings

router = APIRouter(prefix="/api/wallet", tags=["wallet"])

ORDER_ID_RE = re.compile(config.ORDER_ID_PATTERN)

def _qr_limits(qr) -> tuple:
    return float(qr["min_amount"] or 100), float(qr["max_amount"] or 50000)

@router.get("/active-qr")
def get_active_qr():
    return python_engine.get_active_qr_code()

@router.get("/qr-pool")
def get_qr_pool():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, name, note, qr_url, upi_id, min_amount, max_amount "
        "FROM qr_codes WHERE is_active = 1 ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {"qr_codes": [dict(r) for r in rows], "count": len(rows)}

@router.post("/deposit-order")
def create_deposit_order(req: DepositOrderRequest, current_user: dict = Depends(get_current_user)):
    """Hand out the next QR in the rotation and pin it to a fresh order.

    Every order gets the least-recently-used QR, so abandoning one and starting
    again shows a different account instead of the same one every time.
    """
    amount = round(float(req.amount), 2)
    conn = get_db_connection()
    cursor = conn.cursor()
    if not get_bool_setting(conn, "deposits_enabled", True):
        conn.close()
        raise HTTPException(status_code=503, detail="Deposits are temporarily paused by Admin")

    # NULLS FIRST replaces SQLite's COALESCE(last_used_at, '') -- a never-used
    # QR must still sort ahead of one with a real timestamp, and in Postgres
    # you cannot COALESCE a timestamptz with an empty string.
    # FOR UPDATE serialises the rotation so two orders never take the same QR.
    qr = cursor.execute(
        """SELECT * FROM qr_codes WHERE is_active = 1
           ORDER BY last_used_at ASC NULLS FIRST, created_at ASC
           LIMIT 1 FOR UPDATE"""
    ).fetchone()
    if not qr:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Admin has not enabled any deposit QR yet")

    minimum, maximum = _qr_limits(qr)
    if amount < minimum or amount > maximum:
        conn.rollback()
        conn.close()
        raise HTTPException(
            status_code=400, detail=f"Deposit must be between ₹{minimum:.2f} and ₹{maximum:.2f}"
        )

    order_id = f"ORD{uuid.uuid4().hex[:12].upper()}"
    cursor.execute(
        "UPDATE qr_codes SET last_used_at = ? WHERE id = ?",
        (datetime.now(timezone.utc), qr["id"]),
    )
    cursor.execute(
        "INSERT INTO deposit_orders (id, user_id, qr_id, amount) VALUES (?, ?, ?, ?)",
        (order_id, current_user["id"], qr["id"], amount),
    )
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "order_id": order_id,
        "amount": amount,
        "qr": {
            "id": qr["id"],
            "name": qr["name"],
            "note": qr["note"],
            "qr_url": qr["qr_url"],
            "upi_id": qr["upi_id"],
            "min_amount": minimum,
            "max_amount": maximum,
        },
    }

@router.post("/deposit")
def submit_deposit(req: DepositRequest, current_user: dict = Depends(get_current_user)):
    amount = round(float(req.amount), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Enter a valid deposit amount")
    utr = (req.utr or "").strip()
    if len(utr) != 12 or not utr.isdigit():
        raise HTTPException(status_code=400, detail="Please enter a valid 12-digit UTR number!")
    order_id = (req.order_id or "").strip().upper()
    if not ORDER_ID_RE.fullmatch(order_id):
        raise HTTPException(status_code=400, detail="Invalid or expired deposit order")
    if not req.qr_id:
        raise HTTPException(status_code=400, detail="Active payment QR is required")

    conn = get_db_connection()
    cursor = conn.cursor()
    if not get_bool_setting(conn, "deposits_enabled", True):
        conn.close()
        raise HTTPException(status_code=503, detail="Deposits are temporarily paused by Admin")

    # Accept the QR the player was actually shown. Rotation, or an admin
    # disabling that QR while the player was paying, must not invalidate money
    # that has already left their bank.
    order = cursor.execute(
        "SELECT * FROM deposit_orders WHERE id = ? AND user_id = ?",
        (order_id, current_user["id"]),
    ).fetchone()
    qr_id = order["qr_id"] if order else req.qr_id

    qr = cursor.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(
            status_code=400, detail="That payment QR no longer exists. Start a new deposit."
        )

    minimum, maximum = _qr_limits(qr)
    if amount < minimum or amount > maximum:
        conn.close()
        raise HTTPException(
            status_code=400, detail=f"Deposit must be between ₹{minimum:.2f} and ₹{maximum:.2f}"
        )

    existing = cursor.execute("SELECT id FROM upi_deposits WHERE utr = ?", (utr,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="This UTR / Ref Number has already been submitted!")

    dep_id = f"DEP-{uuid.uuid4().hex[:10].upper()}"
    try:
        cursor.execute(
            """
            INSERT INTO upi_deposits
                (id, user_id, user_name, amount, utr, qr_id, order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (dep_id, current_user["id"], current_user["username"], amount, utr, qr_id, order_id),
        )
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail="This deposit order has already been submitted")

    cursor.execute("UPDATE deposit_orders SET consumed = 1 WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}

@router.post("/withdraw")
def submit_withdraw(req: WithdrawRequest, current_user: dict = Depends(get_current_user)):
    amount = round(float(req.amount), 2)
    destination = (req.upi_id or "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    if not get_bool_setting(conn, "withdrawals_enabled", True):
        conn.close()
        raise HTTPException(status_code=503, detail="Withdrawals are temporarily paused by Admin")
    minimum = float(get_setting(conn, "withdrawal_min", "200"))
    if amount < minimum:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is ₹{minimum:.2f}")
    if not destination or len(destination) > 400:
        conn.close()
        raise HTTPException(status_code=400, detail="Add a valid bank account or UPI ID")

    # Reserve balance under a row lock, so two withdrawals submitted at once
    # cannot both pass the check and overdraw the wallet.
    latest_user = cursor.execute(
        "SELECT balance FROM users WHERE id = ? FOR UPDATE", (current_user["id"],)
    ).fetchone()
    if not latest_user or float(latest_user["balance"]) < amount:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient wallet balance!")
    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, current_user["id"]))

    wth_id = f"WTH-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute(
        """
        INSERT INTO upi_withdrawals (id, user_id, user_name, amount, upi_id, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (wth_id, current_user["id"], current_user["username"], amount, destination),
    )

    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}

@router.get("/deposits")
def get_my_deposits(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, amount, utr, order_id, status, timestamp, processed_at "
        "FROM upi_deposits WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return {"deposits": [dict(row) for row in rows]}

@router.get("/withdrawals")
def get_my_withdrawals(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, amount, upi_id, status, timestamp, processed_at "
        "FROM upi_withdrawals WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return {"withdrawals": [dict(row) for row in rows]}

@router.get("/settings")
def get_public_wallet_settings():
    conn = get_db_connection()
    result = get_wallet_settings(conn)
    conn.close()
    return result
