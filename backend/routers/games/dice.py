"""Dice Roll: one die, six faces, bet on what comes up.

Three ways to bet, all paying the same 98% back:

    number      one face          fair 6.00x -> pays 5.88x
    parity      odd / even        fair 2.00x -> pays 1.96x
    half        low 1-3 / high 4-6  fair 2.00x -> pays 1.96x

The 0.98 factor is the whole house edge, and it is applied once, to the fair
odds. That is the same convention WinGo already uses (1.96 on colour, 8.82 on
a number), so a player who understands one table understands this one. Do not
"balance" a bet type by shaving its multiplier further -- every bet here is
meant to be equally good, which is what makes covering more faces pointless
rather than punished.

Payouts are the TOTAL returned, stake included: winning 5.88x on ₹10 puts
₹58.80 back in the wallet, of which ₹48.80 is profit.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from deps import get_current_user
from game_controls import apply_bias, check_playable
from games_core import play_round, secure_unit

router = APIRouter(prefix="/api/games/dice", tags=["games"])

GAME = "dice"

FACES = [1, 2, 3, 4, 5, 6]
# Returned per unit staked, stake included.
NUMBER_PAYS = 5.88
EVEN_MONEY_PAYS = 1.96

MAX_BETS_PER_ROLL = 8


def _covers(bet_type: str, value: str) -> set:
    """Which faces a bet wins on. Empty means the bet is not valid."""
    if bet_type == "number":
        return {int(value)} if value.isdigit() and int(value) in FACES else set()
    if bet_type == "parity":
        if value == "odd":
            return {1, 3, 5}
        if value == "even":
            return {2, 4, 6}
        return set()
    if bet_type == "half":
        if value == "low":
            return {1, 2, 3}
        if value == "high":
            return {4, 5, 6}
        return set()
    return set()


def payout_multiple(bet_type: str) -> float:
    return NUMBER_PAYS if bet_type == "number" else EVEN_MONEY_PAYS


class Bet(BaseModel):
    bet_type: str
    value: str
    amount: float


class RollRequest(BaseModel):
    bets: list[Bet]


@router.get("/table")
def table():
    return {
        "faces": FACES,
        "bet_types": {
            "number": {"label": "Exact number", "pays": f"{NUMBER_PAYS}x"},
            "parity": {"label": "Odd / Even", "pays": f"{EVEN_MONEY_PAYS}x"},
            "half": {"label": "1-3 / 4-6", "pays": f"{EVEN_MONEY_PAYS}x"},
        },
    }


@router.post("/roll")
def roll(req: RollRequest, current_user: dict = Depends(get_current_user)):
    if not req.bets:
        raise HTTPException(status_code=400, detail="Place at least one bet.")
    if len(req.bets) > MAX_BETS_PER_ROLL:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_BETS_PER_ROLL} bets per roll."
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
    losing_faces = [face for face in FACES if face not in covered_by_any]
    winning_faces = [face for face in FACES if face in covered_by_any]

    def pick(pool):
        return pool[int(secure_unit() * len(pool))]

    def pick_face() -> int:
        fair = pick(FACES)
        if controls["mode"] != "manual" or not controls["forced"]:
            return fair
        forced = controls["forced"]
        if forced.isdigit() and int(forced) in FACES:
            return int(forced)
        # "lose"/"win" are relative to what is actually on the table, so they
        # fall back to a fair roll when the board leaves no such face -- a
        # player covering every face cannot be made to lose.
        pool = losing_faces if forced == "lose" else winning_faces
        return pick(pool) if pool else fair

    def settle(face: int):
        results = []
        payout = 0.0
        for bet, covered in placed:
            won = face in covered
            # Rounded per bet, so what each bet is shown to return adds up to
            # exactly what the wallet receives.
            ret = round(bet.amount * payout_multiple(bet.bet_type), 2) if won else 0.0
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
        return round(payout, 2), {
            "face": face,
            "parity": "odd" if face % 2 else "even",
            "half": "low" if face <= 3 else "high",
            "bets": results,
        }

    def resolve(stake):
        payout, outcome = settle(pick_face())

        def as_loss():
            if not losing_faces:
                return payout, outcome
            return settle(pick(losing_faces))

        biased = apply_bias(controls, payout, as_loss)
        return biased if biased is not None else (payout, outcome)

    return play_round(current_user, GAME, total_stake, resolve)
