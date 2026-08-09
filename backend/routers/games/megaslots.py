"""Mega Slots: 5 reels x 3 rows, nine paylines.

Ported from new-games/slot-machine-game. Two things changed on the way in.

1. The prototype paid flat coin amounts (a five-star line paid 10000 whatever
   you staked). Here the table is a multiple of the *line* stake, and the
   line stake is `stake / 9`, so the payout scales with the bet.
2. The prototype's checkWins could pay the same payline three times over --
   once for the 5-match, again for a 4-match inside it, again for a 3-match.
   This pays each payline once, for the longest run from the leftmost reel,
   which is how a real left-to-right slot works.

Tuned to:
    RTP 90.4%   |   hit rate 11.3% of spins   |   top win 420x stake
Those numbers come from the exact formula, not simulation: a line pays when
the first k reels match, so P(exactly k) = p^k * (1-p) for k in {3, 4} and
p^5 for k = 5. Retune if you touch WEIGHTS or PAYOUT_TABLE.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from game_controls import apply_bias, capped_win, check_playable, under_cap
from games_core import play_round, secure_unit, weighted_pick

router = APIRouter(prefix="/api/games/megaslots", tags=["games"])

GAME = "megaslots"

REELS = 5
ROWS = 3

WEIGHTS = {
    "cherry": 15,
    "lemon": 15,
    "orange": 12,
    "plum": 12,
    "grape": 10,
    "bell": 10,
    "melon": 8,
    "diamond": 7,
    "lucky7": 5,
    "gold": 3,
    "crown": 2,
    "star": 1,
}

EMOJI = {
    "cherry": "🍒",
    "lemon": "🍋",
    "orange": "🍊",
    "plum": "🍑",
    "grape": "🍇",
    "bell": "🔔",
    "melon": "🍉",
    "diamond": "💎",
    "lucky7": "7️⃣",
    "gold": "🏆",
    "crown": "👑",
    "star": "⭐",
}

# Multiple of the line stake for a run of 3, 4 or 5 from the left.
PAYOUT_TABLE = {
    "cherry": {3: 35, 4: 100, 5: 340},
    "lemon": {3: 35, 4: 110, 5: 380},
    "orange": {3: 40, 4: 135, 5: 460},
    "plum": {3: 50, 4: 170, 5: 590},
    "grape": {3: 60, 4: 210, 5: 750},
    "bell": {3: 75, 4: 250, 5: 920},
    "melon": {3: 100, 4: 335, 5: 1260},
    "diamond": {3: 135, 4: 460, 5: 1680},
    "lucky7": {3: 185, 4: 670, 5: 2350},
    "gold": {3: 295, 4: 1090, 5: 2940},
    "crown": {3: 420, 4: 1680, 5: 3360},
    "star": {3: 670, 4: 2520, 5: 3780},
}

# Row index per reel, left to right. The prototype listed two pairs of
# identical paylines; these nine are distinct, so nine lines really pay.
PAYLINES = [
    ("TOP", [0, 0, 0, 0, 0]),
    ("MIDDLE", [1, 1, 1, 1, 1]),
    ("BOTTOM", [2, 2, 2, 2, 2]),
    ("V", [0, 1, 2, 1, 0]),
    ("Λ", [2, 1, 0, 1, 2]),
    ("Z", [0, 0, 1, 2, 2]),
    ("S", [2, 2, 1, 0, 0]),
    ("ZIG", [1, 0, 1, 2, 1]),
    ("ZAG", [1, 2, 1, 0, 1]),
]


class SpinRequest(BaseModel):
    amount: float


def spin_reels() -> list:
    """`reels[reel][row]`, every position drawn independently."""
    return [[weighted_pick(WEIGHTS) for _ in range(ROWS)] for _ in range(REELS)]


def evaluate(reels: list, stake: float):
    line_stake = stake / len(PAYLINES)
    wins = []
    payout = 0.0

    for name, rows in PAYLINES:
        symbols = [reels[reel][row] for reel, row in enumerate(rows)]
        first = symbols[0]
        run = 1
        for symbol in symbols[1:]:
            if symbol != first:
                break
            run += 1
        if run < 3:
            continue
        multiple = PAYOUT_TABLE[first][run]
        line_payout = round(multiple * line_stake, 2)
        payout += line_payout
        wins.append(
            {
                "line": name,
                "rows": rows,
                "symbol": first,
                "count": run,
                "payout": line_payout,
            }
        )

    return round(payout, 2), wins


FORCED_TIERS = {
    "small_win": ["cherry", "lemon", "orange", "plum"],
    "big_win": ["bell", "melon", "diamond"],
    "jackpot": ["lucky7", "gold", "crown", "star"],
}


def losing_grid() -> list:
    """A grid where no payline has three matching symbols from the left."""
    for _ in range(200):
        reels = spin_reels()
        if not evaluate(reels, 1.0)[1]:
            return reels
    # Fallback: reel 2 never matches reel 1, so no run can reach three.
    return [["cherry"] * ROWS, ["lemon"] * ROWS] + [["bell"] * ROWS] * (REELS - 2)


def forced_grid(token: str, run: int = REELS) -> list:
    """Manual mode. A forced win runs the symbol along the MIDDLE payline.

    `run` is how many reels from the left carry it. Five is the full line the
    dashboard's tiers mean; three is the shortest run that pays anything, and
    is what a re-drawn win uses -- across five reels even the cheapest symbol
    returns 340 times the *line* stake, which is 38 times the round's, and a
    "small win" that pays 38x is not a small win.
    """
    if token == "lose" or token not in FORCED_TIERS:
        return losing_grid()
    tier = FORCED_TIERS[token]
    symbol = tier[int(secure_unit() * len(tier)) % len(tier)]
    run = max(3, min(run, REELS))
    reels = losing_grid()
    for reel in range(run):
        reels[reel][1] = symbol
    # Stop the run where it was asked to stop: the losing grid underneath may
    # already carry the same symbol on the next reel, which would quietly pay
    # the longer line instead.
    if run < REELS and reels[run][1] == symbol:
        reels[run][1] = next(key for key in WEIGHTS if key != symbol)
    return reels


def result(reels: list, stake: float):
    """`(payout, outcome)` for a grid, the shape every caller settles with."""
    payout, wins = evaluate(reels, stake)
    return payout, {"reels": reels, "wins": wins}


@router.get("/paytable")
def paytable():
    return {
        "symbols": [
            {"id": key, "emoji": EMOJI[key], "weight": weight, "pays": PAYOUT_TABLE[key]}
            for key, weight in WEIGHTS.items()
        ],
        "paylines": [{"line": name, "rows": rows} for name, rows in PAYLINES],
        "reels": REELS,
        "rows": ROWS,
    }


@router.post("/spin")
def spin(req: SpinRequest, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        controls = check_playable(conn, GAME, round(float(req.amount or 0), 2))
    finally:
        conn.close()

    def redraw_loss():
        return 0.0, {"reels": losing_grid(), "wins": []}

    def resolve(stake):
        if controls["mode"] == "manual" and controls["forced"]:
            # A hand-forced result is the admin's own call, so the per-round
            # cap does not second-guess it.
            payout, outcome = result(forced_grid(controls["forced"]), stake)
        else:
            payout, outcome = under_cap(
                controls, stake, lambda: result(spin_reels(), stake), redraw_loss
            )

        biased = apply_bias(controls, payout, redraw_loss)
        return biased if biased is not None else (payout, outcome)

    def redraw_win(stake):
        # A genuine win at the player's win rate: the shortest paying run of a
        # cheap symbol, evaluated normally so reels and payout still agree.
        # Still capped -- writing a symbol across the middle row can light up
        # the diagonal lines that cross it, and three of those together reach
        # far past what a "small win" should pay.
        return capped_win(
            controls, stake, lambda: result(forced_grid("small_win", run=3), stake)
        )

    return play_round(
        current_user, GAME, req.amount, resolve,
        redraw_win=redraw_win, redraw_loss=redraw_loss,
    )
