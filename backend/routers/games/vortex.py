"""Vortex: a 24-segment multiplier wheel. Shared 30-second round.

The player backs a multiplier; the wheel stops on one segment and that
multiplier pays. The wheel is built from the payouts rather than the other way
round, so every choice is worth the same 98%:

    segments of a value c, out of N=24   ->   pays 0.98 * N / c

      12 x  1.96x      (half the wheel)
       6 x  3.92x
       3 x  7.84x
       2 x 11.76x
       1 x 23.52x
      --
      24 segments

Backing the rare 23.52x is neither smarter nor worse than the common 1.96x,
which is the same promise the dice and roulette tables make. Picking round
numbers like 2x/5x/10x instead would quietly hand each segment a different
edge, and the widest one would be the one that looks most attractive.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from deps import get_current_user
from games_core import secure_unit
from shared_rounds import RoundGame

router = APIRouter(prefix="/api/games/vortex", tags=["games"])

GAME = "vortex"
HOUSE = 0.98

# (multiplier, how many segments carry it). Must sum to SEGMENTS.
LAYOUT = [(1.96, 12), (3.92, 6), (7.84, 3), (11.76, 2), (23.52, 1)]
SEGMENTS = sum(count for _, count in LAYOUT)
assert SEGMENTS == 24, SEGMENTS
for value, count in LAYOUT:
    # The wheel and the payouts must not be able to drift apart in an edit.
    assert abs(value - HOUSE * SEGMENTS / count) < 0.01, (value, count)

# Laid out so the big multipliers sit apart rather than in a block -- a wheel
# with its rare segments bunched together looks rigged even when it is not.
WHEEL = [
    1.96, 3.92, 1.96, 7.84, 1.96, 3.92, 1.96, 11.76,
    1.96, 3.92, 1.96, 7.84, 1.96, 3.92, 1.96, 23.52,
    1.96, 3.92, 1.96, 7.84, 1.96, 3.92, 1.96, 11.76,
]
assert len(WHEEL) == SEGMENTS
for value, count in LAYOUT:
    assert WHEEL.count(value) == count, (value, WHEEL.count(value), count)

CHOICES = [value for value, _ in LAYOUT]


class Vortex(RoundGame):
    def selections(self):
        return {f"{value:g}": f"{value:g}x" for value in CHOICES}

    def _spin_to(self, value: float) -> dict:
        indexes = [i for i, v in enumerate(WHEEL) if v == value]
        index = indexes[int(secure_unit() * len(indexes))]
        return {"index": index, "multiplier": WHEEL[index]}

    def decide(self, bets, controls) -> dict:
        forced = (controls.get("forced") or "").strip()
        if controls.get("mode") == "manual" and forced in self.selections():
            return self._spin_to(float(forced))

        index = int(secure_unit() * SEGMENTS)
        outcome = {"index": index, "multiplier": WHEEL[index]}

        bias = controls.get("house_bias", 0)
        if bets and bias > 0 and secure_unit() * 100 < bias:
            cost = {
                value: round(
                    sum(
                        float(b["amount"]) * self.pays(b["selection"], {"multiplier": value})
                        for b in bets
                    ),
                    2,
                )
                for value in CHOICES
            }
            return self._spin_to(min(cost, key=cost.get))

        return outcome

    def pays(self, selection: str, outcome: dict) -> float:
        return outcome["multiplier"] if float(selection) == outcome["multiplier"] else 0.0


game = Vortex(GAME, period_code="VX30")


class BetRequest(BaseModel):
    selection: str
    amount: float


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    return {**game.state(current_user), "wheel": WHEEL, "choices": CHOICES}


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    return game.place(current_user, req.selection, req.amount)
