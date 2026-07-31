"""Admin analytics for every game on the platform.

Reads two ledgers, because the platform has two kinds of game:

* `game_rounds` -- the arcade titles and the lottery, settled in one shot.
* `bets`        -- WinGo, which settles per round when the timer expires.

Both are folded into the same shape so the dashboard renders one table.
GGR is stake minus payout: positive means the house is up.
"""

from fastapi import APIRouter, Depends

from database import get_db_connection
from deps import require_admin

router = APIRouter(prefix="/api/admin/games", tags=["admin"])

# Display names for anything that shows up in the ledgers.
GAME_LABELS = {
    "slots": "Lucky Reels",
    "megaslots": "Mega Slots",
    "roulette": "Roulette",
    "lottery": "Daily Lottery",
    "chicken-road": "Chicken Road",
    "aviator": "Aviator",
    "wingo": "WinGo",
}


def _shape(game: str, rounds, players, stake, payout) -> dict:
    stake = round(float(stake or 0), 2)
    payout = round(float(payout or 0), 2)
    return {
        "game": game,
        "label": GAME_LABELS.get(game, game.title()),
        "rounds": int(rounds or 0),
        "players": int(players or 0),
        "stake": stake,
        "payout": payout,
        "ggr": round(stake - payout, 2),
        # Undefined with no stake -- report None rather than a misleading 0%.
        "rtp": round(payout / stake * 100, 2) if stake else None,
    }


@router.get("/overview")
def overview(days: int = 7, _: bool = Depends(require_admin)):
    """Totals per game for the last `days` days, plus today on its own."""
    days = max(1, min(days, 365))
    window = f"{days} days"

    conn = get_db_connection()
    try:
        arcade = conn.execute(
            """
            SELECT game, COUNT(*) AS rounds, COUNT(DISTINCT user_id) AS players,
                   SUM(stake) AS stake, SUM(payout) AS payout
            FROM game_rounds
            WHERE created_at >= NOW() - ?::interval
            GROUP BY game
            """,
            (window,),
        ).fetchall()

        # WinGo pays out through bets.payout once a round is settled.
        wingo = conn.execute(
            """
            SELECT COUNT(*) AS rounds, COUNT(DISTINCT user_id) AS players,
                   SUM(total_stake) AS stake, SUM(payout) AS payout
            FROM bets
            WHERE created_at >= NOW() - ?::interval
            """,
            (window,),
        ).fetchone()

        today_rows = conn.execute(
            """
            SELECT game, COUNT(*) AS rounds, COUNT(DISTINCT user_id) AS players,
                   SUM(stake) AS stake, SUM(payout) AS payout
            FROM game_rounds
            WHERE created_at >= date_trunc('day', NOW())
            GROUP BY game
            """
        ).fetchall()

        daily = conn.execute(
            """
            SELECT date_trunc('day', created_at)::date AS day, game,
                   SUM(stake) AS stake, SUM(payout) AS payout, COUNT(*) AS rounds
            FROM game_rounds
            WHERE created_at >= NOW() - ?::interval
            GROUP BY 1, 2
            ORDER BY 1 DESC
            """,
            (window,),
        ).fetchall()
    finally:
        conn.close()

    games = [
        _shape(row["game"], row["rounds"], row["players"], row["stake"], row["payout"])
        for row in arcade
    ]
    if wingo and wingo["rounds"]:
        games.append(
            _shape("wingo", wingo["rounds"], wingo["players"], wingo["stake"], wingo["payout"])
        )
    games.sort(key=lambda item: item["stake"], reverse=True)

    totals = _shape(
        "all",
        sum(g["rounds"] for g in games),
        0,
        sum(g["stake"] for g in games),
        sum(g["payout"] for g in games),
    )
    totals.pop("players")

    return {
        "days": days,
        "games": games,
        "today": [
            _shape(row["game"], row["rounds"], row["players"], row["stake"], row["payout"])
            for row in today_rows
        ],
        "daily": [
            {
                "day": str(row["day"]),
                "game": row["game"],
                "label": GAME_LABELS.get(row["game"], row["game"].title()),
                "rounds": int(row["rounds"]),
                "stake": round(float(row["stake"] or 0), 2),
                "payout": round(float(row["payout"] or 0), 2),
                "ggr": round(float(row["stake"] or 0) - float(row["payout"] or 0), 2),
            }
            for row in daily
        ],
        "totals": totals,
    }


@router.get("/rounds")
def recent_rounds(game: str | None = None, limit: int = 50, _: bool = Depends(require_admin)):
    """The live feed: most recent settled arcade rounds, newest first."""
    limit = max(1, min(limit, 200))
    conn = get_db_connection()
    try:
        if game:
            rows = conn.execute(
                """
                SELECT r.id, r.game, r.stake, r.payout, r.outcome, r.created_at,
                       u.username, u.phone
                FROM game_rounds r JOIN users u ON u.id = r.user_id
                WHERE r.game = ?
                ORDER BY r.created_at DESC LIMIT ?
                """,
                (game, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT r.id, r.game, r.stake, r.payout, r.outcome, r.created_at,
                       u.username, u.phone
                FROM game_rounds r JOIN users u ON u.id = r.user_id
                ORDER BY r.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    finally:
        conn.close()

    return {
        "rounds": [
            {
                **dict(row),
                "label": GAME_LABELS.get(row["game"], row["game"].title()),
                "profit": round(float(row["payout"]) - float(row["stake"]), 2),
            }
            for row in rows
        ]
    }


@router.get("/players")
def top_players(days: int = 7, limit: int = 20, _: bool = Depends(require_admin)):
    """Who is staking the most, and whether the house is up against them."""
    days = max(1, min(days, 365))
    limit = max(1, min(limit, 100))
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.phone, u.balance,
                   COUNT(*) AS rounds, SUM(r.stake) AS stake, SUM(r.payout) AS payout
            FROM game_rounds r JOIN users u ON u.id = r.user_id
            WHERE r.created_at >= NOW() - ?::interval
            GROUP BY u.id, u.username, u.phone, u.balance
            ORDER BY stake DESC LIMIT ?
            """,
            (f"{days} days", limit),
        ).fetchall()
    finally:
        conn.close()

    return {
        "players": [
            {
                "id": row["id"],
                "username": row["username"],
                "phone": row["phone"],
                "balance": round(float(row["balance"]), 2),
                "rounds": int(row["rounds"]),
                "stake": round(float(row["stake"] or 0), 2),
                "payout": round(float(row["payout"] or 0), 2),
                "ggr": round(float(row["stake"] or 0) - float(row["payout"] or 0), 2),
            }
            for row in rows
        ]
    }
