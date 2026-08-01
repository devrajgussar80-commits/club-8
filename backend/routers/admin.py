"""Admin dashboard, moderation queues and payment configuration.

Every route here is behind `require_admin`, which accepts either an admin
session token or the `X-Admin-Key` shared key.
"""

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import auth as auth_helpers
import config
import referrals_core
from database import get_db_connection
from deps import get_admin_user, require_admin
from game_engine import python_engine
from schemas import (
    AddQRReq,
    AdminCredentialsRequest,
    AdminKeyRotationRequest,
    AdminLoginRequest,
    LocalPushRequest,
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


@router.post("/security/credentials")
def change_admin_credentials(
    req: AdminCredentialsRequest, admin: dict = Depends(get_admin_user)
):
    """Change the signed-in admin's own login phone and/or password.

    The current password is required regardless of the session token, so a
    stolen token alone cannot rotate the credentials and lock the admin out.
    """
    if not auth_helpers.verify_password(req.current_password, admin["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if not req.new_phone and not req.new_password:
        raise HTTPException(status_code=400, detail="Nothing to change")

    conn = get_db_connection()
    try:
        if req.new_phone and req.new_phone != admin["phone"]:
            clash = conn.execute(
                "SELECT id FROM users WHERE phone = ? AND id <> ?",
                (req.new_phone, admin["id"]),
            ).fetchone()
            if clash:
                raise HTTPException(status_code=400, detail="That phone is already in use")
            conn.execute(
                "UPDATE users SET phone = ? WHERE id = ?", (req.new_phone, admin["id"])
            )
        if req.new_password:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (auth_helpers.hash_password(req.new_password), admin["id"]),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {
        "status": "success",
        "phone": req.new_phone or admin["phone"],
        "password_changed": bool(req.new_password),
    }


# ----------------- DEPLOY -----------------
# No deploy-hook plumbing here on purpose: a push to GitHub already triggers a
# Vercel + Render redeploy, so the only button that adds anything is the local
# commit+push below.


def _git(args, cwd):
    """Run a git command in the repo, no shell (so the commit message is one
    safe argument, never interpreted)."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180
    )


@router.get("/deploy/local-available")
def local_deploy_available(_: bool = Depends(require_admin)):
    """Whether the one-click local push can run here. False on the deployed
    host, so the dashboard only shows the button during local development."""
    return {"available": not config.IS_PRODUCTION}


@router.post("/deploy/local-push")
def local_push(req: LocalPushRequest, _: bool = Depends(require_admin)):
    """Commit and push the working tree from the LOCAL dev machine.

    Only runs when this backend is the local dev server: the deployed host has
    no git repo or push credentials, and must never try. Pushing to GitHub is
    what makes Vercel and Render redeploy, so this one call is the whole
    'commit + push + redeploy' the button promises.
    """
    if config.IS_PRODUCTION:
        raise HTTPException(
            status_code=403,
            detail="This runs only on your local machine, not on the live server.",
        )

    repo = config.FRONTEND_DIR  # project root; the backend lives one level in
    if not os.path.isdir(os.path.join(repo, ".git")):
        raise HTTPException(status_code=400, detail="This project folder is not a git repository.")

    message = req.message.strip() or f"Site update {datetime.now():%Y-%m-%d_%H:%M}"

    _git(["add", "-A"], repo)

    # Nothing staged -> nothing to deploy.
    if _git(["diff", "--cached", "--quiet"], repo).returncode == 0:
        return {"status": "noop", "detail": "No changes to deploy — already up to date."}

    commit = _git(
        [
            "-c", "user.email=devrajgussar80@gmail.com",
            "-c", "user.name=devrajgussar80-commits",
            "commit", "-m", message,
        ],
        repo,
    )
    if commit.returncode != 0:
        raise HTTPException(status_code=500, detail=f"commit failed: {commit.stderr[:300]}")

    push = _git(["push"], repo)
    if push.returncode != 0:
        detail = push.stderr[:400] or "git push failed"
        if "could not read Username" in push.stderr or "Authentication" in push.stderr:
            detail = "Push failed — run 'gh auth login' once in a terminal, then retry."
        raise HTTPException(status_code=500, detail=detail)

    return {
        "status": "success",
        "message": message,
        "detail": "Pushed to GitHub. Vercel and Render will redeploy automatically.",
    }


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
    # First approved deposit is what turns a signup into a claimable referral.
    # Same transaction as the credit, so the two can never disagree.
    referrals_core.qualify_on_deposit(conn, dep["user_id"])
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


# ----------------- REFERRALS -----------------
@router.get("/referrals")
def list_referrals(_: bool = Depends(require_admin)):
    """Every referral, so the admin can trace who invited whom and whether the
    invited player has deposited yet."""
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT r.id, r.status, r.reward, r.created_at, r.qualified_at, r.approved_at,
               r.referred_name, r.referred_phone,
               ref.username AS referrer_name, ref.phone AS referrer_phone,
               ref.referral_code AS referrer_code,
               COALESCE(dep.total, 0) AS referred_deposit_total
          FROM referrals r
          JOIN users ref ON ref.id = r.referrer_id
          LEFT JOIN (
              SELECT user_id, SUM(amount) AS total
                FROM upi_deposits WHERE status = 'approved'
                GROUP BY user_id
          ) dep ON dep.user_id = r.referred_id
         ORDER BY r.created_at DESC
        """
    ).fetchall()
    conn.close()

    referrals = [dict(r) for r in rows]
    pending = sum(1 for r in referrals if r["status"] == "deposited")
    return {
        "referrals": referrals,
        "reward_per_referral": referrals_core.REWARD_AMOUNT,
        "pending_approval": pending,
    }


@router.post("/referrals/{referral_id}/approve")
def approve_referral(referral_id: str, _: bool = Depends(require_admin)):
    """Release the reward: credit the referrer once, close the referral.

    Only a 'deposited' referral can be approved -- the referred user must have
    a qualifying deposit first. The row is locked and re-checked so a double
    click cannot pay the reward twice.
    """
    conn = get_db_connection()
    try:
        ref = conn.execute(
            "SELECT * FROM referrals WHERE id = ? FOR UPDATE", (referral_id,)
        ).fetchone()
        if not ref:
            raise HTTPException(status_code=404, detail="Referral not found")
        if ref["status"] != "deposited":
            raise HTTPException(
                status_code=400,
                detail="Only referrals whose invited user has deposited can be approved.",
            )
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (ref["reward"], ref["referrer_id"]),
        )
        conn.execute(
            "UPDATE referrals SET status = 'approved', approved_at = ? WHERE id = ?",
            (datetime.now(timezone.utc), referral_id),
        )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {"status": "success", "referral_id": referral_id, "reward": ref["reward"]}


@router.post("/referrals/{referral_id}/reject")
def reject_referral(referral_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    try:
        ref = conn.execute(
            "SELECT status FROM referrals WHERE id = ? FOR UPDATE", (referral_id,)
        ).fetchone()
        if not ref:
            raise HTTPException(status_code=404, detail="Referral not found")
        if ref["status"] in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="This referral is already closed.")
        conn.execute(
            "UPDATE referrals SET status = 'rejected' WHERE id = ?", (referral_id,)
        )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {"status": "success", "referral_id": referral_id}


# ----------------- ANDROID APP -----------------
@router.post("/app/upload")
async def upload_app(
    version: str = Form(""),
    apk_file: UploadFile = File(...),
    _: bool = Depends(require_admin),
):
    """Replace the downloadable APK. Stored in Postgres so it survives deploys."""
    name = (apk_file.filename or "").lower()
    if not name.endswith(".apk"):
        raise HTTPException(status_code=400, detail="Upload a .apk file")

    content = await apk_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(content) > config.APK_UPLOAD_MAX_BYTES:
        limit_mb = config.APK_UPLOAD_MAX_BYTES // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"APK must be smaller than {limit_mb} MB")

    conn = get_db_connection()
    try:
        # One row, id = 'current'. Upsert so a new upload cleanly replaces the
        # old bytes instead of piling up versions in the table.
        conn.execute(
            """
            INSERT INTO app_downloads (id, filename, version, content_type, size_bytes, data, uploaded_at)
            VALUES ('current', ?, ?, ?, ?, ?, NOW())
            ON CONFLICT (id) DO UPDATE SET
                filename = EXCLUDED.filename,
                version = EXCLUDED.version,
                content_type = EXCLUDED.content_type,
                size_bytes = EXCLUDED.size_bytes,
                data = EXCLUDED.data,
                uploaded_at = NOW()
            """,
            (
                apk_file.filename,
                version.strip() or None,
                config.APK_CONTENT_TYPE,
                len(content),
                content,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "filename": apk_file.filename,
        "version": version.strip() or None,
        "size_bytes": len(content),
    }


@router.delete("/app")
def delete_app(_: bool = Depends(require_admin)):
    """Take the app down: removes the stored APK so the button hides itself."""
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM app_downloads WHERE id = 'current'")
        conn.commit()
    finally:
        conn.close()
    return {"status": "success"}


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
