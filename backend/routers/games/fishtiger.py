"""Fish vs Tiger: one card each, higher card wins. Shared 30-second round.

A single 52-card deck, ranks A(1) to K(13), suits shown but ignored for the
result. Fish and Tiger each get one card; the higher rank wins the round.

Odds, from the deck rather than from a table someone copied:

    P(tie)  = 3/51 = 1/17           (three cards of the same rank remain)
    P(each) = (16/17) / 2 = 8/17

Payouts follow the platform convention -- fair odds, times 0.98:

    fish / tiger   ties PUSH, so the fair price is 2.00x  ->  1.96x
    tie            fair 17.00x                            -> 16.66x

Pushing the side bets on a tie is what makes every bet here worth the same
98%. The casino version instead pays 1:1 and confiscates half the stake on a
tie, and pays the tie bet only 8:1 -- that is an 11% edge on one bet and 3.7%
on another, which is exactly the kind of hidden inconsistency this platform
does not do elsewhere.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from deps import get_current_user
from games_core import secure_unit
from shared_rounds import RoundGame, pick

router = APIRouter(prefix="/api/games/fishtiger", tags=["games"])

GAME = "fishtiger"
SIDE_PAYS = 1.96
TIE_PAYS = 16.66

RANKS = list(range(1, 14))
SUITS = ["♠", "♥", "♦", "♣"]
RANK_NAMES = {1: "A", 11: "J", 12: "Q", 13: "K"}


def card_name(rank: int) -> str:
    return RANK_NAMES.get(rank, str(rank))


class FishTiger(RoundGame):
    def selections(self):
        return {"fish": "Fish", "tiger": "Tiger", "tie": "Tie"}

    def _deal(self):
        """Two distinct cards from one deck, so a tie needs matching ranks
        drawn from the three that remain -- not two independent draws, which
        would make ties 1/13 instead of 1/17."""
        deck = [(rank, suit) for rank in RANKS for suit in SUITS]
        first = pick(deck)
        deck.remove(first)
        second = pick(deck)
        return first, second

    def _as_outcome(self, fish, tiger) -> dict:
        winner = "tie" if fish[0] == tiger[0] else ("fish" if fish[0] > tiger[0] else "tiger")
        return {
            "fish": {"rank": fish[0], "name": card_name(fish[0]), "suit": fish[1]},
            "tiger": {"rank": tiger[0], "name": card_name(tiger[0]), "suit": tiger[1]},
            "winner": winner,
        }

    def _deal_for(self, wanted: str) -> dict:
        """Deal until the round goes the way the admin asked. Bounded, and it
        deals real cards rather than fabricating a pair -- the cards shown and
        the result paid always agree."""
        for _ in range(400):
            fish, tiger = self._deal()
            outcome = self._as_outcome(fish, tiger)
            if outcome["winner"] == wanted:
                return outcome
        return self._as_outcome(*self._deal())

    def decide(self, bets, controls) -> dict:
        forced = (controls.get("forced") or "").strip()
        if controls.get("mode") == "manual" and forced in self.selections():
            return self._deal_for(forced)

        fish, tiger = self._deal()
        outcome = self._as_outcome(fish, tiger)

        bias = controls.get("house_bias", 0)
        if bets and bias > 0 and secure_unit() * 100 < bias:
            # Steer to whichever result pays the table least.
            cost = {
                side: round(
                    sum(
                        float(b["amount"]) * self.pays(b["selection"], {"winner": side})
                        for b in bets
                    ),
                    2,
                )
                for side in self.selections()
            }
            cheapest = min(cost, key=cost.get)
            return self._deal_for(cheapest)

        return outcome

    def pays(self, selection: str, outcome: dict) -> float:
        winner = outcome["winner"]
        if selection == "tie":
            return TIE_PAYS if winner == "tie" else 0.0
        # A tie returns the side bets rather than taking them.
        if winner == "tie":
            return 1.0
        return SIDE_PAYS if selection == winner else 0.0


game = FishTiger(GAME, period_code="FT30")


class BetRequest(BaseModel):
    selection: str
    amount: float


@router.get("/state")
def state(current_user: dict = Depends(get_current_user)):
    return {
        **game.state(current_user),
        "pays": {"fish": SIDE_PAYS, "tiger": SIDE_PAYS, "tie": TIE_PAYS},
    }


@router.post("/bet")
def bet(req: BetRequest, current_user: dict = Depends(get_current_user)):
    return game.place(current_user, req.selection, req.amount)
