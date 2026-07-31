"""Admin dashboard, moderation queues and payment configuration.

Every route here is behind `require_admin`, which accepts either an admin
session token or the `X-Admin-Key` shared key.
"""

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import auth as auth_helpers
import config
from database import get_db_connection
from deps import require_admin
from game_engine import python_engine
from schemas import (
    AddQRReq,
    AdminKeyRotationRequest,
    AdminLoginRequest,
    ForceResultReq,
    GrantAdminRequest,
    PlatformSettingsReq,
    PredictionModeReq,
    UserGameAccessReq,
    UserStatusReq,
)
from settings_store import (
    get_approved_deposit_total,
    get_settings,
    get_wallet_settings,
    set_setting,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_SESSION_DAYS = 30

USERS_WITH_DEPOSIT_TOTALS = """
    SELECT u.id, u.phone, u.username, u.balance, u.status, u.created_at,
           u.referral_code, u.game_access_enabled,
           COALESCE(SUM(CASE WHEN d.status = 'approved' THEN d.amount ELSE 0 END), 0)
               AS approved_deposit_total
    FROM users u
    LEFT JOIN upi_deposits d ON d.user_id = u.id
    GROUP BY u.id
    ORDER BY u.created_at DESC
"""

FINANCIAL_SUMMARY = """
    SELECT
    (SELECT COUNT(*) FROM users) AS users_count,
    (SELECT COALESCE(SUM(balance), 0) FROM users) AS total_user_balance,
    (SELECT COUNT(*) FROM upi_deposits WHERE status = 'pending') AS pending_deposits,
    (SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE status = 'approved')
        AS approved_deposit_total,
    (SELECT COUNT(*) FROM upi_withdrawals WHERE status = 'pending') AS pending_withdrawals,
    (SELECT COALESCE(SUM(amount), 0) FROM upi_withdrawals WHERE status = 'paid')
        AS paid_withdrawal_total
"""


def _round_metrics(cursor) -> dict:
    active_bets = [
        dict(r)
        for r in cursor.execute(
            "SELECT * FROM bets WHERE period = ?", (python_engine.current_period,)
        ).fetchall()
    ]

    def stake_by(pick: str) -> float:
        return sum(float(b["total_stake"]) for b in active_bets if b["selection"] == pick)

    green_stake, red_stake, violet_stake = stake_by("green"), stake_by("red"), stake_by("violet")
    return {
        "total_active_stake": green_stake + red_stake + violet_stake,
        "active_bets_count": len(active_bets),
        "green_stake": green_stake,
        "red_stake": red_stake,
        "violet_stake": violet_stake,
    }


# ----------------- ADMIN SESSION -----------------
@router.post("/login")
def admin_login(req: AdminLoginRequest):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE phone = ?", (req.phone,)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    user_dict = dict(user)
    if not user_dict.get("is_admin"):
        raise HTTPException(status_code=403, detail="This account is not an admin")
    if not auth_helpers.verify_password(req.password, user_dict["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    token = auth_helpers.create_access_token(
        {"user_id": user_dict["id"], "phone": user_dict["phone"], "is_admin": True},
        expires_delta=timedelta(days=ADMIN_SESSION_DAYS),
    )
    return {
        "status": "success",
        "token": token,
        "admin": {
            "id": user_dict["id"],
            "name": user_dict["username"],
            "phone": user_dict["phone"],
        },
    }


@router.post("/grant-admin")
def grant_admin(req: GrantAdminRequest, _: bool = Depends(require_admin)):
    """Promote an existing account to admin. Authenticated by the access key."""
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE phone = ?", (req.phone,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="No account with that phone number")
    conn.execute(
        "UPDATE users SET is_admin = ? WHERE phone = ?",
        (1 if req.is_admin else 0, req.phone),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "phone": req.phone, "is_admin": req.is_admin}


@router.post("/rotate-access-key")
def rotate_admin_access_key(req: AdminKeyRotationRequest, _: bool = Depends(require_admin)):
    """One-time authenticated rotation without storing the key in plaintext."""
    if len(req.api_key.strip()) < 24:
        raise HTTPException(status_code=400, detail="Admin access key must be at least 24 characters")
    key_hash = hashlib.sha256(req.api_key.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    try:
        set_setting(conn, "admin_api_key_hash", key_hash)
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "message": "Admin access key rotated"}


# ----------------- DASHBOARD -----------------
@router.get("/dashboard")
def get_admin_dashboard(_: bool = Depends(require_admin)):
    """Everything the dashboard renders, in one round trip.

    The panel used to open with five parallel calls on every refresh. On a
    single-worker host that queued behind itself and made the UI feel slow.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    settings = get_settings(conn)
    metrics = _round_metrics(cursor)
    financial = dict(cursor.execute(FINANCIAL_SUMMARY).fetchone())

    users = [dict(u) for u in cursor.execute(USERS_WITH_DEPOSIT_TOTALS).fetchall()]
    deposits = [
        dict(r)
        for r in cursor.execute("SELECT * FROM upi_deposits ORDER BY timestamp DESC LIMIT 300").fetchall()
    ]
    withdrawals = [
        dict(r)
        for r in cursor.execute(
            "SELECT * FROM upi_withdrawals ORDER BY timestamp DESC LIMIT 300"
        ).fetchall()
    ]
    qr_codes = [
        dict(q) for q in cursor.execute("SELECT * FROM qr_codes ORDER BY created_at DESC").fetchall()
    ]
    conn.close()

    return {
        "metrics": {
            "prediction_mode": settings.get("prediction_mode", "auto_least"),
            **metrics,
            **financial,
        },
        "platform_settings": {
            "deposits_enabled": str(settings.get("deposits_enabled", "true")).lower() == "true",
            "withdrawals_enabled": str(settings.get("withdrawals_enabled", "true")).lower() == "true",
            "withdrawal_min": float(settings.get("withdrawal_min", 200)),
        },
        "users": users,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "qr_codes": qr_codes,
        "game_access_min_deposit": config.GAME_ACCESS_MIN_DEPOSIT,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
def get_admin_metrics(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = get_settings(conn, ["prediction_mode"])
    metrics = _round_metrics(cursor)
    financial = dict(cursor.execute(FINANCIAL_SUMMARY).fetchone())
    conn.close()
    return {
        "prediction_mode": settings.get("prediction_mode", "auto_least"),
        **metrics,
        **financial,
    }


# ----------------- PLATFORM SETTINGS -----------------
@router.get("/platform-settings")
def get_admin_platform_settings(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    result = get_wallet_settings(conn)
    conn.close()
    return result


@router.put("/platform-settings")
def update_admin_platform_settings(req: PlatformSettingsReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    values = {
        "deposits_enabled": "true" if req.deposits_enabled else "false",
        "withdrawals_enabled": "true" if req.withdrawals_enabled else "false",
        "withdrawal_min": str(round(req.withdrawal_min, 2)),
    }
    for key, value in values.items():
        set_setting(conn, key, value)
    conn.commit()
    conn.close()
    return {"status": "success", **values}


@router.post("/prediction-mode")
def set_prediction_mode(req: PredictionModeReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    set_setting(conn, "prediction_mode", req.mode)
    conn.commit()
    conn.close()
    return {"status": "success", "mode": req.mode}


@router.post("/force-result")
def force_result(req: ForceResultReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    set_setting(conn, "prediction_mode", "manual")
    set_setting(conn, "forced_number", str(req.number))
    conn.commit()
    conn.close()
    return {"status": "success", "forced_number": req.number}


# ----------------- USERS -----------------
@router.get("/users")
def get_all_users(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    users = conn.execute(USERS_WITH_DEPOSIT_TOTALS).fetchall()
    conn.close()
    return {"users": [dict(u) for u in users]}


@router.put("/users/{user_id}/game-access")
def update_user_game_access(
    user_id: str, req: UserGameAccessReq, _: bool = Depends(require_admin)
):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    approved_total = get_approved_deposit_total(conn, user_id)
    if req.enabled and approved_total < config.GAME_ACCESS_MIN_DEPOSIT:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Minimum ₹{config.GAME_ACCESS_MIN_DEPOSIT:.0f} approved recharge is required "
                f"before enabling game access. This user has ₹{approved_total:.2f} approved."
            ),
        )
    conn.execute(
        "UPDATE users SET game_access_enabled = ? WHERE id = ?",
        (1 if req.enabled else 0, user_id),
    )
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "user_id": user_id,
        "game_access_enabled": req.enabled,
        "approved_deposit_total": approved_total,
    }


@router.put("/users/{user_id}/status")
def update_user_status(user_id: str, req: UserStatusReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (req.status, user_id))
    conn.commit()
    conn.close()
    return {"status": "success", "user_id": user_id, "new_status": req.status}


@router.delete("/users/{user_id}")
def delete_user_account(user_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    pending = conn.execute(
        "SELECT COUNT(*) FROM upi_withdrawals WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    ).fetchone()[0]
    if pending:
        conn.close()
        # Deleting now would strand the reserved balance: the refund path in
        # reject_withdrawal credits a user row that no longer exists.
        raise HTTPException(
            status_code=400,
            detail="Settle this user's pending withdrawals before deleting the account",
        )
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "deleted_user_id": user_id}


# ----------------- DEPOSITS -----------------
@router.get("/deposits")
def get_admin_deposits(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM upi_deposits ORDER BY timestamp DESC").fetchall()
    conn.close()
    return {"deposits": [dict(r) for r in rows]}


@router.post("/deposits/{dep_id}/approve")
def approve_deposit(dep_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    dep = conn.execute(
        "SELECT * FROM upi_deposits WHERE id = ? FOR UPDATE", (dep_id,)
    ).fetchone()
    if not dep or dep["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Deposit not found or already processed")

    conn.execute(
        "UPDATE upi_deposits SET status = 'approved', processed_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc), dep_id),
    )
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE id = ?", (dep["amount"], dep["user_id"])
    )
    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}


@router.post("/deposits/{dep_id}/reject")
def reject_deposit(dep_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    dep = conn.execute(
        "SELECT status FROM upi_deposits WHERE id = ? FOR UPDATE", (dep_id,)
    ).fetchone()
    if not dep or dep["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Deposit not found or already processed")
    conn.execute(
        "UPDATE upi_deposits SET status = 'rejected', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc), dep_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}


# ----------------- WITHDRAWALS -----------------
@router.get("/withdrawals")
def get_admin_withdrawals(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM upi_withdrawals ORDER BY timestamp DESC").fetchall()
    conn.close()
    return {"withdrawals": [dict(r) for r in rows]}


@router.post("/withdrawals/{wth_id}/approve")
def approve_withdrawal(wth_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    wth = conn.execute(
        "SELECT status FROM upi_withdrawals WHERE id = ? FOR UPDATE", (wth_id,)
    ).fetchone()
    if not wth or wth["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Withdrawal not found or already processed")
    conn.execute(
        "UPDATE upi_withdrawals SET status = 'paid', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc), wth_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}


@router.post("/withdrawals/{wth_id}/reject")
def reject_withdrawal(wth_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    wth = conn.execute(
        "SELECT * FROM upi_withdrawals WHERE id = ? FOR UPDATE", (wth_id,)
    ).fetchone()
    if not wth or wth["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Withdrawal not found or already processed")

    conn.execute(
        "UPDATE upi_withdrawals SET status = 'rejected', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc), wth_id),
    )
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE id = ?", (wth["amount"], wth["user_id"])
    )
    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}


# ----------------- QR CODES -----------------
@router.get("/qr-codes")
def get_admin_qr_codes(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    qrs = conn.execute("SELECT * FROM qr_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"qr_codes": [dict(q) for q in qrs]}


@router.post("/qr-codes")
def add_admin_qr_code(req: AddQRReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    qr_id = f"QR-{uuid.uuid4().hex[:8].upper()}"
    conn.execute(
        """INSERT INTO qr_codes
        (id, name, note, qr_url, upi_id, min_amount, max_amount, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
        (qr_id, req.name, req.note, req.qr_url, req.upi_id, req.min_amount, req.max_amount),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "qr_id": qr_id}


@router.post("/qr-codes/upload")
async def upload_admin_qr_code(
    name: str = Form(...),
    note: str = Form("Scan with any UPI app"),
    upi_id: str = Form(""),
    min_amount: float = Form(100),
    max_amount: float = Form(50000),
    qr_file: UploadFile = File(...),
    _: bool = Depends(require_admin),
):
    if min_amount < 1 or max_amount < min_amount:
        raise HTTPException(status_code=400, detail="Invalid deposit amount limits")

    extension = config.QR_UPLOAD_TYPES.get(qr_file.content_type or "")
    if not extension:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG or WEBP QR image")

    content = await qr_file.read()
    if not content or len(content) > config.QR_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="QR image must be smaller than 5 MB")

    # Stored as bytes in Postgres rather than written to disk: the host's
    # filesystem does not survive a deploy, and a missing QR means players
    # cannot pay at all. See the qr_codes columns in database.py.
    conn = get_db_connection()
    qr_id = f"QR-{uuid.uuid4().hex[:8].upper()}"
    qr_url = config.qr_public_url(qr_id)
    conn.execute(
        """INSERT INTO qr_codes
        (id, name, note, qr_url, upi_id, min_amount, max_amount, is_active,
         image_data, image_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            qr_id,
            name.strip(),
            note.strip(),
            qr_url,
            upi_id.strip(),
            min_amount,
            max_amount,
            content,
            qr_file.content_type,
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "qr_id": qr_id, "qr_url": qr_url}


@router.post("/qr-codes/{qr_id}/activate")
def activate_admin_qr_code(qr_id: str, enabled: bool = True, _: bool = Depends(require_admin)):
    """Toggle a QR in or out of the rotation pool.

    Several QRs can be live at once; each new deposit order is handed the
    least-recently-used one.
    """
    conn = get_db_connection()
    qr = conn.execute("SELECT id FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(status_code=404, detail="QR code not found")
    conn.execute("UPDATE qr_codes SET is_active = ? WHERE id = ?", (1 if enabled else 0, qr_id))
    remaining = conn.execute("SELECT COUNT(*) FROM qr_codes WHERE is_active = 1").fetchone()[0]
    conn.commit()
    conn.close()
    return {"status": "success", "qr_id": qr_id, "enabled": enabled, "pool_size": remaining}


@router.delete("/qr-codes/{qr_id}")
def delete_admin_qr_code(qr_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    qr = conn.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(status_code=404, detail="QR code not found")
    conn.execute("DELETE FROM qr_codes WHERE id = ?", (qr_id,))
    if qr["is_active"]:
        replacement = conn.execute(
            "SELECT id FROM qr_codes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if replacement:
            conn.execute("UPDATE qr_codes SET is_active = 1 WHERE id = ?", (replacement["id"],))
    conn.commit()
    conn.close()

    # Uploads are stored absolute when PUBLIC_API_URL is set and relative when
    # it is not, so match the path segment rather than the start of the string.
    # Matching only the relative form left every file on disk in production.
    qr_url = qr["qr_url"] or ""
    if "/uploads/qr/" in qr_url:
        filename = os.path.basename(qr_url)
        file_path = os.path.abspath(os.path.join(config.QR_UPLOAD_DIR, filename))
        if (
            file_path.startswith(os.path.abspath(config.QR_UPLOAD_DIR) + os.sep)
            and os.path.exists(file_path)
        ):
            os.remove(file_path)
    return {"status": "success", "deleted_qr_id": qr_id}
