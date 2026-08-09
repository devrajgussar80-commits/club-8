"""Per-game admin controls: auto vs manual, forced outcomes, stake limits.

Every game already had its own hard-coded RNG and payout table. This adds one
layer above that, stored in ``system_settings`` under ``game:<name>:<field>``,
so the dashboard can steer a single game without a deploy.

    mode        "auto"   -- the game's own RNG decides, untouched
                "manual" -- `forced` decides, see each game's FORCED_CHOICES
    forced      game-specific token, only read when mode is "manual"
    house_bias  0-100. In auto mode, the percentage of *winning* rounds that
                are re-drawn as a loss. 0 leaves the published RTP intact;
                anything above 0 lowers it. Kept separate from `mode` so the
                usual state is "auto, bias 0" -- the honest one.
    max_win     the most one round may return, as a multiple of its own stake.
                0 is uncapped. This is what stops a small stake landing a very
                large number on a game whose paytable reaches into the
                hundreds of times the stake -- Mega Slots tops out at 420x, so
                a 500 rupee spin could return over 200,000 with no cap.
    enabled     false takes the game offline with a clear message
    min_stake / max_stake   per-game limits, inside the global ones

Only `enabled` and the stake limits are enforced before the wallet is touched.
`mode`, `forced`, `house_bias` and `max_win` are applied by each game inside
its own resolve step, because only the game knows what a "win" looks like.
"""

from typing import Callable

from fastapi import HTTPException

from games_core import MAX_STAKE, MIN_STAKE, secure_unit
from settings_store import set_setting

# Every game the dashboard can steer.
#
# `can_force` says whether mode/forced/house_bias actually reach the game. It
# is not decoration: a game whose result is decided in the browser cannot be
# steered from here, and showing an admin a switch that changes nothing is
# worse than showing no switch. Only `enabled` and the stake limits apply
# everywhere the server sees the round.
#
# `max_win` is each game's own starting cap, because what counts as an outsized
# return depends on the paytable behind it. Games whose top prize is already
# modest, or whose odds are the published ones players expect (roulette pays
# 35:1 on a straight number and would be misreported if that were trimmed),
# ship uncapped.
GAMES = {
    "slots": {
        "label": "Lucky Reels",
        "can_force": True,
        "max_win": 10,
        "forced_choices": ["lose", "small_win", "big_win", "jackpot"],
    },
    "megaslots": {
        "label": "Mega Slots",
        "can_force": True,
        "max_win": 10,
        "forced_choices": ["lose", "small_win", "big_win", "jackpot"],
    },
    "roulette": {
        "label": "Roulette",
        "can_force": True,
        # Uncapped: 36x on a straight number is the table's advertised price,
        # not an accident of the paytable.
        "max_win": 0,
        # Plus any pocket number 0-36, typed straight into the box.
        "forced_choices": ["lose", "win"],
    },
    "dice": {
        "label": "Dice Roll",
        "can_force": True,
        # Plus any face 1-6, typed straight into the box.
        "forced_choices": ["lose", "win"],
    },
    "fishtiger": {
        "label": "Fish vs Tiger",
        "can_force": True,
        "forced_choices": ["fish", "tiger", "tie"],
    },
    "vortex": {
        "label": "Vortex",
        "can_force": True,
        # The multipliers the wheel carries.
        "forced_choices": ["1.96", "3.92", "7.84", "11.76", "23.52"],
    },
    "aviator": {
        "label": "XAviator",
        "can_force": True,
        # A multiplier typed in as text, e.g. "1.00" for an instant bust.
        "forced_choices": ["1.00", "1.50", "2.00", "5.00", "10.00"],
    },
    "wingo": {
        "label": "WinGo",
        "can_force": True,
        "forced_choices": [str(n) for n in range(10)],
    },
    "lottery": {
        "label": "Daily Lottery",
        # The result is the admin's own winning-number entry on the lottery
        # desk, so there is nothing here to force.
        "can_force": False,
        "note": "Winner is picked on the lottery desk below.",
        "forced_choices": [],
    },
    # Chicken Road and Mines are step games: the stake is taken up front and
    # the player chooses when to stop, so there is no single result to force.
    # The cap and the stake limits still apply -- both settle on the server.
    # The key is "chicken", matching what the router writes to game_rounds;
    # it read "chicken-road" before, so its card was steering nothing.
    "chicken": {
        "label": "Chicken Road",
        "can_force": False,
        "max_win": 10,
        "note": "The player picks when to cash out, so there is no result to force.",
        "forced_choices": [],
    },
    "mines": {
        "label": "Mines",
        "can_force": False,
        "max_win": 10,
        "note": "The player picks when to cash out, so there is no result to force.",
        "forced_choices": [],
    },
}

DEFAULTS = {
    "mode": "auto",
    "forced": "",
    "house_bias": "0",
    "enabled": "true",
    "min_stake": str(MIN_STAKE),
    "max_stake": str(MAX_STAKE),
}

# Games that do not name their own starting cap are uncapped.
DEFAULT_MAX_WIN = 0.0
MAX_WIN_LIMIT = 10_000.0


def _defaults(game: str) -> dict:
    return {**DEFAULTS, "max_win": str(GAMES[game].get("max_win", DEFAULT_MAX_WIN))}


def _key(game: str, field: str) -> str:
    return f"game:{game}:{field}"


def load_raw(conn) -> dict:
    """Every ``game:*`` setting in one round trip.

    Reading them one key at a time meant 6 queries per game and 42 for the
    dashboard, which over a network database took ~20 seconds to answer. One
    query is also what keeps `check_playable` cheap on the betting hot path.
    """
    rows = conn.execute(
        "SELECT key, value FROM system_settings WHERE key LIKE 'game:%'"
    ).fetchall()
    return {row["key"]: str(row["value"]) for row in rows}


def get_controls(conn, game: str, raw: dict | None = None) -> dict:
    if game not in GAMES:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")
    if raw is None:
        raw = load_raw(conn)
    raw = {field: raw.get(_key(game, field), default)
           for field, default in _defaults(game).items()}
    return {
        "game": game,
        "label": GAMES[game]["label"],
        "mode": raw["mode"] if raw["mode"] in ("auto", "manual") else "auto",
        "forced": raw["forced"],
        "house_bias": _clamp_bias(raw["house_bias"]),
        "max_win": _clamp_max_win(raw["max_win"]),
        "enabled": raw["enabled"].lower() == "true",
        "min_stake": float(raw["min_stake"]),
        "max_stake": float(raw["max_stake"]),
        "forced_choices": GAMES[game]["forced_choices"],
        "can_force": GAMES[game]["can_force"],
        "note": GAMES[game].get("note", ""),
    }


def get_all_controls(conn) -> list:
    raw = load_raw(conn)
    return [get_controls(conn, game, raw) for game in GAMES]


def _clamp_bias(value) -> float:
    try:
        bias = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, bias))


def _clamp_max_win(value) -> float:
    try:
        cap = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_WIN
    return max(0.0, min(MAX_WIN_LIMIT, cap))


def save_controls(conn, game: str, payload: dict) -> dict:
    """Validate and persist. Unknown fields are ignored, not an error."""
    if game not in GAMES:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")

    if not GAMES[game]["can_force"]:
        # Refuse rather than store-and-ignore: a saved "manual" that never
        # takes effect is the failure mode this flag exists to prevent.
        # `max_win` is not in this list -- every server-settled game honours a
        # cap, whether or not its result can be forced.
        for field in ("mode", "forced", "house_bias"):
            if payload.get(field) not in (None, "", 0, 0.0, "auto"):
                raise HTTPException(
                    status_code=400,
                    detail=f"{GAMES[game]['label']} cannot be steered from here. "
                           f"{GAMES[game].get('note', '')}".strip(),
                )

    if "mode" in payload:
        if payload["mode"] not in ("auto", "manual"):
            raise HTTPException(status_code=400, detail="Mode must be auto or manual.")
        set_setting(conn, _key(game, "mode"), payload["mode"])

    if "forced" in payload:
        forced = str(payload["forced"] or "").strip()
        if forced and not _forced_is_valid(game, forced):
            raise HTTPException(
                status_code=400, detail=f"'{forced}' is not a valid forced result for {game}."
            )
        set_setting(conn, _key(game, "forced"), forced)

    if "house_bias" in payload:
        set_setting(conn, _key(game, "house_bias"), str(_clamp_bias(payload["house_bias"])))

    if "max_win" in payload:
        set_setting(conn, _key(game, "max_win"), str(_clamp_max_win(payload["max_win"])))

    if "enabled" in payload:
        set_setting(conn, _key(game, "enabled"), "true" if payload["enabled"] else "false")

    if "min_stake" in payload or "max_stake" in payload:
        current = get_controls(conn, game)
        low = float(payload.get("min_stake", current["min_stake"]))
        high = float(payload.get("max_stake", current["max_stake"]))
        if low < MIN_STAKE or high > MAX_STAKE or low > high:
            raise HTTPException(
                status_code=400,
                detail=f"Stake limits must sit inside ₹{MIN_STAKE:.0f}-₹{MAX_STAKE:.0f} "
                       f"and min cannot exceed max.",
            )
        set_setting(conn, _key(game, "min_stake"), str(low))
        set_setting(conn, _key(game, "max_stake"), str(high))

    conn.commit()
    return get_controls(conn, game)


def _forced_is_valid(game: str, forced: str) -> bool:
    if forced in GAMES[game]["forced_choices"]:
        return True
    # Two games accept free-typed values beyond their preset list.
    if game == "roulette":
        return forced.isdigit() and 0 <= int(forced) <= 36
    if game == "dice":
        return forced.isdigit() and 1 <= int(forced) <= 6
    if game == "aviator":
        try:
            return 1.0 <= float(forced) <= 1000.0
        except ValueError:
            return False
    return False


# ------------------------------------------------------------ enforcement

def check_playable(conn, game: str, stake: float) -> dict:
    """Gate a round before any money moves. Returns the game's controls."""
    controls = get_controls(conn, game)
    if not controls["enabled"]:
        raise HTTPException(
            status_code=503, detail=f"{controls['label']} is temporarily unavailable."
        )
    if stake < controls["min_stake"] or stake > controls["max_stake"]:
        raise HTTPException(
            status_code=400,
            detail=f"{controls['label']} accepts ₹{controls['min_stake']:.0f}"
                   f"-₹{controls['max_stake']:.0f} per round.",
        )
    return controls


def win_cap(controls: dict, stake: float) -> float:
    """The most one round may return, in rupees. 0 means uncapped."""
    multiple = controls.get("max_win") or 0
    return round(stake * multiple, 2) if multiple > 0 else 0.0


def under_cap(controls: dict, stake: float, draw: Callable, floor: Callable, attempts: int = 40):
    """Draw a round until its payout fits inside `max_win`.

    Re-drawing rather than trimming the number is the whole point: a payout
    capped after the fact would leave five matching symbols on screen next to
    a figure that does not match them, and the player would be right to think
    they had been short-changed. `draw()` returns `(payout, outcome)` and is
    called again until one fits; `floor()` returns a round that cannot exceed
    the cap and settles the rare case where the draws keep overshooting.
    """
    cap = win_cap(controls, stake)
    payout, outcome = draw()
    if cap <= 0 or payout <= cap:
        return payout, outcome
    for _ in range(attempts):
        payout, outcome = draw()
        if payout <= cap:
            return payout, outcome
    return floor()


def capped_win(controls: dict, stake: float, draw: Callable, attempts: int = 40):
    """A winning draw that fits inside `max_win`, or None if none turned up.

    For the win-rate re-draws: a win handed out by the player's win rate obeys
    the same ceiling as one the game drew for itself. Declining is the honest
    answer when none fits -- there is nothing to trim without the screen and
    the wallet disagreeing -- and the caller then keeps its natural round.
    """
    cap = win_cap(controls, stake)
    for _ in range(attempts):
        payout, outcome = draw()
        if cap <= 0 or payout <= cap:
            return payout, outcome
    return None


def apply_bias(controls: dict, payout: float, redraw_loss: Callable):
    """In auto mode, re-draw `house_bias`% of winning rounds as losses.

    Returns `(payout, outcome)`. `redraw_loss()` must produce a genuinely
    losing round for that game -- this never fabricates a payout, it only
    replaces a win with a loss, so the bias can lower RTP and never raise it.
    """
    bias = controls["house_bias"]
    if payout <= 0 or bias <= 0:
        return None
    if secure_unit() * 100 >= bias:
        return None
    return redraw_loss()
