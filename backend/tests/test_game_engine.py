"""Payout table, result selection and round resolution."""

import pytest


@pytest.fixture()
def engine(app_env):
    from game_engine import python_engine

    return python_engine


@pytest.mark.parametrize(
    "number,color,size",
    [
        (0, "violet", "Small"),
        (1, "green", "Small"),
        (2, "red", "Small"),
        (5, "violet", "Big"),
        (8, "red", "Big"),
        (9, "green", "Big"),
    ],
)
def test_number_details(engine, number, color, size):
    assert engine.get_number_details(number) == {
        "number": number,
        "color": color,
        "size": size,
    }


@pytest.mark.parametrize(
    "bet,number,expected",
    [
        ({"select_type": "color", "selection": "green"}, 3, 1.96),
        ({"select_type": "color", "selection": "green"}, 5, 1.5),
        ({"select_type": "color", "selection": "green"}, 2, 0.0),
        ({"select_type": "color", "selection": "red"}, 4, 1.96),
        ({"select_type": "color", "selection": "red"}, 0, 1.5),
        ({"select_type": "color", "selection": "violet"}, 5, 4.5),
        ({"select_type": "color", "selection": "violet"}, 6, 0.0),
        ({"select_type": "number", "selection": "7"}, 7, 8.82),
        ({"select_type": "number", "selection": "7"}, 6, 0.0),
        ({"select_type": "size", "selection": "Big"}, 9, 1.96),
        ({"select_type": "size", "selection": "Small"}, 9, 0.0),
    ],
)
def test_payout_multiplier(engine, bet, number, expected):
    assert engine.payout_multiplier_for(bet, number) == expected


def test_manual_mode_returns_the_forced_number(engine):
    outcome = engine.calculate_winning_outcome(
        bets=[], settings={"prediction_mode": "manual", "forced_number": "4"}
    )
    assert outcome["number"] == 4


def test_manual_mode_falls_back_when_the_forced_number_is_garbage(engine):
    outcome = engine.calculate_winning_outcome(
        bets=[], settings={"prediction_mode": "manual", "forced_number": "not-a-number"}
    )
    assert outcome["number"] == 7


def test_auto_least_picks_the_cheapest_outcome(engine):
    # Only 7 is backed, so every other number costs the house nothing.
    bets = [{"select_type": "number", "selection": "7", "total_stake": 1000}]
    for _ in range(20):
        outcome = engine.calculate_winning_outcome(
            bets=bets, settings={"prediction_mode": "auto_least"}
        )
        assert outcome["number"] != 7


def test_random_mode_stays_in_range(engine):
    for _ in range(30):
        outcome = engine.calculate_winning_outcome(bets=[], settings={"prediction_mode": "random"})
        assert 0 <= outcome["number"] <= 9


def test_resolve_pays_the_winner_and_records_the_round(engine, db):
    from settings_store import set_setting

    conn = db()
    conn.execute(
        "INSERT INTO users (id, phone, username, password_hash, balance, status) "
        "VALUES ('USR-T1', '+910000000001', 'Winner', '!no-login', 0, 'active')"
    )
    period = engine.rooms["parity"]["period"]
    conn.execute(
        """INSERT INTO bets
        (id, user_id, period, select_type, selection, amount, multiplier, total_stake, status)
        VALUES ('BET-T1', 'USR-T1', ?, 'number', '4', 100, 1, 100, 'pending')""",
        (period,),
    )
    set_setting(conn, "prediction_mode", "manual")
    set_setting(conn, "forced_number", "4")
    conn.commit()
    conn.close()

    engine.resolve_room("parity")

    conn = db()
    bet = dict(conn.execute("SELECT * FROM bets WHERE id = 'BET-T1'").fetchone())
    balance = conn.execute("SELECT balance FROM users WHERE id = 'USR-T1'").fetchone()[0]
    round_row = dict(conn.execute("SELECT * FROM rounds WHERE period = ?", (period,)).fetchone())
    conn.close()

    assert bet["status"] == "win"
    assert bet["payout"] == 882.0
    assert balance == 882.0
    assert round_row["winning_number"] == 4
    assert round_row["status"] == "completed"


def test_resolving_the_same_period_twice_does_not_pay_twice(engine, db):
    """The rounds primary key is the claim that stops a double payout."""
    from settings_store import set_setting

    conn = db()
    conn.execute(
        "INSERT INTO users (id, phone, username, password_hash, balance, status) "
        "VALUES ('USR-T2', '+910000000002', 'Winner', '!no-login', 0, 'active')"
    )
    period = engine.rooms["sapre"]["period"]
    conn.execute(
        """INSERT INTO bets
        (id, user_id, period, select_type, selection, amount, multiplier, total_stake, status)
        VALUES ('BET-T2', 'USR-T2', ?, 'number', '4', 100, 1, 100, 'pending')""",
        (period,),
    )
    set_setting(conn, "prediction_mode", "manual")
    set_setting(conn, "forced_number", "4")
    conn.commit()
    conn.close()

    engine.resolve_room("sapre")
    engine.resolve_room("sapre")

    conn = db()
    balance = conn.execute("SELECT balance FROM users WHERE id = 'USR-T2'").fetchone()[0]
    conn.close()
    assert balance == 882.0
