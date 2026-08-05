"""Round clock and settlement shared by the multiplayer table games.

Fish vs Tiger and Vortex differ only in what a selection means and what it
pays. Everything underneath -- when a round opens, when betting freezes, who
claims a closed round for settlement, how a stake is held and a payout
credited -- is identical, and is written once here.

That is not tidiness for its own sake. The settlement claim is

    UPDATE round_games SET status='settled' WHERE game=? AND period=? AND status='open' RETURNING

which is what stops two concurrent requests both paying out the same round.
Copying that into each new game is how a platform ends up paying a round
twice, so a game module never touches it: it supplies `decide` and `pays`,
and gets the money handling for free.
"""

import json
import math
import threading
import time
import uuid
from datetime import datetime

from fastapi import HTTPException

from database import get_db_connection
from game_controls import check_playable, get_controls
from games_core import hold_stake, secure_unit

# One tick at a time across every game: each state read advances the clock, and
# without this two requests could both try to settle the same closed round.
_tick_lock = threading.Lock()


class RoundGame:
    """A shared-round game.

    `decide(bets, controls)` returns the round's outcome dict.
    `pays(selection, outcome)` returns the multiple of the stake returned,
    stake included -- 0 for a loss, 1 for a push.
    """

    def __init__(self, game, period_code, duration=30, freeze=3):
        self.game = game
        self.period_code = period_code
        self.duration = duration
        self.freeze = freeze

    # ------------------------------------------------------------ subclass API

    def selections(self) -> dict:
        """{selection: label} -- the bets this game accepts."""
        raise NotImplementedError

    def decide(self, bets, controls) -> dict:
        raise NotImplementedError

    def pays(self, selection: str, outcome: dict) -> float:
        raise NotImplementedError

    # ---------------------------------------------------------------- the clock

    def _slot(self, now: float) -> int:
        return int(now // self.duration)

    def _period(self, slot: int) -> str:
        return f"{datetime.now():%Y%m%d}{self.period_code}{slot % 100000000:08d}"

    def view(self, now: float) -> dict:
        remaining = max(0, math.ceil((self._slot(now) + 1) * self.duration - now))
        return {
            "period": self._period(self._slot(now)),
            "seconds_left": remaining,
            "betting_open": remaining > self.freeze,
        }

    def tick(self, conn) -> str:
        """Settle any round that has closed; return the period now open."""
        with _tick_lock:
            now = time.time()
            current = self._period(self._slot(now))
            previous = self._period(self._slot(now) - 1)
            # Guarantee a row for the round that just closed even if nobody bet
            # in it, so the history has no gaps.
            conn.execute(
                "INSERT INTO round_games (game, period, status) VALUES (?, ?, 'open') "
                "ON CONFLICT (game, period) DO NOTHING",
                (self.game, previous),
            )
            due = conn.execute(
                "SELECT period FROM round_games "
                "WHERE game = ? AND status = 'open' AND period <> ?",
                (self.game, current),
            ).fetchall()
            for row in due:
                self._settle(conn, row["period"])
            conn.commit()
            return current

    def _settle(self, conn, period: str) -> None:
        claimed = conn.execute(
            "UPDATE round_games SET status = 'settled', settled_at = NOW() "
            "WHERE game = ? AND period = ? AND status = 'open' RETURNING period",
            (self.game, period),
        ).fetchone()
        if not claimed:
            return  # already settled, or another request claimed it first

        bets = conn.execute(
            "SELECT * FROM round_bets WHERE game = ? AND period = ? AND status = 'pending'",
            (self.game, period),
        ).fetchall()
        outcome = self.decide(bets, get_controls(conn, self.game))

        for bet in bets:
            multiple = self.pays(bet["selection"], outcome)
            payout = round(float(bet["amount"]) * multiple, 2)
            # A push returns the stake, which is neither a win nor a loss --
            # labelling it either way would make the player's history lie.
            status = "won" if multiple > 1 else ("push" if multiple == 1 else "lost")
            conn.execute(
                "UPDATE round_bets SET status = ?, payout = ? WHERE id = ?",
                (status, payout, bet["id"]),
            )
            if payout > 0:
                conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (payout, bet["user_id"]),
                )
            # Same analytics ledger as every other game.
            conn.execute(
                """
                INSERT INTO game_rounds (id, game, user_id, stake, payout, outcome)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{self.game.upper()[:3]}-{uuid.uuid4().hex[:10].upper()}",
                    self.game,
                    bet["user_id"],
                    float(bet["amount"]),
                    payout,
                    json.dumps({**outcome, "bet": bet["selection"]}),
                ),
            )

        conn.execute(
            "UPDATE round_games SET outcome = ? WHERE game = ? AND period = ?",
            (json.dumps(outcome), self.game, period),
        )

    # ------------------------------------------------------------------- betting

    def place(self, user: dict, selection: str, amount: float) -> dict:
        if selection not in self.selections():
            raise HTTPException(status_code=400, detail="Invalid selection.")

        conn = get_db_connection()
        try:
            check_playable(conn, self.game, round(float(amount or 0), 2))
            current = self.tick(conn)
            if not self.view(time.time())["betting_open"]:
                raise HTTPException(
                    status_code=400, detail="Betting is closed for this round."
                )
        finally:
            conn.close()

        # Debited before the bet is recorded: if the wallet cannot cover it this
        # raises and no row is written, so a bet never exists unpaid for.
        held = hold_stake(user, amount)

        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO round_games (game, period, status) VALUES (?, ?, 'open') "
                "ON CONFLICT (game, period) DO NOTHING",
                (self.game, current),
            )
            conn.execute(
                """
                INSERT INTO round_bets (id, game, period, user_id, selection, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"RB-{uuid.uuid4().hex[:12].upper()}",
                    self.game, current, user["id"], selection, held["stake"],
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {"status": "success", "period": current,
                "selection": selection, "amount": held["stake"],
                "balance": held["balance"]}

    def state(self, user: dict) -> dict:
        conn = get_db_connection()
        try:
            current = self.tick(conn)
            view = self.view(time.time())

            mine = conn.execute(
                "SELECT selection, amount FROM round_bets "
                "WHERE game = ? AND period = ? AND user_id = ? ORDER BY created_at",
                (self.game, current, user["id"]),
            ).fetchall()

            history = conn.execute(
                "SELECT period, outcome FROM round_games "
                "WHERE game = ? AND status = 'settled' AND outcome IS NOT NULL "
                "ORDER BY period DESC LIMIT 15",
                (self.game,),
            ).fetchall()

            # How the player's bets on the round that just settled did, so the
            # client can show a result rather than only the next countdown.
            # The wallet moves at settlement, which happens server-side with no
            # request from this player -- so the balance rides along with the
            # state or a winner watches a stale number until they navigate away.
            balance = conn.execute(
                "SELECT balance FROM users WHERE id = ?", (user["id"],)
            ).fetchone()

            last = history[0] if history else None
            settled = []
            if last:
                settled = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT selection, amount, status, payout FROM round_bets "
                        "WHERE game = ? AND period = ? AND user_id = ?",
                        (self.game, last["period"], user["id"]),
                    ).fetchall()
                ]
        finally:
            conn.close()

        return {
            **view,
            "balance": round(float(balance["balance"]), 2) if balance else None,
            "selections": self.selections(),
            "my_bets": [dict(row) for row in mine],
            "history": [
                {"period": row["period"], "outcome": row["outcome"]} for row in history
            ],
            "last_result": (
                {"period": last["period"], "outcome": last["outcome"], "my_bets": settled}
                if last else None
            ),
        }


def pick(pool):
    """Uniform choice from a sequence, using the OS CSPRNG."""
    return pool[int(secure_unit() * len(pool))]
