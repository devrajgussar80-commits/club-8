"""Player registration, login and profile."""

import uuid

from fastapi import APIRouter, Depends, HTTPException

import auth as auth_helpers
import config
import luck
import referrals_core
from database import get_db_connection
from deps import get_current_user
from schemas import LoginRequest, RegisterRequest
from settings_store import get_approved_deposit_total

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The wallet a new account opens with. Also the starting point of its bonus
# run (see backend/luck.py), which is why the dashboard can change it: both
# numbers have to move together or the run starts from the wrong place.
SIGNUP_BONUS = 100.0


@router.post("/register")
def register_user(req: RegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    existing = cursor.execute("SELECT id FROM users WHERE phone = ?", (req.phone,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Phone number is already registered!")

    # COUNT(*) reuses an id after any deletion, which would hand a new account
    # the previous holder's bets and deposits.
    user_id = f"USR{uuid.uuid4().hex[:10].upper()}"
    pwd_hash = auth_helpers.hash_password(req.password)

    own_code = referrals_core.new_user_code(conn)
    entered = (req.referral_code or "").strip().upper() or None

    # The bonus run starts here: the wallet opens on the bonus, and the target
    # it may climb to is drawn once, now, so it is fixed before the first
    # round rather than decided by anything that happens during one.
    luck_settings = luck.load_settings(conn)
    bonus = luck_settings["signup_bonus"] or SIGNUP_BONUS

    cursor.execute(
        """
        INSERT INTO users
            (id, phone, username, password_hash, balance, status,
             referral_code, referred_by, game_access_enabled,
             luck_target, luck_progress, luck_done)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 0, ?, ?, 0)
        """,
        (
            user_id, req.phone, req.username, pwd_hash, bonus, own_code, entered,
            luck.draw_target(luck_settings), bonus,
        ),
    )

    # Link this signup to the referrer, if the entered code is real. Part of
    # the same transaction, so a new account and its referral commit together.
    referrals_core.record_referral(conn, user_id, req.username, req.phone, entered)

    conn.commit()
    conn.close()

    token = auth_helpers.create_access_token({"user_id": user_id, "phone": req.phone})
    return {
        "status": "success",
        "token": token,
        "user": {
            "id": user_id,
            "name": req.username,
            "phone": req.phone,
            "balance": bonus,
            "game_access_enabled": False,
            "approved_deposit_total": 0.0,
            "referral_code": own_code,
        },
    }


@router.post("/login")
def login_user(req: LoginRequest):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE phone = ?", (req.phone,)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    user_dict = dict(user)
    if not auth_helpers.verify_password(req.password, user_dict["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    if user_dict["status"] == "disabled":
        raise HTTPException(status_code=403, detail="Account suspended by Admin!")

    # Transparently move legacy SHA-256 records onto PBKDF2 now that we have the
    # plaintext in hand.
    if auth_helpers.needs_rehash(user_dict["password_hash"]):
        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth_helpers.hash_password(req.password), user_dict["id"]),
        )
        conn.commit()
        conn.close()

    token = auth_helpers.create_access_token(
        {"user_id": user_dict["id"], "phone": user_dict["phone"]}
    )
    return {
        "status": "success",
        "token": token,
        "user": {
            "id": user_dict["id"],
            "name": user_dict["username"],
            "phone": user_dict["phone"],
            "balance": user_dict["balance"],
            "game_access_enabled": bool(user_dict.get("game_access_enabled", 0)),
            "referral_code": user_dict.get("referral_code"),
        },
    }


@router.get("/me")
def get_profile(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        approved_total = get_approved_deposit_total(conn, current_user["id"])
        run = luck.Run(current_user, luck.load_settings(conn))
    finally:
        conn.close()

    user_data = dict(current_user)
    user_data.pop("password_hash", None)
    # How the run is going is the server's business. Handing the player their
    # own target and progress would tell them exactly when the boost stops.
    for column in ("luck_target", "luck_progress", "luck_done", "team_win_rate"):
        user_data.pop(column, None)

    user_data["approved_deposit_total"] = approved_total
    user_data["has_approved_min_deposit"] = approved_total >= config.GAME_ACCESS_MIN_DEPOSIT
    user_data["game_access_enabled"] = bool(user_data.get("game_access_enabled", 0))
    user_data["game_access_min_deposit"] = config.GAME_ACCESS_MIN_DEPOSIT
    # The three ways into the arcade, resolved here rather than re-derived in
    # the browser: the admin switch, enough approved deposits, or a signup
    # bonus that has not finished its run. Whether the run is still open is
    # the one the client cannot work out for itself.
    user_data["bonus_run_active"] = run.open
    user_data["game_access_open"] = bool(
        user_data["game_access_enabled"]
        or user_data["has_approved_min_deposit"]
        or run.open
    )
    return {"status": "success", "user": user_data}
