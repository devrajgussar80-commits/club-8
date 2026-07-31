"""European roulette: single zero, 37 pockets.

The prototype in new-games/roulette-casino-game only let you pick numbers and
paid a flat 350. This keeps its wheel -- same pocket order, same red set, so
the ported wheel animation lands on the right slot -- but pays the real table
odds, which is where the house edge comes from:

    every bet on this table returns 36/37 of its stake on average -> RTP 97.3%

Because the edge is baked into the odds, no bet type needs its own fudge
factor, and adding a new one is safe as long as the payout is the standard
(36 / covered_pockets) - 1.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from game_controls import apply_bias, check_playable
from games_core import play_round, secure_unit

router = APIRouter(prefix="/api/games/roulette", tags=["games"])

GAME = "roulette"

# Physical pocket order on a European wheel, clockwise from zero. The frontend
# uses the same list to work out the stopping angle.
WHEEL_ORDER = [
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23,
    10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
]
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

MAX_BETS_PER_SPIN = 20


def _covers(bet_type: str, value: str) -> set:
    """The set of pockets a bet wins on. Empty set means the bet is invalid."""
    if bet_type == "straight":
        if not value.isdigit() or not 0 <= int(value) <= 36:
            return set()
        return {int(value)}
    if bet_type == "color":
        if value == "red":
            return set(RED_NUMBERS)
        if value == "black":
            return set(range(1, 37)) - RED_NUMBERS
        return set()
    if bet_type == "parity":
        if value == "even":
            return {n for n in range(1, 37) if n % 2 == 0}
        if value == "odd":
            return {n for n in range(1, 37) if n % 2 == 1}
        return set()
    if bet_type == "half":
        if value == "low":
            return set(range(1, 19))
        if value == "high":
            return set(range(19, 37))
        return set()
    if bet_type == "dozen":
        if value not in {"1", "2", "3"}:
            return set()
        start = (int(value) - 1) * 12 + 1
        return set(range(start, start + 12))
    if bet_type == "column":
        if value not in {"1", "2", "3"}:
            return set()
        return {n for n in range(1, 37) if n % 3 == int(value) % 3}
    return set()


def payout_multiple(covered: int) -> int:
    """Total return per unit staked, stake included. 36/covered, as at a table."""
    return 36 // covered


class Bet(BaseModel):
    bet_type: str
    value: str
    amount: float


class SpinRequest(BaseModel):
    bets: list[Bet]


@router.get("/table")
def table():
    return {
        "wheel_order": WHEEL_ORDER,
        "red_numbers": sorted(RED_NUMBERS),
        "bet_types": {
            "straight": {"label": "Number", "pays": "35:1"},
            "color": {"label": "Red / Black", "pays": "1:1"},
            "parity": {"label": "Odd / Even", "pays": "1:1"},
            "half": {"label": "1-18 / 19-36", "pays": "1:1"},
            "dozen": {"label": "Dozen", "pays": "2:1"},
            "column": {"label": "Column", "pays": "2:1"},
        },
    }


@router.post("/spin")
def spin(req: SpinRequest, current_user: dict = Depends(get_current_user)):
    if not req.bets:
        raise HTTPException(status_code=400, detail="Place at least one bet.")
    if len(req.bets) > MAX_BETS_PER_SPIN:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_BETS_PER_SPIN} bets per spin."
        )

    placed = []
    for bet in req.bets:
        covered = _covers(bet.bet_type, str(bet.value))
        if not covered:
            raise HTTPException(
                status_code=400, detail=f"Invalid bet: {bet.bet_type} {bet.value}"
            )
        if bet.amount <= 0:
            raise HTTPException(status_code=400, detail="Every bet needs a positive amount.")
        placed.append((bet, covered))

    total_stake = round(sum(bet.amount for bet, _ in placed), 2)

    conn = get_db_connection()
    try:
        controls = check_playable(conn, GAME, total_stake)
    finally:
        conn.close()

    covered_by_any = set().union(*(covered for _, covered in placed))
    losing_pockets = [p for p in WHEEL_ORDER if p not in covered_by_any]
    winning_pockets = [p for p in WHEEL_ORDER if p in covered_by_any]

    def pick_pocket() -> int:
        random_pocket = WHEEL_ORDER[int(secure_unit() * len(WHEEL_ORDER))]
        if controls["mode"] != "manual" or not controls["forced"]:
            return random_pocket
        forced = controls["forced"]
        if forced.isdigit():
            return int(forced)
        # "lose"/"win" are relative to the bets actually on the table, so they
        # fall back to a random pocket when the board leaves no such choice
        # (a player covering every number cannot be made to lose).
        pool = losing_pockets if forced == "lose" else winning_pockets
        return pool[int(secure_unit() * len(pool))] if pool else random_pocket

    def settle(pocket: int):
        results = []
        payout = 0.0
        for bet, covered in placed:
            won = pocket in covered
            # Rounding per bet, not on the total, so what the player is told
            # each bet returned adds up to what the wallet actually receives.
            ret = round(bet.amount * payout_multiple(len(covered)), 2) if won else 0.0
            payout += ret
            results.append(
                {
                    "bet_type": bet.bet_type,
                    "value": bet.value,
                    "amount": bet.amount,
                    "won": won,
                    "returned": ret,
                }
            )
        colour = "green" if pocket == 0 else ("red" if pocket in RED_NUMBERS else "black")
        return round(payout, 2), {
            "pocket": pocket,
            "pocket_index": WHEEL_ORDER.index(pocket),
            "colour": colour,
            "bets": results,
        }

    def resolve(stake):
        payout, outcome = settle(pick_pocket())

        def as_loss():
            if not losing_pockets:
                return payout, outcome
            return settle(losing_pockets[int(secure_unit() * len(losing_pockets))])

        biased = apply_bias(controls, payout, as_loss)
        return biased if biased is not None else (payout, outcome)

    def _unused_legacy_resolve(stake):
        pocket = WHEEL_ORDER[int(secure_unit() * len(WHEEL_ORDER))]
        results = []
        payout = 0.0
        for bet, covered in placed:
            won = pocket in covered
            # Rounding per bet, not on the total, so what the player is told
            # each bet returned adds up to what the wallet actually receives.
            ret = round(bet.amount * payout_multiple(len(covered)), 2) if won else 0.0
            payout += ret
            results.append(
                {
                    "bet_type": bet.bet_type,
                    "value": bet.value,
                    "amount": bet.amount,
                    "won": won,
                    "returned": ret,
                }
            )
        colour = "green" if pocket == 0 else ("red" if pocket in RED_NUMBERS else "black")
        return round(payout, 2), {
            "pocket": pocket,
            "pocket_index": WHEEL_ORDER.index(pocket),
            "colour": colour,
            "bets": results,
        }

    return play_round(current_user, GAME, total_stake, resolve)
