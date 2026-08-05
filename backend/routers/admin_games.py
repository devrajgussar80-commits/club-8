"""Admin analytics for every game on the platform.

Reads two ledgers, because the platform has two kinds of game:

* `game_rounds` -- the arcade titles and the lottery, settled in one shot.
* `bets`        -- WinGo, which settles per round when the timer expires.

Both are folded into the same shape so the dashboard renders one table.
GGR is stake minus payout: positive means the house is up.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from database import get_db_connection
from deps import require_admin
from game_controls import get_all_controls, get_controls, save_controls

router = APIRouter(prefix="/api/admin/games", tags=["admin"])

# Display names for anything that shows up in the ledgers.
GAME_LABELS = {
    "slots": "Lucky Reels",
    "megaslots": "Mega Slots",
    "roulette": "Roulette",
    "dice": "Dice Roll",
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


class ControlsUpdate(BaseModel):
    mode: str | None = None
    forced: str | None = None
    house_bias: float | None = None
    enabled: bool | None = None
    min_stake: float | None = None
    max_stake: float | None = None


@router.get("/controls")
def list_controls(_: bool = Depends(require_admin)):
    """Every game with its current auto/manual settings and its live activity."""
    conn = get_db_connection()
    try:
        controls = get_all_controls(conn)
        live = _live_activity(conn)
    finally:
        conn.close()

    for entry in controls:
        entry.update(live.get(entry["game"], _EMPTY_LIVE.copy()))
    return {"games": controls}


@router.get("/controls/{game}")
def read_controls(game: str, _: bool = Depends(require_admin)):
    conn = get_db_connection()
    try:
        controls = get_controls(conn, game)
        controls.update(_live_activity(conn).get(game, _EMPTY_LIVE.copy()))
        recent = conn.execute(
            """
            SELECT r.id, r.stake, r.payout, r.created_at, u.username
            FROM game_rounds r JOIN users u ON u.id = r.user_id
            WHERE r.game = ? ORDER BY r.created_at DESC LIMIT 20
            """,
            (game,),
        ).fetchall()
    finally:
        conn.close()
    controls["recent"] = [
        {**dict(row), "profit": round(float(row["payout"]) - float(row["stake"]), 2)}
        for row in recent
    ]
    controls["live_bets"] = live_bets(game)
    return controls


# Shared-round games hold open bets until the round settles, so for those the
# admin can see the table as it stands: who is on which selection, right now.
# The one-shot games (slots, roulette, dice single-player) have nothing open to
# show -- their round is already over by the time it reaches the database.
# Every column is written with the `b.` alias below, because `users` also has
# `status` and joining the two made an unqualified `WHERE status = 'pending'`
# ambiguous -- Postgres rejects the query outright.
LIVE_BET_SOURCES = {
    "wingo": {
        "table": "bets",
        "selection": "b.select_type || ':' || b.selection",
        "stake": "b.total_stake",
        "open": "b.status = 'pending'",
    },
    "dice": {
        "table": "dice_bets",
        "selection": "b.bet_type || ':' || b.selection",
        "stake": "b.amount",
        "open": "b.status = 'pending'",
    },
}


def live_bets(game: str) -> dict:
    """Who is betting on what in the round that is open right now."""
    source = LIVE_BET_SOURCES.get(game)
    if not source:
        return {"supported": False, "selections": [], "players": []}

    conn = get_db_connection()
    try:
        if not _table_exists(conn, source["table"]):
            return {"supported": False, "selections": [], "players": []}

        # Grouped by what was picked: the shape an operator actually reads
        # before a round closes -- where the money is, and what it would cost.
        selections = conn.execute(
            f"""
            SELECT {source['selection']} AS selection,
                   COUNT(DISTINCT b.user_id) AS players,
                   COUNT(*) AS bets,
                   COALESCE(SUM({source['stake']}), 0) AS staked
            FROM {source['table']} b
            WHERE {source['open']}
            GROUP BY 1 ORDER BY staked DESC
            """
        ).fetchall()

        # Counted straight from the round, not by summing the per-selection
        # rows: one player betting on both red and green is two rows but one
        # player, so adding the groups up would overstate the headcount.
        totals = conn.execute(
            f"""
            SELECT COUNT(DISTINCT b.user_id) AS players,
                   COUNT(*) AS bets,
                   COALESCE(SUM({source['stake']}), 0) AS staked
            FROM {source['table']} b
            WHERE {source['open']}
            """
        ).fetchone()
    finally:
        conn.close()

    total_players = int(totals["players"] or 0)
    rows = [
        {
            "selection": row["selection"],
            "players": int(row["players"]),
            "bets": int(row["bets"]),
            "staked": round(float(row["staked"]), 2),
            # Share of everyone in the round, so "20 of 100 backed green" reads
            # directly off the card.
            "share": round(int(row["players"]) / total_players * 100)
            if total_players else 0,
        }
        for row in selections
    ]

    return {
        "supported": True,
        "total_players": total_players,
        "total_bets": int(totals["bets"] or 0),
        "total_staked": round(float(totals["staked"] or 0), 2),
        "selections": rows,
    }


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table,),
    ).fetchone()
    return bool(row)


@router.put("/controls/{game}")
def update_controls(game: str, req: ControlsUpdate, _: bool = Depends(require_admin)):
    payload = {key: value for key, value in req.model_dump().items() if value is not None}
    conn = get_db_connection()
    try:
        return save_controls(conn, game, payload)
    finally:
        conn.close()


_EMPTY_LIVE = {
    "live_players": 0,
    "live_rounds": 0,
    "live_stake": 0.0,
    "hour_players": 0,
    "hour_rounds": 0,
    "hour_stake": 0.0,
}


def _live_activity(conn) -> dict:
    """Who is playing right now, per game.

    "Now" is the last 5 minutes of settled rounds -- these games settle in one
    request, so there is no open-bet table to count. The hour window sits
    beside it to show whether a quiet 5 minutes is a lull or a dead game.

    ONE query, deliberately. This ran as three separate statements and, against
    a network database, three round trips per panel refresh is most of the
    delay the admin actually feels. FILTER does the 5-minute window inside the
    hour scan, and the UNION picks up WinGo, whose live number comes from
    pending bets rather than settled rounds.
    """
    rows = conn.execute(
        """
        SELECT game,
               COUNT(DISTINCT user_id) FILTER (WHERE created_at >= NOW() - INTERVAL '5 minutes') AS live_players,
               COUNT(*)                FILTER (WHERE created_at >= NOW() - INTERVAL '5 minutes') AS live_rounds,
               COALESCE(SUM(stake)     FILTER (WHERE created_at >= NOW() - INTERVAL '5 minutes'), 0) AS live_stake,
               COUNT(DISTINCT user_id) AS hour_players,
               COUNT(*) AS hour_rounds,
               COALESCE(SUM(stake), 0) AS hour_stake
        FROM game_rounds
        WHERE created_at >= NOW() - INTERVAL '1 hour'
        GROUP BY game

        UNION ALL

        SELECT 'wingo' AS game,
               COUNT(DISTINCT user_id) FILTER (WHERE status = 'pending') AS live_players,
               COUNT(*)                FILTER (WHERE status = 'pending') AS live_rounds,
               COALESCE(SUM(total_stake) FILTER (WHERE status = 'pending'), 0) AS live_stake,
               COUNT(DISTINCT user_id) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') AS hour_players,
               COUNT(*)                FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') AS hour_rounds,
               COALESCE(SUM(total_stake) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour'), 0) AS hour_stake
        FROM bets
        WHERE status = 'pending' OR created_at >= NOW() - INTERVAL '1 hour'
        """
    ).fetchall()

    live = {}
    for row in rows:
        if not (row["live_rounds"] or row["hour_rounds"]):
            continue
        live[row["game"]] = {
            "live_players": int(row["live_players"] or 0),
            "live_rounds": int(row["live_rounds"] or 0),
            "live_stake": round(float(row["live_stake"] or 0), 2),
            "hour_players": int(row["hour_players"] or 0),
            "hour_rounds": int(row["hour_rounds"] or 0),
            "hour_stake": round(float(row["hour_stake"] or 0), 2),
        }
    return live


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
