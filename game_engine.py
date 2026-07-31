"""Independent fair-play countdown engines for all Wingo demo rooms."""

import asyncio
import math
import random
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

    def calculate_winning_outcome(self):
        return self.get_number_details(random.randint(0, 9))

    def resolve_room(self, room):
        state = self.rooms[room]
        period = state["period"]
        result = self.calculate_winning_outcome()
        number = result["number"]
        color = result["color"]
        size = result["size"]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO rounds
                (period, room, winning_number, winning_color, winning_size, status)
            VALUES (?, ?, ?, ?, ?, 'completed')
            """,
            (period, room, number, color, size),
        )

        bets = [
            dict(row)
            for row in cursor.execute(
                "SELECT * FROM bets WHERE period = ? AND status = 'pending'",
                (period,),
            ).fetchall()
        ]

        for bet in bets:
            stake = float(bet["total_stake"])
            selection = bet["selection"]
            select_type = bet["select_type"]
            won = False
            payout_multiplier = 0.0

            if select_type == "color":
                if selection == "green" and number in (1, 3, 7, 9):
                    won, payout_multiplier = True, 1.96
                elif selection == "green" and number == 5:
                    won, payout_multiplier = True, 1.5
                elif selection == "red" and number in (2, 4, 6, 8):
                    won, payout_multiplier = True, 1.96
                elif selection == "red" and number == 0:
                    won, payout_multiplier = True, 1.5
                elif selection == "violet" and number in (0, 5):
                    won, payout_multiplier = True, 4.5
            elif select_type == "number" and int(selection) == number:
                won, payout_multiplier = True, 8.82
            elif select_type == "size" and selection == size:
                won, payout_multiplier = True, 1.96

            if won:
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
