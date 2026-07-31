"""Chicken Road: server-authoritative rounds with a provably fair outcome.

Every lane is decided by HMAC-SHA256(server_seed, "client_seed:nonce:lane"), and
the hash of the server seed is published before the round starts. Once the round
ends the seed is revealed, so a player can replay every step and confirm nothing
was changed mid-round.

The multiplier curve is derived from the survival chance rather than hand-picked:

    payout(n) = RTP / p**n

so cashing out after any number of lanes returns exactly the same expected value,
`RTP`, on every difficulty. Picking the two independently — as the old
client-side version did — silently gave hardcore players a much worse edge.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timezone

from database import get_db_connection

RTP = 0.98

# `survival` is tuned so the final lane lands near the payout players expect from
# this game; `lanes` matches the published board length per difficulty.
DIFFICULTIES = {
    "easy": {"lanes": 24, "survival": 0.8752},
    "medium": {"lanes": 22, "survival": 0.7852},
    "hard": {"lanes": 20, "survival": 0.6930},
    "hardcore": {"lanes": 15, "survival": 0.5260},
}

MIN_BET = 1.0
MAX_BET = 100000.0


def multiplier_at(difficulty: str, lane: int) -> float:
    """Payout for stopping after `lane` completed lanes (lane 0 == not started)."""
    config = DIFFICULTIES[difficulty]
    if lane <= 0:
        return 1.0
    return round(RTP / (config["survival"] ** lane), 2)


def multiplier_table(difficulty: str):
    config = DIFFICULTIES[difficulty]
    return [multiplier_at(difficulty, lane) for lane in range(1, config["lanes"] + 1)]


def _lane_roll(server_seed: str, client_seed: str, nonce: int, lane: int) -> float:
    digest = hmac.new(
        server_seed.encode("utf-8"),
        f"{client_seed}:{nonce}:{lane}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return int(digest[:8], 16) / 0x100000000


def lane_is_safe(server_seed: str, client_seed: str, nonce: int, lane: int, difficulty: str) -> bool:
    return _lane_roll(server_seed, client_seed, nonce, lane) < DIFFICULTIES[difficulty]["survival"]


def init_arcade_tables():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chicken_rounds (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            bet REAL NOT NULL,
            lane INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            payout REAL DEFAULT 0,
            server_seed TEXT NOT NULL,
            server_seed_hash TEXT NOT NULL,
            client_seed TEXT NOT NULL,
            nonce INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settled_at TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def _round_public(row, reveal_seed=False):
    lost = row["status"] == "lost"
    data = {
        "round_id": row["id"],
        "difficulty": row["difficulty"],
        "bet": float(row["bet"]),
        "lane": int(row["lane"]),
        "status": row["status"],
        "payout": float(row["payout"] or 0),
        # A lost round keeps nothing, so reporting the lane's multiplier here
        # would read as if the player still earned it.
        "multiplier": 0.0 if lost else multiplier_at(row["difficulty"], int(row["lane"])),
        "next_multiplier": multiplier_at(row["difficulty"], int(row["lane"]) + 1),
        "lanes": DIFFICULTIES[row["difficulty"]]["lanes"],
        "server_seed_hash": row["server_seed_hash"],
        "client_seed": row["client_seed"],
        "nonce": int(row["nonce"]),
    }
    if reveal_seed:
        data["server_seed"] = row["server_seed"]
    return data


def start_round(user_id: str, difficulty: str, bet: float, client_seed: str):
    if difficulty not in DIFFICULTIES:
        raise ValueError("Unknown difficulty")
    bet = round(float(bet), 2)
    if bet < MIN_BET or bet > MAX_BET:
        raise ValueError(f"Bet must be between {MIN_BET:.0f} and {MAX_BET:.0f}")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")

    open_round = cursor.execute(
        "SELECT id FROM chicken_rounds WHERE user_id = ? AND status = 'active'", (user_id,)
    ).fetchone()
    if open_round:
        conn.rollback()
        conn.close()
        raise ValueError("Finish your current round first")

    balance_row = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
    if not balance_row or float(balance_row["balance"]) < bet:
        conn.rollback()
        conn.close()
        raise ValueError("Insufficient wallet balance")

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (bet, user_id))

    nonce_row = cursor.execute(
        "SELECT COUNT(*) AS played FROM chicken_rounds WHERE user_id = ?", (user_id,)
    ).fetchone()
    nonce = int(nonce_row["played"]) + 1
    server_seed = secrets.token_hex(32)
    round_id = f"CR-{uuid.uuid4().hex[:10].upper()}"

    cursor.execute(
        """INSERT INTO chicken_rounds
           (id, user_id, difficulty, bet, lane, status, server_seed, server_seed_hash, client_seed, nonce)
           VALUES (?, ?, ?, ?, 0, 'active', ?, ?, ?, ?)""",
        (
            round_id,
            user_id,
            difficulty,
            bet,
            server_seed,
            hashlib.sha256(server_seed.encode("utf-8")).hexdigest(),
            (client_seed or secrets.token_hex(8))[:64],
            nonce,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM chicken_rounds WHERE id = ?", (round_id,)).fetchone()
    conn.close()
    return _round_public(row)


def step_round(user_id: str, round_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        "SELECT * FROM chicken_rounds WHERE id = ? AND user_id = ?", (round_id, user_id)
    ).fetchone()
    if not row or row["status"] != "active":
        conn.rollback()
        conn.close()
        raise ValueError("No active round")

    difficulty = row["difficulty"]
    lane = int(row["lane"]) + 1
    if lane > DIFFICULTIES[difficulty]["lanes"]:
        conn.rollback()
        conn.close()
        raise ValueError("Road already complete — cash out")

    safe = lane_is_safe(row["server_seed"], row["client_seed"], int(row["nonce"]), lane, difficulty)
    now = datetime.now(timezone.utc).isoformat()

    if not safe:
        cursor.execute(
            "UPDATE chicken_rounds SET lane = ?, status = 'lost', payout = 0, settled_at = ? WHERE id = ?",
            (lane, now, round_id),
        )
        conn.commit()
        result = cursor.execute("SELECT * FROM chicken_rounds WHERE id = ?", (round_id,)).fetchone()
        conn.close()
        payload = _round_public(result, reveal_seed=True)
        payload["safe"] = False
        return payload

    reached_end = lane >= DIFFICULTIES[difficulty]["lanes"]
    if reached_end:
        payout = round(float(row["bet"]) * multiplier_at(difficulty, lane), 2)
        cursor.execute(
            "UPDATE chicken_rounds SET lane = ?, status = 'cashed', payout = ?, settled_at = ? WHERE id = ?",
            (lane, payout, now, round_id),
        )
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (payout, user_id))
    else:
        cursor.execute("UPDATE chicken_rounds SET lane = ? WHERE id = ?", (lane, round_id))

    conn.commit()
    result = cursor.execute("SELECT * FROM chicken_rounds WHERE id = ?", (round_id,)).fetchone()
    conn.close()
    payload = _round_public(result, reveal_seed=reached_end)
    payload["safe"] = True
    payload["completed"] = reached_end
    return payload


def cashout_round(user_id: str, round_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    row = cursor.execute(
        "SELECT * FROM chicken_rounds WHERE id = ? AND user_id = ?", (round_id, user_id)
    ).fetchone()
    if not row or row["status"] != "active":
        conn.rollback()
        conn.close()
        raise ValueError("No active round")
    lane = int(row["lane"])
    if lane <= 0:
        conn.rollback()
        conn.close()
        raise ValueError("Take at least one step before cashing out")

    payout = round(float(row["bet"]) * multiplier_at(row["difficulty"], lane), 2)
    cursor.execute(
        "UPDATE chicken_rounds SET status = 'cashed', payout = ?, settled_at = ? WHERE id = ?",
        (payout, datetime.now(timezone.utc).isoformat(), round_id),
    )
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (payout, user_id))
    conn.commit()
    result = cursor.execute("SELECT * FROM chicken_rounds WHERE id = ?", (round_id,)).fetchone()
    conn.close()
    return _round_public(result, reveal_seed=True)


def active_round(user_id: str):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM chicken_rounds WHERE user_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return _round_public(row) if row else None
