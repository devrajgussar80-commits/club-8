"""FastAPI dependencies for player and admin authentication."""

import hashlib
import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException

import auth
import config
from database import get_db_connection


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        if config.ALLOW_DEMO_USER:
            conn = get_db_connection(readonly=True)
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (config.DEMO_USER_ID,)
            ).fetchone()
            conn.close()
            if user:
                return dict(user)
        raise HTTPException(status_code=401, detail="Please log in to continue")

    token = authorization.split(" ", 1)[1]
    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Every authenticated request runs this, including the once-a-second game
    # state polls, and it only ever reads.
    conn = get_db_connection(readonly=True)
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
    row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (payload.get("user_id"),)).fetchone()
    conn.close()
    return bool(row and row["is_admin"])


def require_admin(
    x_admin_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """Accepts either an admin session token or the shared access key.

    The token path is what the dashboard uses; the key stays supported so
    existing tooling and the recovery path keep working.
    """
    if _admin_token_is_valid(authorization):
        return True

    if not config.ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin API is not configured on this server")

    supplied_key = x_admin_key or ""
    rotated_hash = _rotated_admin_key_hash()
    supplied_hash = hashlib.sha256(supplied_key.encode("utf-8")).hexdigest()
    if rotated_hash:
        if not hmac.compare_digest(supplied_hash, rotated_hash):
            raise HTTPException(status_code=401, detail="Invalid admin access key")
        return True
    if not hmac.compare_digest(supplied_key, config.ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid admin access key")
    return True


def get_admin_user(authorization: Optional[str] = Header(None)) -> dict:
    """The signed-in admin's account, for actions that change that account.

    Unlike require_admin this refuses the shared key: rotating the admin's own
    phone or password has to be tied to a real logged-in admin, not to anyone
    holding the key.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin session required")
    payload = auth.decode_access_token(authorization.split(" ", 1)[1])
    if not payload or not payload.get("is_admin"):
        raise HTTPException(status_code=401, detail="Admin session required")

    conn = get_db_connection(readonly=True)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (payload.get("user_id"),)).fetchone()
    conn.close()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="This account is not an admin")
    return dict(user)


def get_employee_user(authorization: Optional[str] = Header(None)) -> dict:
    """The signed-in staff account, for every /api/employee route.

    Deliberately re-reads `is_employee` from the row rather than trusting the
    claim in the token: revoking someone's portal access has to take effect on
    their next request, not whenever their week-long token happens to expire.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Please sign in to continue")
    payload = auth.decode_access_token(authorization.split(" ", 1)[1])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    conn = get_db_connection(readonly=True)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (payload.get("user_id"),)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    row = dict(user)
    if not row.get("is_employee"):
        raise HTTPException(status_code=403, detail="This account is not an employee account")
    if row.get("status") == "disabled":
        raise HTTPException(status_code=403, detail="Your account has been suspended by Admin.")
    return row


AdminAuth = Depends(require_admin)
CurrentUser = Depends(get_current_user)
EmployeeUser = Depends(get_employee_user)
