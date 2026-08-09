"""Promote an account to admin, or create one, from the command line.

There is no other way in when nobody is signed in yet. ``/api/admin/grant-admin``
promotes an existing account but sits behind ``require_admin`` itself, and
``/api/admin/security/credentials`` needs a live session -- so a lost admin
password, or a database that has never had an admin in it, cannot be recovered
through the API at all. This is that missing door, and it needs shell access to
the machine holding DATABASE_URL, which is the point.

Run it from ``backend/`` with the same DATABASE_URL the app uses::

    python make_admin.py 9876543210
    python make_admin.py 9876543210 --username "Operator"

The password is asked for at the prompt, or taken from ADMIN_BOOTSTRAP_PASSWORD.
It is deliberately not an argument: shells keep a history file, and an admin
password for a platform that moves real money does not belong in it -- nor in
this file, which is committed to a public repository.

Existing account: the password is reset and is_admin set. Everything else --
balance, referral code, bonus run, history -- is left exactly as it was.
Missing account: a new one is created the way registration would, minus the
signup bonus, since an operator console is not a player.
"""

import argparse
import getpass
import os
import sys
import uuid

import auth as auth_helpers
import referrals_core
from database import get_db_connection


def read_password() -> str:
    """From the environment for an automated run, otherwise asked for twice."""
    from_env = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if from_env:
        return from_env

    first = getpass.getpass("New admin password: ")
    if first != getpass.getpass("Repeat it: "):
        sys.exit("The two passwords do not match. Nothing was changed.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote or create an admin account.")
    parser.add_argument("phone", help="10-digit login phone number")
    parser.add_argument(
        "--username",
        default="Admin",
        help="Display name, used only when the account has to be created",
    )
    args = parser.parse_args()

    phone = "".join(ch for ch in args.phone if ch.isdigit())[-10:]
    if len(phone) != 10:
        sys.exit(f"Expected a 10-digit phone number, got {args.phone!r}.")

    password = read_password()
    if len(password) < 8:
        sys.exit("Use at least 8 characters. This account approves withdrawals.")

    conn = get_db_connection()
    try:
        # The login route matches the phone as a literal string, so a row saved
        # as +91XXXXXXXXXX can never be found by someone typing ten digits --
        # a correct password refused for a reason no message explains. Look for
        # all three shapes the app has written over time, then settle the row
        # on the plain ten digits so the login SELECT agrees with it.
        existing = conn.execute(
            "SELECT id, username, phone FROM users WHERE phone IN (?, ?, ?)",
            (phone, f"+91{phone}", f"91{phone}"),
        ).fetchone()

        pwd_hash = auth_helpers.hash_password(password)

        if existing:
            row = dict(existing)
            conn.execute(
                "UPDATE users SET password_hash = ?, phone = ?, is_admin = 1, "
                "status = 'active' WHERE id = ?",
                (pwd_hash, phone, row["id"]),
            )
            conn.commit()
            print(f"Updated {row['username']} ({phone}): password reset, is_admin = 1.")
            if row["phone"] != phone:
                print(f"  Phone normalised from {row['phone']} -- that alone would "
                      f"have blocked sign-in.")
        else:
            user_id = f"USR{uuid.uuid4().hex[:10].upper()}"
            # No signup bonus and no bonus run: the run steers a player's win
            # rate, and an operator account has no business carrying one.
            conn.execute(
                """
                INSERT INTO users
                    (id, phone, username, password_hash, balance, status,
                     referral_code, game_access_enabled, is_admin,
                     luck_target, luck_progress, luck_done)
                VALUES (?, ?, ?, ?, 0, 'active', ?, 1, 1, NULL, 0, 1)
                """,
                (user_id, phone, args.username, pwd_hash,
                 referrals_core.new_user_code(conn)),
            )
            conn.commit()
            print(f"Created {args.username} ({phone}) as an admin.")

        print("Sign in at /admin with that phone and password.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
