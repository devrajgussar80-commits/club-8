"""FastAPI server entrypoint for the Club 8 REST API."""

import asyncio
import hashlib
import hmac
import io
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import qrcode
from fastapi import FastAPI, HTTPException, Header, Depends, Body, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import database
from database import get_db_connection
import auth
from game_engine import python_engine
import arcade_engine

BASE_DIR = os.path.dirname(__file__)
UPLOAD_ROOT = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
QR_UPLOAD_DIR = os.path.join(UPLOAD_ROOT, "qr")
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
APP_ENV = os.environ.get("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"
if IS_PRODUCTION and not ADMIN_API_KEY:
    raise RuntimeError("ADMIN_API_KEY is not set. Refusing to expose the admin API without a key.")
# The demo fallback hands out a real account to unauthenticated callers, so it
# can never be on in production regardless of how the env var is set.
ALLOW_DEMO_USER = (
    os.environ.get("ALLOW_DEMO_USER", "false").lower() == "true" and not IS_PRODUCTION
)
SERVE_FRONTEND = os.environ.get("SERVE_FRONTEND", "true").lower() == "true"
FRONTEND_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if origin.strip()
]
os.makedirs(QR_UPLOAD_DIR, exist_ok=True)

# A single approved-recharge threshold gates every game. Admin flips one switch
# per user and that unlocks WinGo, Aviator, Chicken Road and Mines together.
GAME_ACCESS_MIN_DEPOSIT = 300.0

# Initialize database
database.init_db()
arcade_engine.init_arcade_tables()


def get_approved_deposit_total(conn, user_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE user_id = ? AND status = 'approved'",
        (user_id,),
    ).fetchone()
    return float(row[0] or 0)

app = FastAPI(title="Club 8 API", version="3.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def disable_local_preview_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response

# Startup event
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(python_engine.start_loop())

# Auth Dependency
def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        if ALLOW_DEMO_USER:
            conn = get_db_connection()
            user = conn.execute("SELECT * FROM users WHERE id = 'USR9842'").fetchone()
            conn.close()
            return dict(user) if user else None
        raise HTTPException(status_code=401, detail="Please log in to continue")

    token = authorization.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (payload.get("user_id"),)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    user_dict = dict(user)
    if user_dict.get("status") == "disabled":
        raise HTTPException(status_code=403, detail="Your account has been suspended by Admin.")

    return user_dict

def _rotated_admin_key_hash() -> str:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'admin_api_key_hash'"
        ).fetchone()
        return str(row[0]) if row else ""
    finally:
        conn.close()


def _admin_token_is_valid(authorization: Optional[str]) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    payload = auth.decode_access_token(authorization.split(" ", 1)[1])
    if not payload or not payload.get("is_admin"):
        return False
    conn = get_db_connection()
    row = conn.execute(
        "SELECT is_admin FROM users WHERE id = ?", (payload.get("user_id"),)
    ).fetchone()
    conn.close()
    return bool(row and row["is_admin"])


def require_admin(
    x_admin_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Accepts either an admin session token or the shared access key.

    The token path is what the dashboard uses; the key stays supported so
    existing tooling and the recovery path keep working.
    """
    if _admin_token_is_valid(authorization):
        return True

    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin API is not configured on this server")
    supplied_key = x_admin_key or ""
    rotated_hash = _rotated_admin_key_hash()
    supplied_hash = hashlib.sha256(supplied_key.encode("utf-8")).hexdigest()
    if rotated_hash:
        if not hmac.compare_digest(supplied_hash, rotated_hash):
            raise HTTPException(status_code=401, detail="Invalid admin access key")
        return True
    if not hmac.compare_digest(supplied_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid admin access key")
    return True


class AdminLoginRequest(BaseModel):
    phone: str
    password: str


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE phone = ?", (req.phone,)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    user_dict = dict(user)
    if not user_dict.get("is_admin"):
        raise HTTPException(status_code=403, detail="This account is not an admin")
    if not auth.verify_password(req.password, user_dict["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    token = auth.create_access_token(
        {"user_id": user_dict["id"], "phone": user_dict["phone"], "is_admin": True},
        expires_delta=timedelta(days=30),
    )
    return {
        "status": "success",
        "token": token,
        "admin": {"id": user_dict["id"], "name": user_dict["username"], "phone": user_dict["phone"]},
    }


class GrantAdminRequest(BaseModel):
    phone: str
    is_admin: bool = True


@app.post("/api/admin/grant-admin")
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


class AdminKeyRotationRequest(BaseModel):
    api_key: str


@app.post("/api/admin/rotate-access-key")
def rotate_admin_access_key(
    req: AdminKeyRotationRequest,
    _: bool = Depends(require_admin),
):
    """One-time authenticated rotation without storing the key in plaintext."""
    if len(req.api_key.strip()) < 24:
        raise HTTPException(status_code=400, detail="Admin access key must be at least 24 characters")
    key_hash = hashlib.sha256(req.api_key.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('admin_api_key_hash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key_hash,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "message": "Admin access key rotated"}

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "club-8-python-api"}

# ----------------- HTML PAGE ROUTING -----------------
@app.get("/login", response_class=FileResponse)
def serve_login():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.get("/game", response_class=FileResponse)
def serve_game():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.get("/admin", response_class=FileResponse)
def serve_admin():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

# ----------------- AUTH ROUTER -----------------
class RegisterRequest(BaseModel):
    phone: str
    username: str
    password: str
    referral_code: Optional[str] = None

class LoginRequest(BaseModel):
    phone: str
    password: str

@app.post("/api/auth/register")
def register_user(req: RegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    existing = cursor.execute("SELECT * FROM users WHERE phone = ?", (req.phone,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Phone number is already registered!")

    # COUNT(*) reuses an id after any deletion, which would hand a new account
    # the previous holder's bets and deposits.
    user_id = f"USR{uuid.uuid4().hex[:10].upper()}"
    pwd_hash = auth.hash_password(req.password)

    cursor.execute("""
    INSERT INTO users (id, phone, username, password_hash, balance, status, referral_code, game_access_enabled)
    VALUES (?, ?, ?, ?, 100.0, 'active', ?, 0)
    """, (user_id, req.phone, req.username, pwd_hash, req.referral_code))

    conn.commit()
    conn.close()

    token = auth.create_access_token({"user_id": user_id, "phone": req.phone})
    return {"status": "success", "token": token, "user": {"id": user_id, "name": req.username, "phone": req.phone, "balance": 100.0, "game_access_enabled": False, "approved_deposit_total": 0.0}}

@app.post("/api/auth/login")
def login_user(req: LoginRequest):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE phone = ?", (req.phone,)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    user_dict = dict(user)
    if not auth.verify_password(req.password, user_dict["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    if user_dict["status"] == "disabled":
        raise HTTPException(status_code=403, detail="Account suspended by Admin!")

    # Transparently move legacy SHA-256 records onto PBKDF2 now that we have the
    # plaintext in hand.
    if auth.needs_rehash(user_dict["password_hash"]):
        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(req.password), user_dict["id"]),
        )
        conn.commit()
        conn.close()

    token = auth.create_access_token({"user_id": user_dict["id"], "phone": user_dict["phone"]})
    return {
        "status": "success",
        "token": token,
        "user": {
            "id": user_dict["id"],
            "name": user_dict["username"],
            "phone": user_dict["phone"],
            "balance": user_dict["balance"],
            "game_access_enabled": bool(user_dict.get("game_access_enabled", 0))
        }
    }

@app.get("/api/auth/me")
def get_profile(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    approved_total = get_approved_deposit_total(conn, current_user["id"])
    conn.close()

    user_data = dict(current_user)
    user_data["approved_deposit_total"] = approved_total
    user_data["has_approved_min_deposit"] = approved_total >= GAME_ACCESS_MIN_DEPOSIT
    user_data["game_access_enabled"] = bool(user_data.get("game_access_enabled", 0))
    user_data["game_access_min_deposit"] = GAME_ACCESS_MIN_DEPOSIT
    return {"status": "success", "user": user_data}

# ----------------- GAME ROUTER -----------------
class BetRequest(BaseModel):
    select_type: str # 'color' | 'number' | 'size'
    selection: str
    amount: float
    multiplier: int
    room: str = "parity"
    period: Optional[str] = None

@app.get("/api/game/status")
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
        "game_access_min_deposit": GAME_ACCESS_MIN_DEPOSIT,
        "has_approved_min_deposit": approved_total >= GAME_ACCESS_MIN_DEPOSIT
    }

@app.post("/api/game/bet")
def place_bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    # WinGo is deliberately open to every signed-in user. The admin access
    # switch gates only the premium arcade games.
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Bet amount must be greater than zero.")
    if req.multiplier not in (1, 5, 10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="Invalid bet multiplier.")
    if req.select_type not in ("color", "number", "size"):
        raise HTTPException(status_code=400, detail="Invalid bet type.")
    if req.select_type == "size" and req.selection not in ("Big", "Small"):
        raise HTTPException(status_code=400, detail="Invalid Big/Small selection.")
    if req.select_type == "color" and req.selection not in ("green", "red", "violet"):
        raise HTTPException(status_code=400, detail="Invalid color selection.")
    if req.select_type == "number":
        try:
            selected_number = int(req.selection)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid number selection.")
        if selected_number < 0 or selected_number > 9:
            raise HTTPException(status_code=400, detail="Number must be between 0 and 9.")

    try:
        room_status = python_engine.get_status(req.room)
    except KeyError:
        raise HTTPException(status_code=404, detail="Game room not found")

    if not python_engine.is_bet_open(req.room, req.period):
        raise HTTPException(
            status_code=400,
            detail="This round is closed. Please place the bet in the next round."
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    total_stake = round(req.amount * req.multiplier, 2)

    # Re-read the balance under a write lock. The snapshot on current_user is
    # stale, so checking it alone let concurrent bets overdraw the wallet.
    cursor.execute("BEGIN IMMEDIATE")
    latest = cursor.execute(
        "SELECT balance FROM users WHERE id = ?", (current_user["id"],)
    ).fetchone()
    if not latest or float(latest["balance"]) < total_stake:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient balance.")

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (total_stake, current_user["id"]))

    bet_id = f"BET-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute("""
    INSERT INTO bets (id, user_id, period, select_type, selection, amount, multiplier, total_stake, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (bet_id, current_user["id"], room_status["period"], req.select_type, req.selection, req.amount, req.multiplier, total_stake))

    conn.commit()
    conn.close()

    return {"status": "success", "bet_id": bet_id, "total_stake": total_stake}

@app.get("/api/game/history")
def get_game_history(room: str = "parity"):
    if room not in python_engine.rooms:
        raise HTTPException(status_code=404, detail="Game room not found")
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM rounds WHERE status = 'completed' AND room = ? ORDER BY period DESC LIMIT 500",
        (room,)
    ).fetchall()
    conn.close()
    return {"history": [dict(r) for r in rows]}

@app.get("/api/game/my-bets")
def get_my_bets(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM bets WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (current_user["id"],)).fetchall()
    conn.close()
    return {"bets": [dict(r) for r in rows]}

# ----------------- UPI WALLET ROUTER -----------------
@app.get("/api/wallet/active-qr")
def get_active_qr():
    return python_engine.get_active_qr_code()

def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default

class DepositRequest(BaseModel):
    amount: float
    utr: str
    qr_id: Optional[str] = None
    order_id: Optional[str] = None

@app.post("/api/wallet/deposit")
def submit_deposit(req: DepositRequest, current_user: dict = Depends(get_current_user)):
    amount = round(float(req.amount), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Enter a valid deposit amount")
    if not req.utr or len(req.utr.strip()) != 12 or not req.utr.strip().isdigit():
        raise HTTPException(status_code=400, detail="Please enter a valid 12-digit UTR number!")
    order_id = (req.order_id or "").strip().upper()
    if not re.fullmatch(r"ORD[A-Z0-9]{10,24}", order_id):
        raise HTTPException(status_code=400, detail="Invalid or expired deposit order")
    if not req.qr_id:
        raise HTTPException(status_code=400, detail="Active payment QR is required")

    conn = get_db_connection()
    cursor = conn.cursor()
    if get_setting(conn, "deposits_enabled", "true").lower() != "true":
        conn.close()
        raise HTTPException(status_code=503, detail="Deposits are temporarily paused by Admin")

    qr = cursor.execute(
        "SELECT * FROM qr_codes WHERE id = ? AND is_active = 1",
        (req.qr_id,),
    ).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(status_code=400, detail="This QR is no longer active. Create a new deposit order.")
    minimum = float(qr["min_amount"] or 100)
    maximum = float(qr["max_amount"] or 50000)
    if amount < minimum or amount > maximum:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deposit must be between ₹{minimum:.2f} and ₹{maximum:.2f}")

    existing = cursor.execute("SELECT * FROM upi_deposits WHERE utr = ?", (req.utr.strip(),)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="This UTR / Ref Number has already been submitted!")

    dep_id = f"DEP-{uuid.uuid4().hex[:10].upper()}"
    try:
        cursor.execute("""
        INSERT INTO upi_deposits (id, user_id, user_name, amount, utr, qr_id, order_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (dep_id, current_user["id"], current_user["username"], amount, req.utr.strip(), req.qr_id, order_id))
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail="This deposit order has already been submitted")

    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}

class WithdrawRequest(BaseModel):
    amount: float
    upi_id: str

@app.post("/api/wallet/withdraw")
def submit_withdraw(req: WithdrawRequest, current_user: dict = Depends(get_current_user)):
    amount = round(float(req.amount), 2)
    destination = (req.upi_id or "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    if get_setting(conn, "withdrawals_enabled", "true").lower() != "true":
        conn.close()
        raise HTTPException(status_code=503, detail="Withdrawals are temporarily paused by Admin")
    minimum = float(get_setting(conn, "withdrawal_min", "200"))
    if amount < minimum:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is ₹{minimum:.2f}")
    if not destination or len(destination) > 400:
        conn.close()
        raise HTTPException(status_code=400, detail="Add a valid bank account or UPI ID")

    # Reserve balance
    cursor.execute("BEGIN IMMEDIATE")
    latest_user = cursor.execute("SELECT balance FROM users WHERE id = ?", (current_user["id"],)).fetchone()
    if not latest_user or float(latest_user["balance"]) < amount:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient wallet balance!")
    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, current_user["id"]))

    wth_id = f"WTH-{uuid.uuid4().hex[:10].upper()}"
    cursor.execute("""
    INSERT INTO upi_withdrawals (id, user_id, user_name, amount, upi_id, status)
    VALUES (?, ?, ?, ?, ?, 'pending')
    """, (wth_id, current_user["id"], current_user["username"], amount, destination))

    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}

@app.get("/api/wallet/deposits")
def get_my_deposits(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, amount, utr, order_id, status, timestamp, processed_at FROM upi_deposits WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return {"deposits": [dict(row) for row in rows]}

@app.get("/api/wallet/withdrawals")
def get_my_withdrawals(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, amount, upi_id, status, timestamp, processed_at FROM upi_withdrawals WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return {"withdrawals": [dict(row) for row in rows]}

@app.get("/api/wallet/settings")
def get_wallet_settings():
    conn = get_db_connection()
    result = {
        "deposits_enabled": get_setting(conn, "deposits_enabled", "true").lower() == "true",
        "withdrawals_enabled": get_setting(conn, "withdrawals_enabled", "true").lower() == "true",
        "withdrawal_min": float(get_setting(conn, "withdrawal_min", "200")),
    }
    conn.close()
    return result

@app.get("/api/wallet/payment-qr")
def generate_payment_qr(amount: float, qr_id: str, order_id: str):
    amount = round(float(amount), 2)
    order_id = order_id.strip().upper()
    if not re.fullmatch(r"ORD[A-Z0-9]{10,24}", order_id):
        raise HTTPException(status_code=400, detail="Invalid deposit order")

    conn = get_db_connection()
    qr_record = conn.execute(
        "SELECT * FROM qr_codes WHERE id = ? AND is_active = 1",
        (qr_id,),
    ).fetchone()
    conn.close()
    if not qr_record or not (qr_record["upi_id"] or "").strip():
        raise HTTPException(status_code=404, detail="Active UPI QR not available")
    minimum = float(qr_record["min_amount"] or 100)
    maximum = float(qr_record["max_amount"] or 50000)
    if amount < minimum or amount > maximum:
        raise HTTPException(status_code=400, detail="Amount is outside active QR limits")

    payload = "upi://pay?" + urlencode({
        "pa": qr_record["upi_id"].strip(),
        "pn": qr_record["name"].strip(),
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tr": order_id,
        "tn": order_id,
    })
    generator = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    generator.add_data(payload)
    generator.make(fit=True)
    image = generator.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")

# ----------------- ARCADE: CHICKEN ROAD -----------------
class ChickenStartRequest(BaseModel):
    bet: float
    difficulty: str = "easy"
    client_seed: Optional[str] = None


class ChickenRoundRequest(BaseModel):
    round_id: str


@app.get("/api/arcade/chicken/config")
def chicken_config(current_user: dict = Depends(get_current_user)):
    return {
        "rtp": arcade_engine.RTP,
        "min_bet": arcade_engine.MIN_BET,
        "max_bet": arcade_engine.MAX_BET,
        "difficulties": {
            name: {
                "lanes": cfg["lanes"],
                "survival": cfg["survival"],
                "multipliers": arcade_engine.multiplier_table(name),
            }
            for name, cfg in arcade_engine.DIFFICULTIES.items()
        },
        "active_round": arcade_engine.active_round(current_user["id"]),
        "balance": current_user["balance"],
    }


def _chicken_response(payload, user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    payload["balance"] = float(row["balance"]) if row else 0.0
    return payload


@app.post("/api/arcade/chicken/start")
def chicken_start(req: ChickenStartRequest, current_user: dict = Depends(get_current_user)):
    if not bool(current_user.get("game_access_enabled", 0)):
        raise HTTPException(
            status_code=403,
            detail=f"Recharge ₹{GAME_ACCESS_MIN_DEPOSIT:.0f} and ask admin to unlock the arcade games.",
        )
    try:
        payload = arcade_engine.start_round(
            current_user["id"], req.difficulty, req.bet, req.client_seed
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _chicken_response(payload, current_user["id"])


@app.post("/api/arcade/chicken/step")
def chicken_step(req: ChickenRoundRequest, current_user: dict = Depends(get_current_user)):
    try:
        payload = arcade_engine.step_round(current_user["id"], req.round_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _chicken_response(payload, current_user["id"])


@app.post("/api/arcade/chicken/cashout")
def chicken_cashout(req: ChickenRoundRequest, current_user: dict = Depends(get_current_user)):
    try:
        payload = arcade_engine.cashout_round(current_user["id"], req.round_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _chicken_response(payload, current_user["id"])


# ----------------- ADMIN ROUTER -----------------
@app.get("/api/admin/dashboard")
def get_admin_dashboard(_: bool = Depends(require_admin)):
    """Everything the dashboard renders, in one round trip.

    The panel used to open with five parallel calls on every refresh. On a
    single-worker host that queued behind itself and made the UI feel slow.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    settings = {
        row["key"]: row["value"]
        for row in cursor.execute("SELECT key, value FROM system_settings").fetchall()
    }

    active_bets = [
        dict(r)
        for r in cursor.execute(
            "SELECT * FROM bets WHERE period = ?", (python_engine.current_period,)
        ).fetchall()
    ]
    stake_by = lambda pick: sum(
        float(b["total_stake"]) for b in active_bets if b["selection"] == pick
    )
    green_stake, red_stake, violet_stake = stake_by("green"), stake_by("red"), stake_by("violet")

    financial = dict(cursor.execute(
        """SELECT
        (SELECT COUNT(*) FROM users) AS users_count,
        (SELECT COALESCE(SUM(balance), 0) FROM users) AS total_user_balance,
        (SELECT COUNT(*) FROM upi_deposits WHERE status = 'pending') AS pending_deposits,
        (SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE status = 'approved') AS approved_deposit_total,
        (SELECT COUNT(*) FROM upi_withdrawals WHERE status = 'pending') AS pending_withdrawals,
        (SELECT COALESCE(SUM(amount), 0) FROM upi_withdrawals WHERE status = 'paid') AS paid_withdrawal_total"""
    ).fetchone())

    users = [dict(u) for u in cursor.execute(
        """SELECT u.id, u.phone, u.username, u.balance, u.status, u.created_at,
                  u.referral_code, u.game_access_enabled,
                  COALESCE(SUM(CASE WHEN d.status = 'approved' THEN d.amount ELSE 0 END), 0) AS approved_deposit_total
           FROM users u LEFT JOIN upi_deposits d ON d.user_id = u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    ).fetchall()]

    deposits = [dict(r) for r in cursor.execute(
        "SELECT * FROM upi_deposits ORDER BY timestamp DESC LIMIT 300"
    ).fetchall()]
    withdrawals = [dict(r) for r in cursor.execute(
        "SELECT * FROM upi_withdrawals ORDER BY timestamp DESC LIMIT 300"
    ).fetchall()]
    qr_codes = [dict(q) for q in cursor.execute(
        "SELECT * FROM qr_codes ORDER BY created_at DESC"
    ).fetchall()]
    conn.close()

    return {
        "metrics": {
            "prediction_mode": settings.get("prediction_mode", "auto_least"),
            "total_active_stake": green_stake + red_stake + violet_stake,
            "active_bets_count": len(active_bets),
            "green_stake": green_stake,
            "red_stake": red_stake,
            "violet_stake": violet_stake,
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
        "game_access_min_deposit": GAME_ACCESS_MIN_DEPOSIT,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/admin/metrics")
def get_admin_metrics(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT value FROM system_settings WHERE key = 'prediction_mode'")
    mode = cursor.fetchone()["value"]

    cursor.execute("SELECT * FROM bets WHERE period = ?", (python_engine.current_period,))
    active_bets = [dict(r) for r in cursor.fetchall()]

    green_stake = sum(float(b["total_stake"]) for b in active_bets if b["selection"] == "green")
    red_stake = sum(float(b["total_stake"]) for b in active_bets if b["selection"] == "red")
    violet_stake = sum(float(b["total_stake"]) for b in active_bets if b["selection"] == "violet")
    total_stake = green_stake + red_stake + violet_stake

    financial = cursor.execute(
        """SELECT
        (SELECT COUNT(*) FROM users) AS users_count,
        (SELECT COALESCE(SUM(balance), 0) FROM users) AS total_user_balance,
        (SELECT COUNT(*) FROM upi_deposits WHERE status = 'pending') AS pending_deposits,
        (SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE status = 'approved') AS approved_deposit_total,
        (SELECT COUNT(*) FROM upi_withdrawals WHERE status = 'pending') AS pending_withdrawals,
        (SELECT COALESCE(SUM(amount), 0) FROM upi_withdrawals WHERE status = 'paid') AS paid_withdrawal_total"""
    ).fetchone()
    conn.close()
    return {
        "prediction_mode": mode,
        "total_active_stake": total_stake,
        "active_bets_count": len(active_bets),
        "green_stake": green_stake,
        "red_stake": red_stake,
        "violet_stake": violet_stake,
        **dict(financial),
    }

class PlatformSettingsReq(BaseModel):
    deposits_enabled: bool
    withdrawals_enabled: bool
    withdrawal_min: float

@app.get("/api/admin/platform-settings")
def get_admin_platform_settings(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    result = {
        "deposits_enabled": get_setting(conn, "deposits_enabled", "true").lower() == "true",
        "withdrawals_enabled": get_setting(conn, "withdrawals_enabled", "true").lower() == "true",
        "withdrawal_min": float(get_setting(conn, "withdrawal_min", "200")),
    }
    conn.close()
    return result

@app.put("/api/admin/platform-settings")
def update_admin_platform_settings(req: PlatformSettingsReq, _: bool = Depends(require_admin)):
    if req.withdrawal_min < 1 or req.withdrawal_min > 1000000:
        raise HTTPException(status_code=400, detail="Invalid minimum withdrawal amount")
    conn = get_db_connection()
    values = {
        "deposits_enabled": "true" if req.deposits_enabled else "false",
        "withdrawals_enabled": "true" if req.withdrawals_enabled else "false",
        "withdrawal_min": str(round(req.withdrawal_min, 2)),
    }
    for key, value in values.items():
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()
    conn.close()
    return {"status": "success", **values}

class PredictionModeReq(BaseModel):
    mode: str

@app.post("/api/admin/prediction-mode")
def set_prediction_mode(req: PredictionModeReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("UPDATE system_settings SET value = ? WHERE key = 'prediction_mode'", (req.mode,))
    conn.commit()
    conn.close()
    return {"status": "success", "mode": req.mode}

class ForceResultReq(BaseModel):
    number: int

@app.post("/api/admin/force-result")
def force_result(req: ForceResultReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("UPDATE system_settings SET value = 'manual' WHERE key = 'prediction_mode'")
    conn.execute("UPDATE system_settings SET value = ? WHERE key = 'forced_number'", (str(req.number),))
    conn.commit()
    conn.close()
    return {"status": "success", "forced_number": req.number}

@app.get("/api/admin/users")
def get_all_users(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    users = conn.execute("""
        SELECT u.id, u.phone, u.username, u.balance, u.status, u.created_at,
               u.referral_code, u.game_access_enabled,
               COALESCE(SUM(CASE WHEN d.status = 'approved' THEN d.amount ELSE 0 END), 0) AS approved_deposit_total
        FROM users u
        LEFT JOIN upi_deposits d ON d.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return {"users": [dict(u) for u in users]}

class UserStatusReq(BaseModel):
    status: str

class UserGameAccessReq(BaseModel):
    enabled: bool

@app.put("/api/admin/users/{user_id}/game-access")
def update_user_game_access(user_id: str, req: UserGameAccessReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    approved_total = get_approved_deposit_total(conn, user_id)
    if req.enabled and approved_total < GAME_ACCESS_MIN_DEPOSIT:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Minimum ₹{GAME_ACCESS_MIN_DEPOSIT:.0f} approved recharge is required before enabling game access. "
                f"This user has ₹{approved_total:.2f} approved."
            ),
        )
    conn.execute("UPDATE users SET game_access_enabled = ? WHERE id = ?", (1 if req.enabled else 0, user_id))
    conn.commit()
    conn.close()
    return {"status": "success", "user_id": user_id, "game_access_enabled": req.enabled, "approved_deposit_total": approved_total}

@app.put("/api/admin/users/{user_id}/status")
def update_user_status(user_id: str, req: UserStatusReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (req.status, user_id))
    conn.commit()
    conn.close()
    return {"status": "success", "user_id": user_id, "new_status": req.status}

@app.delete("/api/admin/users/{user_id}")
def delete_user_account(user_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "deleted_user_id": user_id}

@app.get("/api/admin/deposits")
def get_admin_deposits(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM upi_deposits ORDER BY timestamp DESC").fetchall()
    conn.close()
    return {"deposits": [dict(r) for r in rows]}

@app.post("/api/admin/deposits/{dep_id}/approve")
def approve_deposit(dep_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("BEGIN IMMEDIATE")
    dep = conn.execute("SELECT * FROM upi_deposits WHERE id = ?", (dep_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Deposit not found or already processed")

    conn.execute(
        "UPDATE upi_deposits SET status = 'approved', processed_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), dep_id),
    )
    conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (dep["amount"], dep["user_id"]))
    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}

@app.post("/api/admin/deposits/{dep_id}/reject")
def reject_deposit(dep_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("BEGIN IMMEDIATE")
    dep = conn.execute("SELECT status FROM upi_deposits WHERE id = ?", (dep_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Deposit not found or already processed")
    conn.execute(
        "UPDATE upi_deposits SET status = 'rejected', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), dep_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "deposit_id": dep_id}

@app.get("/api/admin/withdrawals")
def get_admin_withdrawals(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM upi_withdrawals ORDER BY timestamp DESC").fetchall()
    conn.close()
    return {"withdrawals": [dict(r) for r in rows]}

@app.post("/api/admin/withdrawals/{wth_id}/approve")
def approve_withdrawal(wth_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("BEGIN IMMEDIATE")
    wth = conn.execute("SELECT status FROM upi_withdrawals WHERE id = ?", (wth_id,)).fetchone()
    if not wth or wth["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Withdrawal not found or already processed")
    conn.execute(
        "UPDATE upi_withdrawals SET status = 'paid', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), wth_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}

@app.post("/api/admin/withdrawals/{wth_id}/reject")
def reject_withdrawal(wth_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    conn.execute("BEGIN IMMEDIATE")
    wth = conn.execute("SELECT * FROM upi_withdrawals WHERE id = ?", (wth_id,)).fetchone()
    if not wth or wth["status"] != "pending":
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Withdrawal not found or already processed")

    conn.execute(
        "UPDATE upi_withdrawals SET status = 'rejected', processed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), wth_id),
    )
    conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (wth["amount"], wth["user_id"]))
    conn.commit()
    conn.close()
    return {"status": "success", "withdrawal_id": wth_id}

@app.get("/api/admin/qr-codes")
def get_admin_qr_codes(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    qrs = conn.execute("SELECT * FROM qr_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"qr_codes": [dict(q) for q in qrs]}

class AddQRReq(BaseModel):
    name: str
    note: Optional[str] = "Scan with any UPI App"
    qr_url: str
    upi_id: Optional[str] = None
    min_amount: float = 100
    max_amount: float = 50000

@app.post("/api/admin/qr-codes")
def add_admin_qr_code(req: AddQRReq, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    qr_id = f"QR-{uuid.uuid4().hex[:8].upper()}"
    conn.execute("UPDATE qr_codes SET is_active = 0")
    conn.execute(
        """INSERT INTO qr_codes
        (id, name, note, qr_url, upi_id, min_amount, max_amount, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
        (qr_id, req.name, req.note, req.qr_url, req.upi_id, req.min_amount, req.max_amount),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "qr_id": qr_id}

@app.post("/api/admin/qr-codes/upload")
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

    allowed_types = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    extension = allowed_types.get(qr_file.content_type or "")
    if not extension:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG or WEBP QR image")

    content = await qr_file.read()
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="QR image must be smaller than 5 MB")

    filename = f"qr-{uuid.uuid4().hex}{extension}"
    file_path = os.path.join(QR_UPLOAD_DIR, filename)
    with open(file_path, "wb") as output:
        output.write(content)

    conn = get_db_connection()
    qr_id = f"QR-{uuid.uuid4().hex[:8].upper()}"
    conn.execute("UPDATE qr_codes SET is_active = 0")
    conn.execute(
        """INSERT INTO qr_codes
        (id, name, note, qr_url, upi_id, min_amount, max_amount, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
        (
            qr_id,
            name.strip(),
            note.strip(),
            f"{PUBLIC_API_URL}/uploads/qr/{filename}" if PUBLIC_API_URL else f"/uploads/qr/{filename}",
            upi_id.strip(),
            min_amount,
            max_amount,
        ),
    )
    conn.commit()
    conn.close()
    qr_url = f"{PUBLIC_API_URL}/uploads/qr/{filename}" if PUBLIC_API_URL else f"/uploads/qr/{filename}"
    return {"status": "success", "qr_id": qr_id, "qr_url": qr_url}

@app.post("/api/admin/qr-codes/{qr_id}/activate")
def activate_admin_qr_code(qr_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    qr = conn.execute("SELECT id FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(status_code=404, detail="QR code not found")
    conn.execute("UPDATE qr_codes SET is_active = 0")
    conn.execute("UPDATE qr_codes SET is_active = 1 WHERE id = ?", (qr_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "active_qr_id": qr_id}

@app.delete("/api/admin/qr-codes/{qr_id}")
def delete_admin_qr_code(qr_id: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    qr = conn.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        conn.close()
        raise HTTPException(status_code=404, detail="QR code not found")
    conn.execute("DELETE FROM qr_codes WHERE id = ?", (qr_id,))
    if qr["is_active"]:
        replacement = conn.execute("SELECT id FROM qr_codes ORDER BY created_at DESC LIMIT 1").fetchone()
        if replacement:
            conn.execute("UPDATE qr_codes SET is_active = 1 WHERE id = ?", (replacement["id"],))
    conn.commit()
    conn.close()

    qr_url = qr["qr_url"] or ""
    if qr_url.startswith("/uploads/qr/"):
        filename = os.path.basename(qr_url)
        file_path = os.path.abspath(os.path.join(QR_UPLOAD_DIR, filename))
        if file_path.startswith(os.path.abspath(QR_UPLOAD_DIR) + os.sep) and os.path.exists(file_path):
            os.remove(file_path)
    return {"status": "success", "deleted_qr_id": qr_id}

app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")

if SERVE_FRONTEND:
    app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=os.environ.get("APP_ENV", "development") != "production",
    )
