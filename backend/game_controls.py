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
    enabled     false takes the game offline with a clear message
    min_stake / max_stake   per-game limits, inside the global ones

Only `enabled` and the stake limits are enforced before the wallet is touched.
`mode`, `forced` and `house_bias` are applied by each game inside its own
resolve step, because only the game knows what a "win" looks like.
"""

from typing import Callable

from fastapi import HTTPException

from games_core import MAX_STAKE, MIN_STAKE, secure_unit
from settings_store import get_setting, set_setting

# Every game the dashboard can steer, with the tokens its manual mode accepts.
GAMES = {
    "slots": {
        "label": "Lucky Reels",
        "forced_choices": ["lose", "small_win", "big_win", "jackpot"],
    },
    "megaslots": {
        "label": "Mega Slots",
        "forced_choices": ["lose", "small_win", "big_win", "jackpot"],
    },
    "roulette": {
        "label": "Roulette",
        # Plus any pocket number 0-36, typed straight into the box.
        "forced_choices": ["lose", "win"],
    },
    "aviator": {
        "label": "Aviator",
        # A multiplier typed in as text, e.g. "1.00" for an instant bust.
        "forced_choices": ["1.00", "1.50", "2.00", "5.00", "10.00"],
    },
    "wingo": {
        "label": "WinGo",
        "forced_choices": [str(n) for n in range(10)],
    },
    "chicken-road": {
        "label": "Chicken Road",
        "forced_choices": ["lose", "win"],
    },
    "lottery": {"label": "Daily Lottery", "forced_choices": []},
}

DEFAULTS = {
    "mode": "auto",
    "forced": "",
    "house_bias": "0",
    "enabled": "true",
    "min_stake": str(MIN_STAKE),
    "max_stake": str(MAX_STAKE),
}


def _key(game: str, field: str) -> str:
    return f"game:{game}:{field}"


def get_controls(conn, game: str) -> dict:
    if game not in GAMES:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")
    raw = {field: get_setting(conn, _key(game, field), default)
           for field, default in DEFAULTS.items()}
    return {
        "game": game,
        "label": GAMES[game]["label"],
        "mode": raw["mode"] if raw["mode"] in ("auto", "manual") else "auto",
        "forced": raw["forced"],
        "house_bias": _clamp_bias(raw["house_bias"]),
        "enabled": raw["enabled"].lower() == "true",
        "min_stake": float(raw["min_stake"]),
        "max_stake": float(raw["max_stake"]),
        "forced_choices": GAMES[game]["forced_choices"],
    }


def get_all_controls(conn) -> list:
    return [get_controls(conn, game) for game in GAMES]


def _clamp_bias(value) -> float:
    try:
        bias = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, bias))


def save_controls(conn, game: str, payload: dict) -> dict:
    """Validate and persist. Unknown fields are ignored, not an error."""
    if game not in GAMES:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")

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
