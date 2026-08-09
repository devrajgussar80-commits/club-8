"""The signup-bonus run: a boosted win rate that stops at a per-account ceiling.

Every account opens with `signup_bonus` in the wallet and a `luck_target`
drawn once, uniformly somewhere in [target_min, target_max]. Until the run
reaches that target, `win_rate`% of the account's single-player rounds are
wins. The rate is a rate, not a floor: a round the draw says should lose is
re-drawn as a loss just as one it says should win is re-drawn as a win, so the
number the dashboard shows is the number the player actually gets.

Progress along the run is `luck_progress` -- the bonus, plus the net profit of
every single-player round since. When it first reaches `luck_target` the run
is over, `luck_done` is set, and it never restarts: an account rides from the
bonus up to its own target exactly once, and afterwards plays the games' own
published odds. Withdrawing and starting again does not re-open it, which is
the whole point of storing progress rather than reading the wallet.

Nothing above `target_max` is ever handed out by the boost either. A re-drawn
win that would carry the run past that ceiling is declined and the natural
losing round stands, so the run lands inside [target_min, target_max] and not
beyond it.

Two things are deliberately outside all of this:

* Shared-round games -- WinGo, Dice, Fish vs Tiger, Vortex, XAviator -- deal
  one result to everyone at the table. A round cannot be a win for one player
  and a loss for the rest, so those rounds are neither steered nor counted
  towards the run. `SOLO_GAMES` is the list that can be.
* Team accounts (`team_win_rate` above 0) are set by hand in the dashboard.
  Their rate wins over the platform one and their run has no ceiling, because
  an admin who typed 80% meant 80% for as long as they leave it there.
"""

import secrets

from settings_store import get_settings


def secure_unit() -> float:
    """Uniform float in [0, 1) from the OS CSPRNG.

    The same draw as `games_core.secure_unit`, spelled out again rather than
    imported: games_core is what calls this module, and importing it back
    would make the two import each other.
    """
    return secrets.randbits(53) / float(1 << 53)



# Games settled per player, which is what makes a per-player win rate possible.
# `mines` and `chicken` settle through games_core.settle_held; the other three
# through games_core.play_round.
SOLO_GAMES = frozenset({"slots", "megaslots", "roulette", "mines", "chicken"})

# Seeded into system_settings, so all of this is tunable from the dashboard
# without a deploy. Read on every settled round, so keep the list short.
DEFAULTS = {
    "luck:enabled": "true",
    "luck:win_rate": "60",
    "luck:signup_bonus": "100",
    "luck:target_min": "1700",
    "luck:target_max": "3000",
}

# The user columns a run is built from. Selected alongside the balance in the
# same locked read, so tracking a run costs no extra round trip.
COLUMNS = "team_win_rate, luck_target, luck_progress, luck_done"


def load_settings(conn) -> dict:
    return from_raw(get_settings(conn, DEFAULTS.keys()))


def from_raw(raw: dict) -> dict:
    """Parse and clamp the stored strings. Split out from `load_settings` so a
    caller that has already read every setting -- the admin dashboard reads
    them all in one query -- does not go back for these five."""

    def number(key, low, high):
        try:
            value = float(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            value = float(DEFAULTS[key])
        return max(low, min(high, value))

    low = number("luck:target_min", 0, 10_000_000)
    high = number("luck:target_max", 0, 10_000_000)
    return {
        "enabled": str(raw.get("luck:enabled", "true")).lower() == "true",
        "win_rate": number("luck:win_rate", 0, 100),
        "signup_bonus": number("luck:signup_bonus", 0, 1_000_000),
        # A max typed below the min would make the ceiling unreachable and
        # every run finish on its first round.
        "target_min": min(low, high),
        "target_max": max(low, high),
    }


def draw_target(settings: dict) -> float:
    """One account's ceiling, uniform across the configured band."""
    low, high = settings["target_min"], settings["target_max"]
    return round(low + secure_unit() * (high - low), 2)


def counts(game: str) -> bool:
    return game in SOLO_GAMES


class Run:
    """Where one account stands on its bonus run.

    Built from a user row rather than from a query of its own, so the caller
    reads these columns in the same statement it already needed -- inside
    `play_round` that is the `FOR UPDATE` select which locks the wallet, and
    the run then advances in the same transaction as the money.
    """

    def __init__(self, row, settings: dict):
        row = dict(row) if row is not None else {}
        self.settings = settings
        self.team_rate = float(row.get("team_win_rate") or 0)
        self.target = row.get("luck_target")
        self.progress = row.get("luck_progress")
        self.done = bool(row.get("luck_done"))

    @property
    def bounded(self) -> bool:
        """False for team accounts, whose rate an admin set on purpose."""
        return self.team_rate <= 0

    @property
    def open(self) -> bool:
        """True while the boost still applies to this account."""
        if not self.bounded:
            return True
        return self.settings["enabled"] and not self.done

    @property
    def rate(self) -> float:
        """Win rate (%) this account's single-player rounds are held to."""
        if not self.bounded:
            return self.team_rate
        return self.settings["win_rate"] if self.open else 0.0

    def start(self) -> None:
        """Draw this account's target the first time it plays, if registration
        did not (every account made before this feature shipped)."""
        if self.target is None:
            self.target = draw_target(self.settings)
        if self.progress is None:
            self.progress = self.settings["signup_bonus"]

    def allows(self, stake: float, payout: float) -> bool:
        """Would a re-drawn win of `payout` keep the run under the ceiling?"""
        if not self.bounded:
            return True
        return (self.progress or 0) + payout - stake <= self.settings["target_max"]

    def advance(self, stake: float, payout: float) -> None:
        """Book one settled round against the run."""
        self.start()
        self.progress = round(self.progress + payout - stake, 2)
        if self.bounded and self.progress >= self.target:
            self.done = True

    @property
    def row(self) -> tuple:
        """(target, progress, done) in the order `SET_COLUMNS` expects."""
        return (self.target, self.progress, 1 if self.done else 0)


# Folded into the UPDATE that moves the balance, so a settled round writes the
# wallet and the run's position together or not at all.
SET_COLUMNS = "luck_target = ?, luck_progress = ?, luck_done = ?"


def steer(run: Run, stake: float, natural, redraw_win=None, redraw_loss=None):
    """Hold one single-player round to the account's win rate.

    `natural` is the `(payout, outcome)` the game drew for itself. A round the
    rate says should win, and did not, is re-drawn with `redraw_win(stake)`;
    one it says should lose, and did not, with `redraw_loss()`. Either may
    return None -- some boards have no such result to draw, a roulette player
    covering every pocket cannot be made to lose -- and then the natural round
    stands.

    Both re-draws hand back a real result for the game, never a bare number,
    so what the player is shown and what the wallet receives still agree.
    """
    payout, _ = natural
    rate = run.rate
    if rate <= 0:
        return natural

    won = payout > stake
    # The ceiling binds the game's own draws too, not just the re-drawn ones.
    # Without this a natural win on the last round of a run could carry a
    # wallet well past target_max -- rarely, but "rarely" is not "never", and
    # never is what the ceiling is for. Once the run is over this branch is
    # unreachable (rate is 0 by then) and the published odds apply untouched.
    if won and not run.allows(stake, payout) and redraw_loss is not None:
        drawn = redraw_loss()
        if drawn is not None:
            return drawn

    if secure_unit() * 100 < rate:
        if won or redraw_win is None:
            return natural
        drawn = redraw_win(stake)
        # Declining the win here is what keeps the ceiling: the round simply
        # stays the loss it already was, rather than being paid a trimmed
        # number that the reels on screen would not add up to.
        if drawn is None or not run.allows(stake, drawn[0]):
            return natural
        return drawn

    if not won or redraw_loss is None:
        return natural
    drawn = redraw_loss()
    return drawn if drawn is not None else natural


def rescues(run: Run) -> bool:
    """Should a bust in a step game be turned back into a safe move?

    Mines and Chicken Road leave the player holding the decision of when to
    stop, so the server cannot declare a round won -- what it can do is not
    end it. The first bust of a round is re-drawn into a safe tile or lane at
    the account's win rate; from there the player is in profit and banking it
    is theirs to get right. Once per round, which is why the caller records a
    `rescued` flag in the round rather than asking again on the next tap.
    """
    rate = run.rate
    return rate > 0 and secure_unit() * 100 < rate
