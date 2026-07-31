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


def get_wallet_settings(conn) -> dict:
    return {
        "deposits_enabled": get_bool_setting(conn, "deposits_enabled", True),
        "withdrawals_enabled": get_bool_setting(conn, "withdrawals_enabled", True),
        "withdrawal_min": float(get_setting(conn, "withdrawal_min", "200")),
    }


def get_approved_deposit_total(conn, user_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM upi_deposits WHERE user_id = ? AND status = 'approved'",
        (user_id,),
    ).fetchone()
    return float(row[0] or 0)
