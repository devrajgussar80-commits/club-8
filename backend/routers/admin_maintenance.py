"""Admin maintenance: flushing operational data.

This deletes real records, so two things are deliberate.

First, scope. A "flush everything" that also dropped `users`, `qr_codes` and
`system_settings` would delete the admin's own account, the payment QRs and
every game control -- locking them out of the dashboard they clicked it from.
So those three are never touched by any scope, and player *balances* survive
too: only the ledgers are cleared.

Second, the confirmation phrase. It is checked on the server, not just in the
browser, because a destructive endpoint has to be safe against a stray fetch,
a repeated request, or a mis-click on a phone -- not merely against a missing
dialog.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from deps import require_admin

router = APIRouter(prefix="/api/admin/maintenance", tags=["admin"])

CONFIRM_PHRASE = "FLUSH"

# Order matters: children before parents, so foreign keys never block a delete.
SCOPES = {
    "games": {
        "label": "Game history",
        "detail": "Every settled round, bet and game analytics row.",
        "tables": [
            "game_rounds", "bets", "rounds",
            "dice_bets", "dice_rounds",
        ],
    },
    "visitors": {
        "label": "Visitor tracking",
        "detail": "All anonymous visit sessions and their event timelines.",
        "tables": ["visitor_events", "visitor_sessions"],
    },
    "lottery": {
        "label": "Lottery",
        "detail": "All lottery tickets and draws.",
        "tables": ["lottery_tickets", "lottery_draws"],
    },
    "wallet": {
        "label": "Wallet requests",
        "detail": "Deposit and withdrawal records. Player balances are NOT changed.",
        "tables": ["deposit_orders", "upi_deposits", "upi_withdrawals"],
    },
}

# Never deleted, whatever is asked for.
PROTECTED = ("users", "qr_codes", "system_settings")


class FlushRequest(BaseModel):
    scopes: list[str]
    confirm: str


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table,),
    ).fetchone()
    return bool(row)


@router.get("/flush-scopes")
def flush_scopes(_: bool = Depends(require_admin)):
    """What can be flushed, with a live row count for each."""
    conn = get_db_connection()
    try:
        out = []
        for key, scope in SCOPES.items():
            rows = 0
            for table in scope["tables"]:
                if _table_exists(conn, table):
                    rows += int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            out.append({
                "key": key,
                "label": scope["label"],
                "detail": scope["detail"],
                "tables": scope["tables"],
                "rows": rows,
            })
    finally:
        conn.close()
    return {"scopes": out, "confirm_phrase": CONFIRM_PHRASE, "protected": list(PROTECTED)}


@router.post("/flush")
def flush(req: FlushRequest, _: bool = Depends(require_admin)):
    if req.confirm != CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400, detail=f'Type {CONFIRM_PHRASE} to confirm.'
        )
    chosen = [key for key in req.scopes if key in SCOPES]
    if not chosen:
        raise HTTPException(status_code=400, detail="Pick at least one thing to flush.")

    deleted = {}
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for key in chosen:
            for table in SCOPES[key]["tables"]:
                if table in PROTECTED or not _table_exists(conn, table):
                    continue
                # DELETE, not TRUNCATE: it respects foreign keys and rolls back
                # cleanly with the rest of the transaction if anything fails,
                # so a partial flush cannot leave orphaned rows behind.
                count = cursor.execute(
                    f"DELETE FROM {table} RETURNING 1"
                ).fetchall()
                deleted[table] = len(count)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"status": "success", "scopes": chosen, "deleted": deleted,
            "total": sum(deleted.values())}
