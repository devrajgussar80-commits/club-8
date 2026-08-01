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
