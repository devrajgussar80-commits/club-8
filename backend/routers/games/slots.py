"""Lucky Reels: 3 reels x 3 rows, five paylines.

Ported from the React prototype in new-games/lucky-reels. The symbol weights
and 3-of-a-kind payouts are the prototype's; what changed is that the spin is
drawn here instead of in the browser, and the stake is real money.

House edge, exactly:
    per line   E = sum(p_symbol^3 * payout_symbol) = 0.17275 x stake
    five lines E = 0.8637 x stake   ->  RTP 86.4%
Change a weight or a payout and that number moves -- recompute it before
shipping, the test suite asserts the RTP stays inside a band.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from game_controls import apply_bias, capped_win, check_playable, under_cap
from games_core import play_round, secure_unit, weighted_pick

router = APIRouter(prefix="/api/games/slots", tags=["games"])

GAME = "slots"

# id -> (emoji, payout multiple of stake for 3-of-a-kind, reel weight)
SYMBOLS = {
    "cherry": ("🍒", 2, 30),
    "lemon": ("🍋", 3, 26),
    "watermelon": ("🍉", 5, 20),
    "bell": ("🔔", 10, 12),
    "star": ("⭐", 20, 7),
    "diamond": ("💎", 50, 3),
    "seven": ("7️⃣", 100, 2),
}

WEIGHTS = {key: value[2] for key, value in SYMBOLS.items()}

# [reel, row] triples. Reels are columns, rows are top/middle/bottom.
PAYLINES = [
    ("CENTER", [(0, 1), (1, 1), (2, 1)]),
    ("TOP", [(0, 0), (1, 0), (2, 0)]),
    ("BOTTOM", [(0, 2), (1, 2), (2, 2)]),
    ("DIAGONAL ↘", [(0, 0), (1, 1), (2, 2)]),
    ("DIAGONAL ↗", [(0, 2), (1, 1), (2, 0)]),
]


class SpinRequest(BaseModel):
    amount: float


def spin_reels() -> list:
    """Three reels of three symbols each, every position drawn independently."""
    return [[weighted_pick(WEIGHTS) for _ in range(3)] for _ in range(3)]


def evaluate(reels: list, stake: float):
    """Return (payout, winning lines) for a grid."""
    lines = []
    payout = 0.0
    for name, positions in PAYLINES:
        symbols = [reels[reel][row] for reel, row in positions]
        if symbols[0] == symbols[1] == symbols[2]:
            line_payout = round(SYMBOLS[symbols[0]][1] * stake, 2)
            payout += line_payout
            lines.append(
                {
                    "line": name,
                    "symbol": symbols[0],
                    "positions": positions,
                    "payout": line_payout,
                }
            )
    return round(payout, 2), lines


# Which symbols a forced win lands on. Tiers, not exact payouts, so the admin
# picks the *size* of the win and the paytable still decides the number.
FORCED_TIERS = {
    "small_win": ["cherry", "lemon", "watermelon"],
    "big_win": ["bell", "star"],
    "jackpot": ["diamond", "seven"],
}


def losing_grid() -> list:
    """A grid with no winning line. Roughly 76% of random spins already lose,
    so this converges in a couple of draws."""
    for _ in range(200):
        reels = spin_reels()
        if not evaluate(reels, 1.0)[1]:
            return reels
    # Fallback that cannot pay: three different symbols down every row.
    return [["cherry"] * 3, ["lemon"] * 3, ["bell"] * 3]


def forced_grid(token: str) -> list:
    """Build the grid the admin asked for in manual mode."""
    if token == "lose" or token not in FORCED_TIERS:
        return losing_grid()
    tier = FORCED_TIERS[token]
    symbol = tier[int(secure_unit() * len(tier)) % len(tier)]
    reels = losing_grid()
    for reel in range(3):
        reels[reel][1] = symbol  # the centre payline
    return reels


def result(reels: list, stake: float):
    """`(payout, outcome)` for a grid, the shape every caller settles with."""
    payout, lines = evaluate(reels, stake)
    return payout, {"reels": reels, "lines": lines}


@router.get("/paytable")
def paytable():
    return {
        "symbols": [
            {"id": key, "emoji": emoji, "payout": payout, "weight": weight}
            for key, (emoji, payout, weight) in SYMBOLS.items()
        ],
        "paylines": [{"line": name, "positions": positions} for name, positions in PAYLINES],
    }


@router.post("/spin")
def spin(req: SpinRequest, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        controls = check_playable(conn, GAME, round(float(req.amount or 0), 2))
    finally:
        conn.close()

    def redraw_loss():
        return 0.0, {"reels": losing_grid(), "lines": []}

    def resolve(stake):
        if controls["mode"] == "manual" and controls["forced"]:
            # A hand-forced result is the admin's own call, so the per-round
            # cap does not second-guess it.
            payout, outcome = result(forced_grid(controls["forced"]), stake)
        else:
            payout, outcome = under_cap(
                controls, stake, lambda: result(spin_reels(), stake), redraw_loss
            )

        # Only ever turns a win into a loss, never the other way round.
        biased = apply_bias(controls, payout, redraw_loss)
        return biased if biased is not None else (payout, outcome)

    def redraw_win(stake):
        # A genuine win at the player's win rate: a forced small-win grid,
        # evaluated normally so the reels shown and the payout paid agree,
        # and held to the same per-round cap as a natural spin.
        return capped_win(controls, stake, lambda: result(forced_grid("small_win"), stake))

    return play_round(
        current_user, GAME, req.amount, resolve,
        redraw_win=redraw_win, redraw_loss=redraw_loss,
    )
