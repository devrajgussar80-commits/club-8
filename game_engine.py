"""Independent fair-play countdown engines for all Wingo demo rooms."""

import asyncio
import math
import secrets
import sqlite3
import threading
import time
from datetime import datetime

from database import get_db_connection


ROOM_CONFIG = {
    "parity": {"duration": 30, "code": "30", "label": "30sec"},
    "sapre": {"duration": 60, "code": "01", "label": "1min"},
    "bcone": {"duration": 180, "code": "03", "label": "3min"},
    "emerd": {"duration": 300, "code": "05", "label": "5min"},
}


class PythonGameEngine:
    def __init__(self):
        now = time.time()
        self.active_room = "parity"
        self.qr_rotation_secs = 60
        self._tick_lock = threading.Lock()
        self.rooms = {}

        for room, config in ROOM_CONFIG.items():
            duration = config["duration"]
            remaining = duration - (int(now) % duration)
            self.rooms[room] = {
                "duration": duration,
                "next_close": now + remaining,
                "period": self._make_period(room, int(now // duration)),
            }

    @property
    def current_period(self):
        return self.rooms[self.active_room]["period"]

    @property
    def time_remaining(self):
        return self.get_status(self.active_room)["time_remaining"]

    @property
    def is_frozen(self):
        return self.get_status(self.active_room)["is_frozen"]

    async def start_loop(self):
        while True:
            await asyncio.sleep(1)
            self.tick()

    def _make_period(self, room, slot):
        config = ROOM_CONFIG[room]
        date_part = datetime.now().strftime("%Y%m%d")
        return f"{date_part}{config['code']}{slot % 100000000:08d}"

    def get_status(self, room):
        if room not in self.rooms:
            raise KeyError(room)

        # WSGI hosts may not keep the FastAPI startup task alive. Advancing
        # lazily here keeps every room's period current on each status request.
        self.tick()
        state = self.rooms[room]
        remaining = max(0, math.ceil(state["next_close"] - time.time()))
        return {
            "room": room,
            "label": ROOM_CONFIG[room]["label"],
            "duration": state["duration"],
            "period": state["period"],
            "time_remaining": remaining,
            "is_frozen": remaining <= 5,
            "betting_open": remaining > 5,
        }

    def is_bet_open(self, room, period=None):
        status = self.get_status(room)
        if period and period != status["period"]:
            return False
        return status["betting_open"]

    def tick(self):
        # Every status read calls tick(), so without this lock two concurrent
        # requests could both resolve the same period and pay every winner twice.
        with self._tick_lock:
            now = time.time()
            for room, state in self.rooms.items():
                if now >= state["next_close"]:
                    self.resolve_room(room)
                    duration = state["duration"]
                    while state["next_close"] <= now:
                        state["next_close"] += duration
                    state["period"] = self._make_period(room, int(state["next_close"] // duration) - 1)

            self.qr_rotation_secs -= 1
            if self.qr_rotation_secs <= 0:
                self.qr_rotation_secs = 60

    def get_number_details(self, number):
        if number in (0, 5):
            color = "violet"
        elif number % 2 == 0:
            color = "red"
        else:
            color = "green"
        return {
            "number": number,
            "color": color,
            "size": "Big" if number >= 5 else "Small",
        }

    def payout_multiplier_for(self, bet, number):
        """Payout multiplier this bet would earn if `number` came out, else 0."""
        selection = bet["selection"]
        select_type = bet["select_type"]
        size = "Big" if number >= 5 else "Small"

        if select_type == "color":
            if selection == "green":
                if number in (1, 3, 7, 9):
                    return 1.96
                if number == 5:
                    return 1.5
            elif selection == "red":
                if number in (2, 4, 6, 8):
                    return 1.96
                if number == 0:
                    return 1.5
            elif selection == "violet" and number in (0, 5):
                return 4.5
        elif select_type == "number":
            try:
                if int(selection) == number:
                    return 8.82
            except (TypeError, ValueError):
                return 0.0
        elif select_type == "size" and selection == size:
            return 1.96
        return 0.0

    def calculate_winning_outcome(self, bets=None, settings=None):
        settings = settings or {}
        mode = (settings.get("prediction_mode") or "random").lower()

        if mode == "manual":
            try:
                forced = int(settings.get("forced_number", 7))
            except (TypeError, ValueError):
                forced = 7
            if 0 <= forced <= 9:
                return self.get_number_details(forced)

        if mode == "auto_least" and bets:
            # Pick whichever number costs the house the least this round.
            liabilities = {
                n: sum(
                    float(b["total_stake"]) * self.payout_multiplier_for(b, n)
                    for b in bets
                )
                for n in range(10)
            }
            lowest = min(liabilities.values())
            candidates = [n for n, total in liabilities.items() if total == lowest]
            return self.get_number_details(secrets.choice(candidates))

        return self.get_number_details(secrets.randbelow(10))

    def _read_settings(self, conn):
        return {
            row["key"]: row["value"]
            for row in conn.execute(
                "SELECT key, value FROM system_settings WHERE key IN ('prediction_mode', 'forced_number')"
            ).fetchall()
        }

    def resolve_room(self, room):
        state = self.rooms[room]
        period = state["period"]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        bets = [
            dict(row)
            for row in cursor.execute(
                "SELECT * FROM bets WHERE period = ? AND status = 'pending'",
                (period,),
            ).fetchall()
        ]

        result = self.calculate_winning_outcome(bets, self._read_settings(conn))
        number = result["number"]
        color = result["color"]
        size = result["size"]

        # `period` is the primary key, so this INSERT is the claim. If another
        # worker process already resolved this round the insert fails and we
        # leave its payouts untouched instead of paying every winner twice.
        try:
            cursor.execute(
                """
                INSERT INTO rounds
                    (period, room, winning_number, winning_color, winning_size, status)
                VALUES (?, ?, ?, ?, ?, 'completed')
                """,
                (period, room, number, color, size),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            return

        for bet in bets:
            stake = float(bet["total_stake"])
            payout_multiplier = self.payout_multiplier_for(bet, number)

            if payout_multiplier:
                payout = round(stake * payout_multiplier, 2)
                cursor.execute(
                    "UPDATE bets SET status = 'win', payout = ? WHERE id = ?",
                    (payout, bet["id"]),
                )
                cursor.execute(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (payout, bet["user_id"]),
                )
            else:
                cursor.execute(
                    "UPDATE bets SET status = 'loss', payout = 0 WHERE id = ?",
                    (bet["id"],),
                )

        conn.commit()
        conn.close()

    def get_active_qr_code(self):
        conn = get_db_connection()
        qrs = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM qr_codes ORDER BY is_active DESC, created_at DESC"
            ).fetchall()
        ]
        conn.close()

        if not qrs:
            return {
                "id": "DEMO",
                "name": "Demo credits",
                "note": "No real payment is processed",
                "qr_url": "",
                "seconds_until_next_rotation": self.qr_rotation_secs,
            }

        active = qrs[0]
        active["seconds_until_next_rotation"] = self.qr_rotation_secs
        return active


python_engine = PythonGameEngine()
