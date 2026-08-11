"""The staff portal's API.

Employees are ordinary rows in `users` carrying `is_employee = 1`, created
from the dashboard's Team tab. They sign in here rather than in the player app
because what they need to see is the other side of the referral table: who
they brought in, which of those actually deposited, and what that has earned.

Everything here is scoped to the caller by `get_employee_user`. An employee is
staff, not an operator -- none of these routes can change a balance, approve a
withdrawal, or read another employee's downline.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response

import auth as auth_helpers
import referrals_core
from database import get_db_connection
from deps import get_employee_user
from schemas import EmployeeLoginRequest, EmployeeRegisterRequest

router = APIRouter(prefix="/api/employee", tags=["employee"])

# A shift is long, and a portal that signs people out mid-afternoon just gets
# a shared password written on a desk. Shorter than the admin session all the
# same: this account cannot move money, but it does carry player phone numbers.
SESSION_DAYS = 7

# What a referral pays once the referred player's first deposit is approved.
# Lives in referrals_core so the portal and the reward release agree.
REWARD = referrals_core.REWARD_AMOUNT


def _profile(row: dict, group: dict = None) -> dict:
    """The employee's own record, minus anything the portal has no use for."""
    return {
        "group": group,
        "id": row["id"],
        "name": row["username"],
        "phone": row["phone"],
        "referral_code": row["referral_code"],
        "joined": row["created_at"].isoformat() if row.get("created_at") else None,
        "status": row["status"],
        "has_photo": bool(row.get("photo")),
    }


@router.post("/login")
def employee_login(req: EmployeeLoginRequest):
    """Same credentials the account was created with in the Team tab.

    The 'not an employee' case answers differently from a bad password on
    purpose: a player who wanders in here has typed the right details and
    needs to be told they are in the wrong place, not that their password is
    wrong. It leaks only that the account exists, which the player already
    knows -- it is their own.
    """
    phone = "".join(ch for ch in req.phone if ch.isdigit())[-10:]
    conn = get_db_connection(readonly=True)
    try:
        # Accept the shapes the app has written over time, the same way the
        # admin sign-in does, so a row saved as +91XXXXXXXXXX still resolves.
        row = conn.execute(
            "SELECT * FROM users WHERE phone IN (?, ?, ?)",
            (phone, f"+91{phone}", f"91{phone}"),
        ).fetchone()
    finally:
        conn.close()

    if not row or not auth_helpers.verify_password(req.password, dict(row)["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid phone or password!")

    user = dict(row)
    if not user.get("is_employee"):
        # Someone whose application is still in the queue typed the right
        # password and deserves to know that, rather than being told their
        # account is a player account when it is not one yet.
        state = user.get("employee_status")
        if state == "pending":
            raise HTTPException(
                status_code=403,
                detail="Your account is waiting for admin approval. "
                       "You will be able to sign in once it is approved.",
            )
        if state == "rejected":
            raise HTTPException(
                status_code=403,
                detail=(user.get("employee_note") or "").strip()
                       or "Your application was not approved. Contact your admin.",
            )
        raise HTTPException(
            status_code=403,
            detail="This is a player account. Sign in through the app instead.",
        )
    if user.get("status") == "disabled":
        raise HTTPException(status_code=403, detail="Your account has been suspended by Admin.")

    token = auth_helpers.create_access_token(
        {"user_id": user["id"], "phone": user["phone"], "is_employee": True},
        expires_delta=timedelta(days=SESSION_DAYS),
    )
    return {"status": "success", "token": token, "employee": _profile(user)}


@router.post("/register")
def employee_register(req: EmployeeRegisterRequest):
    """Apply for a staff account from the portal's sign-in page.

    This grants nothing. The row is written with `is_employee = 0` and
    `employee_status = 'pending'`, which is the same state as a player as far
    as every other route is concerned -- the account cannot sign in here, and
    it carries no referral rewards, until an admin approves it in the Team
    tab. Making self-signup write `is_employee = 1` and relying on the admin
    to notice would be an open door to the staff portal.
    """
    phone = "".join(ch for ch in req.phone if ch.isdigit())[-10:]
    if len(phone) != 10:
        raise HTTPException(status_code=400, detail="Enter a 10-digit mobile number.")

    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT id, is_employee, employee_status FROM users "
            "WHERE phone IN (?, ?, ?)",
            (phone, f"+91{phone}", f"91{phone}"),
        ).fetchone()
        if existing:
            row = dict(existing)
            state = row.get("employee_status")
            if row.get("is_employee"):
                raise HTTPException(
                    status_code=400,
                    detail="That number already has a staff account. Sign in instead.",
                )
            if state == "pending":
                raise HTTPException(
                    status_code=400,
                    detail="An application for that number is already waiting for approval.",
                )
            if state == "rejected":
                raise HTTPException(
                    status_code=400,
                    detail="An application for that number was already reviewed. "
                           "Contact your admin.",
                )
            raise HTTPException(
                status_code=400,
                detail="That number is already registered as a player.",
            )

        user_id = f"USR{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            """
            INSERT INTO users
                (id, phone, username, password_hash, balance, status,
                 referral_code, game_access_enabled, is_employee,
                 employee_status, employee_requested_at, employee_note,
                 luck_done)
            VALUES (?, ?, ?, ?, 0, 'active', ?, 0, 0, 'pending', NOW(), ?, 1)
            """,
            (
                user_id, phone, req.username.strip(),
                auth_helpers.hash_password(req.password),
                # A code is issued now so approval is a single flag flip and
                # the invite link works the moment they first sign in.
                referrals_core.new_user_code(conn),
                (req.note or "").strip() or None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "pending",
        "id": user_id,
        "message": "Application sent. Your admin will review it shortly.",
    }


@router.get("/me")
def employee_me(me: dict = Depends(get_employee_user)):
    """Profile plus the headline numbers, so the portal opens on one request."""
    conn = get_db_connection(readonly=True)
    try:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS invited,
                COUNT(*) FILTER (WHERE r.status IN ('deposited', 'approved')) AS deposited,
                COUNT(*) FILTER (WHERE r.status = 'approved') AS paid,
                COUNT(*) FILTER (WHERE r.status = 'rejected') AS rejected,
                COALESCE(SUM(r.reward) FILTER (WHERE r.status = 'approved'), 0) AS earned,
                COALESCE(SUM(r.reward) FILTER (WHERE r.status = 'deposited'), 0) AS pending
            FROM referrals r WHERE r.referrer_id = ?
            """,
            (me["id"],),
        ).fetchone()

        # What the people they invited actually paid in. The reward is a flat
        # amount per referral, so this is the number that says whether a
        # recruiter is bringing in players who spend or players who look once.
        brought = conn.execute(
            """
            SELECT COALESCE(SUM(d.amount), 0) AS total
            FROM upi_deposits d
            JOIN referrals r ON r.referred_id = d.user_id
            WHERE r.referrer_id = ? AND d.status = 'approved'
            """,
            (me["id"],),
        ).fetchone()

        # How this employee places against the others, by paid-out referrals.
        # Only the position is returned -- not the table -- so the portal can
        # motivate without handing one employee another's numbers.
        rank = conn.execute(
            """
            WITH scores AS (
                SELECT u.id,
                       COUNT(r.id) FILTER (WHERE r.status = 'approved') AS wins
                FROM users u
                LEFT JOIN referrals r ON r.referrer_id = u.id
                WHERE u.is_employee = 1
                GROUP BY u.id
            )
            SELECT (SELECT COUNT(*) FROM scores) AS staff,
                   (SELECT COUNT(*) + 1 FROM scores s2
                     WHERE s2.wins > (SELECT wins FROM scores WHERE id = ?)) AS place
            """,
            (me["id"],),
        ).fetchone()

        # The group they belong to, with what the whole group has brought in.
        # None when they are not in one, which the portal renders as a hint to
        # ask their admin rather than as an empty panel.
        group = None
        if me.get("group_id"):
            row = conn.execute(
                """
                SELECT g.id, g.name, g.note,
                       COUNT(DISTINCT u.id) AS members,
                       COUNT(r.id) AS invited,
                       COUNT(r.id) FILTER (WHERE r.status = 'approved') AS paid,
                       COALESCE(SUM(r.reward) FILTER (WHERE r.status = 'approved'), 0) AS earned
                FROM employee_groups g
                LEFT JOIN users u ON u.group_id = g.id AND u.is_employee = 1
                LEFT JOIN referrals r ON r.referrer_id = u.id
                WHERE g.id = ?
                GROUP BY g.id, g.name, g.note
                """,
                (me["group_id"],),
            ).fetchone()
            if row:
                g = dict(row)
                group = {
                    "id": g["id"], "name": g["name"], "note": g["note"] or "",
                    "members": int(g["members"] or 0),
                    "invited": int(g["invited"] or 0),
                    "paid": int(g["paid"] or 0),
                    "earned": round(float(g["earned"] or 0), 2),
                }
    finally:
        conn.close()

    t = dict(totals)
    invited = int(t["invited"] or 0)
    deposited = int(t["deposited"] or 0)
    return {
        "employee": _profile(me, group),
        "stats": {
            "invited": invited,
            "deposited": deposited,
            # The ones who signed up and never paid in. This is the number a
            # recruiter can actually act on, so it is given rather than left
            # for the portal to subtract.
            "not_deposited": invited - deposited,
            "conversion": round(deposited / invited * 100, 1) if invited else 0.0,
            "paid": int(t["paid"] or 0),
            "rejected": int(t["rejected"] or 0),
            "earned": round(float(t["earned"] or 0), 2),
            "pending": round(float(t["pending"] or 0), 2),
            "deposits_brought": round(float(dict(brought)["total"] or 0), 2),
            "reward_per_referral": REWARD,
            "rank": int(dict(rank)["place"] or 1),
            "staff_count": int(dict(rank)["staff"] or 1),
        },
    }


@router.get("/referrals")
def employee_referrals(me: dict = Depends(get_employee_user)):
    """Everyone this employee invited directly, and what each one earned.

    The `earned` flag is the question the portal exists to answer: a referral
    pays only once the referred player's first deposit is approved *and* an
    admin releases the reward, so "signed up" and "earned me something" are
    two different things and the list has to say which is which.
    """
    conn = get_db_connection(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT r.referred_id, r.referred_name, r.referred_phone, r.status,
                   r.reward, r.created_at, r.qualified_at, r.approved_at,
                   u.balance, u.status AS account_status,
                   COALESCE(dep.total, 0) AS deposits
            FROM referrals r
            JOIN users u ON u.id = r.referred_id
            LEFT JOIN (
                SELECT user_id, SUM(amount) AS total
                FROM upi_deposits WHERE status = 'approved' GROUP BY user_id
            ) dep ON dep.user_id = r.referred_id
            WHERE r.referrer_id = ?
            ORDER BY r.created_at DESC
            """,
            (me["id"],),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for raw in rows:
        r = dict(raw)
        status = r["status"]
        out.append({
            "id": r["referred_id"],
            "name": r["referred_name"],
            # Masked: a recruiter needs to recognise their own contact, not to
            # walk away with a list of player phone numbers.
            "phone": _mask(r["referred_phone"]),
            "status": status,
            "deposited": float(r["deposits"] or 0) > 0,
            "deposits": round(float(r["deposits"] or 0), 2),
            "earned": status == "approved",
            "reward": round(float(r["reward"] or 0), 2) if status == "approved" else 0.0,
            "pending_reward": round(float(r["reward"] or 0), 2) if status == "deposited" else 0.0,
            "why": _why(status, float(r["deposits"] or 0)),
            "joined": r["created_at"].isoformat() if r["created_at"] else None,
            "qualified_at": r["qualified_at"].isoformat() if r["qualified_at"] else None,
            "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        })
    return {"referrals": out, "reward_per_referral": REWARD}


def _mask(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"{digits[:2]}••••{digits[-3:]}" if len(digits) >= 5 else "•••••"


def _why(status: str, deposits: float) -> str:
    """Plain English for why a referral has or has not paid out yet."""
    if status == "approved":
        return "Paid — reward released by admin."
    if status == "deposited":
        return "Deposit approved. Reward is waiting for admin to release it."
    if status == "rejected":
        return "Admin declined the reward for this referral."
    if deposits > 0:
        return "They have deposited, but it is not approved yet."
    return "Signed up but has never deposited. No reward until they do."


@router.get("/chain")
def employee_chain(me: dict = Depends(get_employee_user)):
    """This employee's downline as a tree: who they invited, who those invited.

    Reuses the same recursive walk the dashboard uses, rooted at the caller so
    an employee can only ever see below themselves, never sideways or up.
    """
    conn = get_db_connection(readonly=True)
    try:
        tree = referrals_core.chain(conn, me["id"])
    finally:
        conn.close()

    def scrub(nodes: list) -> list:
        for node in nodes:
            node["phone"] = _mask(node.get("phone"))
            # Someone else's wallet is not this employee's business; what they
            # have paid in is what the referral is judged on.
            node.pop("balance", None)
            node["invited"] = scrub(node.get("invited") or [])
        return nodes

    scrubbed = scrub(tree)

    def count(nodes: list) -> int:
        return sum(1 + count(n["invited"]) for n in nodes)

    return {"chain": scrubbed, "total": count(scrubbed), "max_depth": referrals_core.MAX_DEPTH}


@router.get("/colleagues")
def employee_colleagues(me: dict = Depends(get_employee_user)):
    """The other staff, as a leaderboard of referral counts.

    Names, photos and totals only -- no phone numbers, no wallets, and nobody
    else's referral list. Enough to see how the team is doing and who to ask
    for help, without turning the portal into a staff directory.
    """
    conn = get_db_connection(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.created_at, u.photo IS NOT NULL AS has_photo,
                   u.group_id, g.name AS group_name,
                   COUNT(r.id) AS invited,
                   COUNT(r.id) FILTER (WHERE r.status = 'approved') AS paid,
                   COALESCE(SUM(r.reward) FILTER (WHERE r.status = 'approved'), 0) AS earned
            FROM users u
            LEFT JOIN employee_groups g ON g.id = u.group_id
            LEFT JOIN referrals r ON r.referrer_id = u.id
            WHERE u.is_employee = 1 AND u.status <> 'disabled'
            GROUP BY u.id, u.username, u.created_at, (u.photo IS NOT NULL),
                     u.group_id, g.name
            ORDER BY paid DESC, invited DESC, u.created_at
            """
        ).fetchall()
    finally:
        conn.close()

    return {"colleagues": [
        {
            "id": r["id"],
            "name": r["username"],
            "is_me": r["id"] == me["id"],
            "has_photo": bool(r["has_photo"]),
            "group_id": r["group_id"],
            "group_name": r["group_name"],
            "same_group": bool(r["group_id"]) and r["group_id"] == me.get("group_id"),
            "invited": int(r["invited"] or 0),
            "paid": int(r["paid"] or 0),
            "earned": round(float(r["earned"] or 0), 2),
            "joined": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in map(dict, rows)
    ]}


@router.get("/photo/{user_id}")
def employee_photo(user_id: str, _: dict = Depends(get_employee_user)):
    """A staff photo. Behind the same session as the rest of the portal, so
    the images are not a public endpoint keyed by a guessable user id."""
    conn = get_db_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT photo, photo_type FROM users WHERE id = ? AND is_employee = 1",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not dict(row)["photo"]:
        raise HTTPException(status_code=404, detail="No photo for this account")
    r = dict(row)
    return Response(
        content=bytes(r["photo"]),
        media_type=r["photo_type"] or "image/jpeg",
        # Keyed by user id and changed rarely; the portal appends a cache
        # buster when it knows the photo moved.
        headers={"Cache-Control": "private, max-age=300"},
    )
