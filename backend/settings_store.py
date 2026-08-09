"""Reads and writes for the ``system_settings`` key/value table.

Routes used to inline the same SELECT and the same
``INSERT ... ON CONFLICT DO UPDATE`` in five places, which is how
``prediction_mode`` ended up updatable only when the row already existed.
"""

from typing import Dict, Iterable, Optional


def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def get_settings(conn, keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
    if keys is None:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
    else:
        keys = list(keys)
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT key, value FROM system_settings WHERE key IN ({placeholders})", keys
        ).fetchall()
    return {row["key"]: str(row["value"]) for row in rows}


def set_setting(conn, key: str, value: str) -> None:
    """Upsert, so a key missing from the seed still takes the new value."""
    conn.execute(
        "INSERT INTO system_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_bool_setting(conn, key: str, default: bool) -> bool:
    return get_setting(conn, key, "true" if default else "false").lower() == "true"


# Shown to a player who tries to withdraw before recharging. Kept as a setting
# rather than a constant because the wording is the operator's to choose, and
# it is the only explanation the player gets.
WITHDRAWAL_LOCKED_MESSAGE = (
    "Recharge at least ₹{minimum} to unlock withdrawals. "
    "Your signup bonus and anything won with it can be withdrawn once your "
    "first recharge is approved."
)


def get_wallet_settings(conn) -> dict:
    return {
        "deposits_enabled": get_bool_setting(conn, "deposits_enabled", True),
        "withdrawals_enabled": get_bool_setting(conn, "withdrawals_enabled", True),
        # The deposit range used to live only on each QR row, and the dashboard
        # only set it while uploading one -- so there was no way to change the
        # minimum afterwards short of deleting the QR and adding it again.
        # These are the platform-wide bounds; a QR may narrow them further.
        "deposit_min": float(get_setting(conn, "deposit_min", "100")),
        "deposit_max": float(get_setting(conn, "deposit_max", "50000")),
        "withdrawal_min": float(get_setting(conn, "withdrawal_min", "200")),
        "withdrawal_max": float(get_setting(conn, "withdrawal_max", "100000")),
        # Approved deposits an account needs before it may withdraw anything.
        # 0 turns the requirement off.
        "withdrawal_min_deposit": float(get_setting(conn, "withdrawal_min_deposit", "500")),
        "withdrawal_locked_message": get_setting(
            conn, "withdrawal_locked_message", WITHDRAWAL_LOCKED_MESSAGE
        ),
    }


def withdrawal_lock(conn, user_id: str) -> Optional[str]:
    """Why this account may not withdraw yet, or None if it may.

    The wording comes from the dashboard, so `{minimum}` and `{deposited}` are
    filled in here rather than baked into the text an operator has to retype
    every time they change the threshold. A message that uses neither is left
    exactly as it was typed.
    """
    minimum = float(get_setting(conn, "withdrawal_min_deposit", "500"))
    if minimum <= 0:
        return None
    deposited = get_approved_deposit_total(conn, user_id)
    if deposited >= minimum:
        return None

    message = get_setting(conn, "withdrawal_locked_message", WITHDRAWAL_LOCKED_MESSAGE)
    try:
        return message.format(minimum=f"{minimum:.0f}", deposited=f"{deposited:.0f}")
    except (KeyError, IndexError, ValueError):
        # An operator's stray brace must not turn into a 500 on the one route
        # that is meant to explain itself.
        return message


def get_approved_deposit_total(conn, user_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE user_id = ? AND status = 'approved'",
        (user_id,),
    ).fetchone()
    return float(row[0] or 0)
