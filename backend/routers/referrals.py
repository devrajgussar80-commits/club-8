"""What a player sees about their own referrals."""

from fastapi import APIRouter, Depends

from database import get_db_connection
from deps import get_current_user
from referrals_core import REWARD_AMOUNT

router = APIRouter(prefix="/api/referrals", tags=["referrals"])

# The status the referred user is at, in words the player understands.
_LABELS = {
    "signed_up": "Signed up",
    "deposited": "Deposited — reward pending",
    "approved": "Reward credited",
    "rejected": "Not eligible",
}


def _mask_phone(phone: str) -> str:
    if not phone:
        return "—"
    return phone[:3] + "****" + phone[-2:] if len(phone) > 5 else "****"


@router.get("/mine")
def my_referrals(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT referred_name, referred_phone, status, reward, created_at
              FROM referrals
             WHERE referrer_id = ?
             ORDER BY created_at DESC
            """,
            (current_user["id"],),
        ).fetchall()
    finally:
        conn.close()

    referrals = [
        {
            "name": r["referred_name"] or "Player",
            "phone": _mask_phone(r["referred_phone"]),
            "status": r["status"],
            "status_label": _LABELS.get(r["status"], r["status"]),
            "deposited": r["status"] in ("deposited", "approved"),
            "reward": float(r["reward"] or 0),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]

    earned = sum(r["reward"] for r in referrals if r["status"] == "approved")
    pending = sum(r["reward"] for r in referrals if r["status"] == "deposited")

    return {
        "referral_code": current_user.get("referral_code"),
        "reward_per_referral": REWARD_AMOUNT,
        "total_signups": len(referrals),
        "total_deposited": sum(1 for r in referrals if r["deposited"]),
        "earned": round(earned, 2),
        "pending": round(pending, 2),
        "referrals": referrals,
    }
