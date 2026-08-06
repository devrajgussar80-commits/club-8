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

import asyncio
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

# Every game built in this process, so the background clock can find them.
_games: "list[RoundGame]" = []


async def run_clock() -> None:
    """Settle closed rounds in the background instead of on a player's poll.

    Settlement is the expensive part -- an insert, a scan and a write per bet.
    Left on the request path it landed on whichever unlucky player polled first
    after a boundary, and their screen sat for several seconds once a round.
    Here it happens on the clock, so a poll is only ever a read.

    This is a fast path, not the guarantee: `state` still ticks lazily, so a
    host that never runs this task, or a boundary this task sleeps through,
    settles on the next read exactly as before.
    """
    while True:
        now = time.time()
        # Wake just after the earliest boundary any game is heading for.
        nxt = min(((g._slot(now) + 1) * g.duration for g in _games), default=now + 1)
        await asyncio.sleep(max(0.2, nxt - now + 0.2))
        for game in _games:
            try:
                await asyncio.to_thread(game.tick_if_due, True)
            except Exception:
                # A database blip must not stop the clock for every game.
                pass


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
        # The last round boundary this process has already settled.
        self._ticked_slot = None
        _games.append(self)

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
            # Sent so the client can close betting on its own countdown rather
            # than waiting for a poll to tell it -- otherwise the button stays
            # live for a moment after the server has stopped accepting bets.
            "freeze": self.freeze,
        }

    def tick_if_due(self, wait: bool = False) -> str:
        """Like `tick`, but never makes a reader queue behind the settlement.

        A poll between boundaries -- almost all of them -- returns the current
        period without touching the database at all. On the boundary itself the
        background clock is usually already settling, and `wait=False` means a
        poll that arrives mid-settlement is answered straight away rather than
        blocking on the lock: the result lands a moment later and the next poll
        shows it. Waiting instead is what made one poll a round take seconds.
        """
        slot = self._slot(time.time())
        if self._ticked_slot == slot:
            return self._period(slot)

        if not _tick_lock.acquire(blocking=wait):
            return self._period(slot)
        try:
            # Someone else may have finished between the two checks.
            if self._ticked_slot == slot:
                return self._period(slot)
            conn = get_db_connection()
            try:
                return self._tick_locked(conn, slot)
            finally:
                conn.close()
        finally:
            _tick_lock.release()

    def tick(self, conn) -> str:
        """Settle any round that has closed; return the period now open.

        Blocks until the settlement lock is free, so the caller can rely on the
        returned period having a row. Reads should use `tick_if_due` instead.
        """
        slot = self._slot(time.time())
        if self._ticked_slot == slot:
            return self._period(slot)
        with _tick_lock:
            if self._ticked_slot == slot:
                return self._period(slot)
            return self._tick_locked(conn, slot)

    def _tick_locked(self, conn, slot: int) -> str:
        """The settlement itself. Caller holds `_tick_lock`.

        Safe across processes too: settlement is claimed with a conditional
        UPDATE, so a second worker that has not yet ticked this slot simply
        finds nothing left to claim.
        """
        current = self._period(slot)
        previous = self._period(slot - 1)
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
        self._ticked_slot = slot
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
        """Everything the client polls for, in ONE round trip.

        This was four sequential queries -- my bets, the history, the balance,
        then the settled bets of the last round. Against a database on another
        host that is four network waits stacked up, and it made a poll take
        three to five seconds on a game whose round is thirty. Postgres can
        assemble the whole payload itself, so it does.
        """
        current = self.tick_if_due()
        # Read-only from here: a transaction would cost a BEGIN going in and a
        # ROLLBACK going back to the pool, two round trips this never needs.
        conn = get_db_connection(readonly=True)
        try:
            row = conn.execute(
                """
                WITH recent AS (
                    SELECT period, outcome FROM round_games
                    WHERE game = ? AND status = 'settled' AND outcome IS NOT NULL
                    ORDER BY period DESC LIMIT 15
                ),
                latest AS (SELECT period, outcome FROM recent ORDER BY period DESC LIMIT 1)
                SELECT
                  (SELECT balance FROM users WHERE id = ?) AS balance,
                  (SELECT COALESCE(json_agg(json_build_object(
                        'selection', selection, 'amount', amount) ORDER BY created_at), '[]')
                     FROM round_bets
                    WHERE game = ? AND period = ? AND user_id = ?) AS my_bets,
                  (SELECT COALESCE(json_agg(json_build_object(
                        'period', period, 'outcome', outcome) ORDER BY period DESC), '[]')
                     FROM recent) AS history,
                  (SELECT period FROM latest) AS last_period,
                  (SELECT outcome FROM latest) AS last_outcome,
                  (SELECT COALESCE(json_agg(json_build_object(
                        'selection', selection, 'amount', amount,
                        'status', status, 'payout', payout)), '[]')
                     FROM round_bets
                    WHERE game = ? AND user_id = ?
                      AND period = (SELECT period FROM latest)) AS last_bets
                """,
                (self.game, user["id"], self.game, current, user["id"],
                 self.game, user["id"]),
            ).fetchone()
        finally:
            conn.close()

        return {
            **self.view(time.time()),
            "balance": round(float(row["balance"]), 2) if row["balance"] is not None else None,
            "selections": self.selections(),
            "my_bets": row["my_bets"] or [],
            "history": row["history"] or [],
            "last_result": (
                {
                    "period": row["last_period"],
                    "outcome": row["last_outcome"],
                    "my_bets": row["last_bets"] or [],
                }
                if row["last_period"] else None
            ),
        }


def pick(pool):
    """Uniform choice from a sequence, using the OS CSPRNG."""
    return pool[int(secure_unit() * len(pool))]
