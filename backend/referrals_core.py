"""Referral codes and the signup -> deposit -> reward lifecycle.

Model, in one place so the two columns never drift:

* users.referral_code -- the user's OWN code, the one they share. Unique.
* users.referred_by    -- the code they typed at signup (someone else's).
* referrals            -- one row per relationship, carrying the single status
                          that a reward is decided on.

Reward rule: a referral only pays out after the referred user's first deposit
is approved (status -> 'deposited'), and only once the admin approves it
(status -> 'approved', referrer credited). Signing up alone is never enough.
"""

import secrets
import uuid

# No 0/O/1/I: these codes get read off a screen and typed by hand.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
REWARD_AMOUNT = 50.0


def generate_code(length: int = 7) -> str:
    return "R" + "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _unique_code(conn) -> str:
    """A code not already taken. The unique index is the real guard; this just
    avoids a failed insert on the rare collision."""
    for _ in range(20):
        code = generate_code()
        if not conn.execute(
            "SELECT 1 FROM users WHERE referral_code = ?", (code,)
        ).fetchone():
            return code
    # Astronomically unlikely; widen rather than loop forever.
    return generate_code(10)


def migrate_and_backfill(conn) -> None:
    """One-time cleanup plus a per-boot safety net.

    Legacy rows kept the *entered* code in referral_code. Move it to
    referred_by once, then make sure every user has an own code. Guarded by a
    settings flag so the move runs exactly once; the backfill is cheap and
    idempotent, so it runs every boot for any row still missing a code.
    """
    from settings_store import get_setting, set_setting

    if get_setting(conn, "referral_migrated", "false") != "true":
        conn.execute(
            "UPDATE users SET referred_by = referral_code, referral_code = NULL "
            "WHERE referral_code IS NOT NULL AND referred_by IS NULL"
        )
        set_setting(conn, "referral_migrated", "true")
        conn.commit()

    missing = conn.execute(
        "SELECT id FROM users WHERE referral_code IS NULL"
    ).fetchall()
    for row in missing:
        conn.execute(
            "UPDATE users SET referral_code = ? WHERE id = ?",
            (_unique_code(conn), row["id"]),
        )
    if missing:
        conn.commit()


def new_user_code(conn) -> str:
    """Own code for a user being registered right now."""
    return _unique_code(conn)


def record_referral(conn, new_user_id, name, phone, entered_code) -> None:
    """Link a new signup to whoever's code they entered, if it is real.

    A bad or self-referring code is ignored silently: the account is still
    created, it just earns nobody a reward. Runs inside the register txn.
    """
    if not entered_code:
        return
    entered_code = entered_code.strip().upper()
    referrer = conn.execute(
        "SELECT id FROM users WHERE referral_code = ?", (entered_code,)
    ).fetchone()
    if not referrer or referrer["id"] == new_user_id:
        return
    conn.execute(
        """
        INSERT INTO referrals
            (id, referrer_id, referred_id, referred_name, referred_phone, status)
        VALUES (?, ?, ?, ?, ?, 'signed_up')
        ON CONFLICT (referred_id) DO NOTHING
        """,
        (f"REF-{uuid.uuid4().hex[:10].upper()}", referrer["id"], new_user_id, name, phone),
    )


# How far down a chain the dashboard follows. A referral tree is a graph the
# operator built, not one we control, so the walk needs a stop: without it a
# cycle -- which the single-referrer rule makes very unlikely but does not make
# impossible -- would recurse until Postgres gave up.
MAX_DEPTH = 6


NETWORK_QUERY = f"""
WITH RECURSIVE downline AS (
    -- Level 1: the people each user invited directly.
    SELECT r.referrer_id AS root_id,
           r.referred_id,
           1 AS depth
      FROM referrals r
    UNION ALL
    -- Level n+1: the people *those* people invited. This is the chain the
    -- dashboard is asked to show -- A invites B, B invites three more, and
    -- all four hang under A.
    SELECT d.root_id,
           r.referred_id,
           d.depth + 1
      FROM downline d
      JOIN referrals r ON r.referrer_id = d.referred_id
     WHERE d.depth < {MAX_DEPTH}
)
SELECT d.root_id,
       d.depth,
       COUNT(*) AS people,
       COUNT(*) FILTER (WHERE COALESCE(dep.total, 0) > 0) AS depositors,
       COALESCE(SUM(dep.total), 0) AS deposits,
       COALESCE(SUM(u.balance), 0) AS wallets
  FROM downline d
  JOIN users u ON u.id = d.referred_id
  LEFT JOIN (
      SELECT user_id, SUM(amount) AS total
        FROM upi_deposits WHERE status = 'approved'
        GROUP BY user_id
  ) dep ON dep.user_id = d.referred_id
 GROUP BY d.root_id, d.depth
 ORDER BY d.root_id, d.depth
"""


def network(conn) -> list:
    """Every referrer with their downline broken out level by level.

    Level 1 is who they invited themselves; level 2 is who *those* people
    invited, and so on. A user with one direct invite whose invitee brought in
    three more shows as 1 at level 1 and 3 at level 2, which is the chain the
    flat referrals table cannot show on its own.
    """
    levels = {}
    for row in conn.execute(NETWORK_QUERY).fetchall():
        levels.setdefault(row["root_id"], []).append(
            {
                "depth": int(row["depth"]),
                "people": int(row["people"]),
                "depositors": int(row["depositors"]),
                "deposits": round(float(row["deposits"] or 0), 2),
                "wallets": round(float(row["wallets"] or 0), 2),
            }
        )
    if not levels:
        return []

    people = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id, username, phone, referral_code, balance, status "
            "FROM users WHERE id = ANY(?)",
            (list(levels),),
        ).fetchall()
    }

    roots = []
    for root_id, by_level in levels.items():
        user = people.get(root_id, {})
        direct = next((lvl["people"] for lvl in by_level if lvl["depth"] == 1), 0)
        roots.append(
            {
                "id": root_id,
                "name": user.get("username"),
                "phone": user.get("phone"),
                "code": user.get("referral_code"),
                "status": user.get("status"),
                "balance": round(float(user.get("balance") or 0), 2),
                "direct": direct,
                # Everyone below them, however many links down.
                "downline": sum(lvl["people"] for lvl in by_level),
                "indirect": sum(lvl["people"] for lvl in by_level) - direct,
                "depth": max(lvl["depth"] for lvl in by_level),
                "deposits": round(sum(lvl["deposits"] for lvl in by_level), 2),
                "depositors": sum(lvl["depositors"] for lvl in by_level),
                "levels": by_level,
            }
        )
    # Deepest, widest networks first -- those are the ones worth looking at.
    roots.sort(key=lambda r: (-r["downline"], -r["deposits"]))
    return roots


CHAIN_QUERY = f"""
WITH RECURSIVE downline AS (
    SELECT r.referrer_id, r.referred_id, 1 AS depth
      FROM referrals r
     WHERE r.referrer_id = ?
    UNION ALL
    SELECT r.referrer_id, r.referred_id, d.depth + 1
      FROM downline d
      JOIN referrals r ON r.referrer_id = d.referred_id
     WHERE d.depth < {MAX_DEPTH}
)
SELECT d.referrer_id, d.referred_id, d.depth,
       u.username, u.phone, u.referral_code, u.balance, u.status, u.created_at,
       COALESCE(dep.total, 0) AS deposits
  FROM downline d
  JOIN users u ON u.id = d.referred_id
  LEFT JOIN (
      SELECT user_id, SUM(amount) AS total
        FROM upi_deposits WHERE status = 'approved'
        GROUP BY user_id
  ) dep ON dep.user_id = d.referred_id
 ORDER BY d.depth, u.created_at
"""


def chain(conn, root_id: str) -> list:
    """One referrer's whole downline as a tree, ready to render nested."""
    nodes = {}
    children = {}
    for row in conn.execute(CHAIN_QUERY, (root_id,)).fetchall():
        nodes[row["referred_id"]] = {
            "id": row["referred_id"],
            "name": row["username"],
            "phone": row["phone"],
            "code": row["referral_code"],
            "status": row["status"],
            "balance": round(float(row["balance"] or 0), 2),
            "deposits": round(float(row["deposits"] or 0), 2),
            "depth": int(row["depth"]),
            "joined": row["created_at"].isoformat() if row["created_at"] else None,
            "invited": [],
        }
        children.setdefault(row["referrer_id"], []).append(row["referred_id"])

    for parent_id, kids in children.items():
        # The root's own children are the tree's top level; everyone else's
        # hang off their parent's node.
        bucket = nodes[parent_id]["invited"] if parent_id in nodes else None
        if bucket is None:
            continue
        bucket.extend(nodes[kid] for kid in kids if kid in nodes)

    return [nodes[kid] for kid in children.get(root_id, []) if kid in nodes]


def qualify_on_deposit(conn, referred_user_id) -> None:
    """Promote a referral to 'deposited' when the referred user first qualifies.

    Called from inside the deposit-approval transaction. Only moves a referral
    that is still 'signed_up', so later deposits do not reset an already
    approved or rejected reward, and re-approving nothing happens twice.
    """
    conn.execute(
        """
        UPDATE referrals
           SET status = 'deposited', qualified_at = NOW()
         WHERE referred_id = ? AND status = 'signed_up'
        """,
        (referred_user_id,),
    )
