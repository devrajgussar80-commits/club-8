"""One module per arcade game, each owning its own rules and payout table.

Shared money handling lives in `games_core`, never here.
"""

from routers.games import (
    aviator,
    chicken,
    dice,
    lottery,
    megaslots,
    mines,
    roulette,
    slots,
)

__all__ = [
    "aviator",
    "slots",
    "megaslots",
    "roulette",
    "dice",
    "chicken",
    "mines",
    "lottery",
]
