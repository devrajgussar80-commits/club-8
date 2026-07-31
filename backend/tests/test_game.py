"""Bet validation, balance handling and round status."""

import pytest


def _bet(**overrides):
    payload = {
        "select_type": "color",
        "selection": "green",
        "amount": 10,
        "multiplier": 1,
        "room": "parity",
    }
    payload.update(overrides)
    return payload


def test_status_reports_the_current_room(client, register):
    headers, _ = register()
    body = client.get("/api/game/status?room=sapre", headers=headers).json()
    assert body["room"] == "sapre"
    assert body["duration"] == 60
    assert 0 <= body["time_remaining"] <= 60
    assert body["game_access_min_deposit"] == 300.0


def test_status_rejects_unknown_room(client, register):
    headers, _ = register()
    assert client.get("/api/game/status?room=nope", headers=headers).status_code == 404


def test_history_rejects_unknown_room(client):
    assert client.get("/api/game/history?room=nope").status_code == 404


def test_bet_debits_the_wallet(client, register):
    headers, _ = register()
    response = client.post("/api/game/bet", json=_bet(amount=10, multiplier=5), headers=headers)
    if response.status_code == 400 and "round is closed" in response.json()["detail"]:
        pytest.skip("landed inside the 5-second freeze window")
    assert response.status_code == 200
    assert response.json()["total_stake"] == 50.0

    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 50.0
    assert len(client.get("/api/game/my-bets", headers=headers).json()["bets"]) == 1


def test_bet_larger_than_the_balance_is_refused(client, register):
    headers, _ = register()
    response = client.post("/api/game/bet", json=_bet(amount=100, multiplier=50), headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"] in (
        "Insufficient balance.",
        "This round is closed. Please place the bet in the next round.",
    )


@pytest.mark.parametrize(
    "payload",
    [
        _bet(amount=0),
        _bet(amount=-5),
        _bet(multiplier=3),
        _bet(select_type="parity"),
    ],
)
def test_malformed_bets_are_rejected_before_any_write(client, register, payload):
    headers, _ = register()
    assert client.post("/api/game/bet", json=payload, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        _bet(select_type="color", selection="blue"),
        _bet(select_type="size", selection="Huge"),
        _bet(select_type="number", selection="12"),
        _bet(select_type="number", selection="abc"),
    ],
)
def test_bad_selections_are_rejected(client, register, payload):
    headers, _ = register()
    response = client.post("/api/game/bet", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid" in response.json()["detail"] or "between 0 and 9" in response.json()["detail"]


def test_bet_for_a_stale_period_is_refused(client, register):
    headers, _ = register()
    response = client.post(
        "/api/game/bet", json=_bet(period="20200101000000001"), headers=headers
    )
    assert response.status_code == 400
    assert "round is closed" in response.json()["detail"]


def test_bet_requires_authentication(client):
    assert client.post("/api/game/bet", json=_bet()).status_code == 401
