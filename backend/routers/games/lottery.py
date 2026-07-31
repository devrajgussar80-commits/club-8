"""Daily lottery: one ticket number per player per draw, paid by UPI deposit.

Flow, end to end:

    player picks a free number -> pays ₹100 by UPI -> submits the UTR
    admin sees the ticket, approves it (that is what reserves the number)
    admin enters the winning number for the day
    the holder of that number appears in the dashboard with a Pay ₹1000 button

The prize is *not* credited automatically. The admin presses the button, and
`paid_at` is what stops a second press paying twice.

Numbers run 00-99, so a fully sold draw takes ₹10,000 and pays ₹1,000.
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import UniqueViolation, get_db_connection
from deps import get_current_user, require_admin
from game_controls import check_playable

router = APIRouter(tags=["lottery"])

GAME = "lottery"
IST = ZoneInfo("Asia/Kolkata")

TICKET_PRICE = 100.0
PRIZE_AMOUNT = 1000.0
MIN_TICKET = 0
MAX_TICKET = 99


def today_ist() -> date:
    """Draws roll over at midnight IST, not at the server's local midnight."""
    return datetime.now(IST).date()


def _ensure_draw(cursor, draw_date: date) -> dict:
    """Get the draw row for a date, creating it open if it does not exist."""
    row = cursor.execute(
        "SELECT * FROM lottery_draws WHERE draw_date = ?", (draw_date,)
    ).fetchone()
    if row:
        return dict(row)
    cursor.execute(
        """
        INSERT INTO lottery_draws (draw_date, status, ticket_price, prize_amount)
        VALUES (?, 'open', ?, ?)
        ON CONFLICT (draw_date) DO NOTHING
        """,
        (draw_date, TICKET_PRICE, PRIZE_AMOUNT),
    )
    row = cursor.execute(
        "SELECT * FROM lottery_draws WHERE draw_date = ?", (draw_date,)
    ).fetchone()
    return dict(row)


# ------------------------------------------------------------------ player API

class BuyTicketRequest(BaseModel):
    ticket_number: int
    utr: str
    qr_id: str | None = None


@router.get("/api/games/lottery/today")
def today(current_user: dict = Depends(get_current_user)):
    draw_date = today_ist()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        draw = _ensure_draw(cursor, draw_date)
        conn.commit()

        # A rejected ticket releases its number back to the pool.
        taken = cursor.execute(
            "SELECT ticket_number FROM lottery_tickets "
            "WHERE draw_date = ? AND status <> 'rejected' ORDER BY ticket_number",
            (draw_date,),
        ).fetchall()
        mine = cursor.execute(
            "SELECT id, ticket_number, status, price, created_at FROM lottery_tickets "
            "WHERE draw_date = ? AND user_id = ? ORDER BY created_at",
            (draw_date, current_user["id"]),
        ).fetchall()

        # The active QR so the player knows where to send the ₹100.
        qr = cursor.execute(
            "SELECT id, name, qr_url, upi_id FROM qr_codes WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    return {
        "draw_date": str(draw_date),
        "status": draw["status"],
        "ticket_price": float(draw["ticket_price"]),
        "prize_amount": float(draw["prize_amount"]),
        "winning_ticket": draw["winning_ticket"],
        "range": [MIN_TICKET, MAX_TICKET],
        "taken": [row["ticket_number"] for row in taken],
        "my_tickets": [dict(row) for row in mine],
        "payment_qr": dict(qr) if qr else None,
    }


@router.post("/api/games/lottery/buy")
def buy_ticket(req: BuyTicketRequest, current_user: dict = Depends(get_current_user)):
    if not MIN_TICKET <= req.ticket_number <= MAX_TICKET:
        raise HTTPException(
            status_code=400, detail=f"Ticket number must be {MIN_TICKET:02d}-{MAX_TICKET:02d}."
        )
    utr = (req.utr or "").strip()
    if len(utr) < 6:
        raise HTTPException(status_code=400, detail="Enter the UPI reference (UTR) of your payment.")

    draw_date = today_ist()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # The dashboard's on/off switch has to reach ticket sales, otherwise
        # turning the lottery off would only hide it and still take money.
        check_playable(conn, GAME, float(TICKET_PRICE))

        draw = _ensure_draw(cursor, draw_date)
        if draw["status"] != "open":
            raise HTTPException(status_code=400, detail="Today's draw is closed.")

        ticket_id = f"LOT-{uuid.uuid4().hex[:10].upper()}"
        try:
            cursor.execute(
                """
                INSERT INTO lottery_tickets
                    (id, draw_date, user_id, user_name, ticket_number, price, status, utr, qr_id)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    ticket_id,
                    draw_date,
                    current_user["id"],
                    current_user["username"],
                    req.ticket_number,
                    float(draw["ticket_price"]),
                    utr,
                    req.qr_id,
                ),
            )
        except UniqueViolation:
            # Either the number is already claimed for this draw, or this UTR
            # has already been used. Both are partial unique indexes.
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="That ticket number is already taken, or this UTR was already submitted.",
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "ticket_id": ticket_id,
        "ticket_number": req.ticket_number,
        "message": "Ticket booked. It becomes active once the admin confirms your payment.",
    }


@router.get("/api/games/lottery/results")
def results(limit: int = 14, current_user: dict = Depends(get_current_user)):
    limit = max(1, min(limit, 60))
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT d.draw_date, d.winning_ticket, d.prize_amount, d.status, t.user_name
            FROM lottery_draws d
            LEFT JOIN lottery_tickets t
                   ON t.draw_date = d.draw_date
                  AND t.ticket_number = d.winning_ticket
                  AND t.status = 'approved'
            WHERE d.winning_ticket IS NOT NULL
            ORDER BY d.draw_date DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {"results": [dict(row) for row in rows]}


# ------------------------------------------------------------------- admin API

class DrawRequest(BaseModel):
    draw_date: str | None = None
    winning_ticket: int


class ReviewRequest(BaseModel):
    action: str  # "approve" | "reject"


@router.get("/api/admin/lottery/tickets")
def admin_tickets(draw_date: str | None = None, _: bool = Depends(require_admin)):
    target = draw_date or str(today_ist())
    conn = get_db_connection()
    try:
        draw = conn.execute(
            "SELECT * FROM lottery_draws WHERE draw_date = ?", (target,)
        ).fetchone()
        rows = conn.execute(
            """
            SELECT t.*, u.phone, u.balance
            FROM lottery_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.draw_date = ?
            ORDER BY t.created_at DESC
            """,
            (target,),
        ).fetchall()
    finally:
        conn.close()

    tickets = [dict(row) for row in rows]
    approved = [t for t in tickets if t["status"] == "approved"]
    return {
        "draw_date": target,
        "draw": dict(draw) if draw else None,
        "tickets": tickets,
        "summary": {
            "total": len(tickets),
            "pending": sum(1 for t in tickets if t["status"] == "pending"),
            "approved": len(approved),
            "collected": round(sum(t["price"] for t in approved), 2),
        },
    }


@router.post("/api/admin/lottery/tickets/{ticket_id}/review")
def admin_review_ticket(
    ticket_id: str, req: ReviewRequest, _: bool = Depends(require_admin)
):
    """Approve or reject a ticket. The action is in the body, not the path:
    a `{action}` path segment would also swallow `/pay` below."""
    if req.action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Action must be approve or reject.")
    status = "approved" if req.action == "approve" else "rejected"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        row = cursor.execute(
            "SELECT * FROM lottery_tickets WHERE id = ? FOR UPDATE", (ticket_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Ticket not found.")
        if row["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Ticket is already {row['status']}.")
        cursor.execute("UPDATE lottery_tickets SET status = ? WHERE id = ?", (status, ticket_id))

        if status == "approved":
            # Approval is the moment the ₹100 is really taken, so that is when
            # the lottery books its stake. Without this the analytics would
            # only ever see prize payouts and the game would look loss-making.
            cursor.execute(
                """
                INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    f"LOT-{uuid.uuid4().hex[:10].upper()}",
                    GAME,
                    row["user_id"],
                    float(row["price"]),
                    f'{{"ticket": {row["ticket_number"]}, "draw": "{row["draw_date"]}", "event": "buy"}}',
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"status": "success", "ticket_status": status}


@router.post("/api/admin/lottery/draw")
def admin_set_winner(req: DrawRequest, _: bool = Depends(require_admin)):
    """Record the winning number and return who is holding it."""
    if not MIN_TICKET <= req.winning_ticket <= MAX_TICKET:
        raise HTTPException(
            status_code=400, detail=f"Winning ticket must be {MIN_TICKET:02d}-{MAX_TICKET:02d}."
        )
    target = req.draw_date or str(today_ist())

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_draw(cursor, target)
        cursor.execute(
            """
            UPDATE lottery_draws
               SET winning_ticket = ?, status = 'drawn', drawn_at = NOW()
             WHERE draw_date = ?
            """,
            (req.winning_ticket, target),
        )
        winners = cursor.execute(
            """
            SELECT t.*, u.phone, u.balance
            FROM lottery_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.draw_date = ? AND t.ticket_number = ? AND t.status = 'approved'
            """,
            (target, req.winning_ticket),
        ).fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "success",
        "draw_date": target,
        "winning_ticket": req.winning_ticket,
        "winners": [dict(row) for row in winners],
    }


@router.post("/api/admin/lottery/tickets/{ticket_id}/pay")
def admin_pay_prize(ticket_id: str, _: bool = Depends(require_admin)):
    """Top the winner's wallet up by the draw's prize amount, once."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        ticket = cursor.execute(
            """
            SELECT t.*, d.winning_ticket, d.prize_amount
            FROM lottery_tickets t
            JOIN lottery_draws d ON d.draw_date = t.draw_date
            WHERE t.id = ? FOR UPDATE OF t
            """,
            (ticket_id,),
        ).fetchone()
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found.")
        if ticket["status"] != "approved":
            raise HTTPException(status_code=400, detail="Only an approved ticket can be paid.")
        if ticket["winning_ticket"] is None or ticket["winning_ticket"] != ticket["ticket_number"]:
            raise HTTPException(status_code=400, detail="This ticket did not win the draw.")
        if ticket["paid_at"] is not None:
            raise HTTPException(status_code=400, detail="Prize already paid for this ticket.")

        prize = float(ticket["prize_amount"])
        balance = cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ? RETURNING balance",
            (prize, ticket["user_id"]),
        ).fetchone()["balance"]
        cursor.execute(
            "UPDATE lottery_tickets SET paid_at = NOW() WHERE id = ?", (ticket_id,)
        )
        # Logged like any other game round so the lottery shows up in the
        # games analytics next to the arcade titles.
        cursor.execute(
            """
            INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"LOT-{uuid.uuid4().hex[:10].upper()}",
                GAME,
                ticket["user_id"],
                0,
                prize,
                f'{{"ticket": {ticket["ticket_number"]}, "draw": "{ticket["draw_date"]}"}}',
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"status": "success", "prize": prize, "balance": round(float(balance), 2)}
