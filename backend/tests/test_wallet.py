"""Deposit orders, UTR submission and withdrawal reservation."""

import itertools

import pytest

from test_admin import submit_deposit

_utr_counter = itertools.count(100000000000)


def utr():
    return str(next(_utr_counter))


@pytest.fixture()
def two_active_qrs(client, admin_headers):
    """The seed leaves exactly one QR active, so opt the second one in."""
    all_qrs = client.get("/api/admin/qr-codes", headers=admin_headers).json()["qr_codes"]
    for qr in all_qrs:
        client.post(f"/api/admin/qr-codes/{qr['id']}/activate?enabled=true", headers=admin_headers)
    return [qr["id"] for qr in all_qrs]


@pytest.fixture()
def upi_qr(client, admin_headers):
    """The seeded QRs carry no upi_id, so the server cannot build a UPI payload.

    Retire them and install one QR that has everything a payment needs.
    """
    for qr in client.get("/api/admin/qr-codes", headers=admin_headers).json()["qr_codes"]:
        client.post(f"/api/admin/qr-codes/{qr['id']}/activate?enabled=false", headers=admin_headers)
    response = client.post(
        "/api/admin/qr-codes",
        json={
            "name": "Test UPI",
            "qr_url": "https://example.com/qr.png",
            "upi_id": "merchant@upi",
            "min_amount": 100,
            "max_amount": 50000,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["qr_id"]


@pytest.fixture()
def order(client, register):
    """Start a deposit order and return (headers, order payload)."""

    def _order(amount=500):
        headers, _ = register()
        response = client.post("/api/wallet/deposit-order", json={"amount": amount}, headers=headers)
        assert response.status_code == 200, response.text
        return headers, response.json()

    return _order


def test_wallet_settings_are_public(client):
    body = client.get("/api/wallet/settings").json()
    assert body["deposits_enabled"] is True
    assert body["withdrawals_enabled"] is True
    assert body["withdrawal_min"] == 200.0
    # The withdraw screen explains the recharge gate before the player fills
    # the form in, so both halves of it have to be readable without a token.
    assert body["withdrawal_min_deposit"] == 500.0
    assert "{minimum}" in body["withdrawal_locked_message"]


def test_deposit_order_pins_a_qr(client, order):
    _, body = order(500)
    assert body["order_id"].startswith("ORD")
    assert body["amount"] == 500
    assert body["qr"]["id"]


def test_deposit_order_rotates_through_the_pool(client, order, two_active_qrs):
    _, first = order()
    _, second = order()
    _, third = order()
    # Least-recently-used ordering: two live QRs must alternate, not repeat.
    assert first["qr"]["id"] != second["qr"]["id"]
    assert third["qr"]["id"] == first["qr"]["id"]


def test_deposit_order_enforces_qr_limits(client, register):
    headers, _ = register()
    response = client.post("/api/wallet/deposit-order", json={"amount": 1}, headers=headers)
    assert response.status_code == 400
    assert "between" in response.json()["detail"]


def test_deposit_submission_is_pending_until_approved(client, order):
    headers, body = order()
    reference = utr()
    response = client.post(
        "/api/wallet/deposit",
        json={
            "amount": body["amount"],
            "utr": reference,
            "qr_id": body["qr"]["id"],
            "order_id": body["order_id"],
        },
        headers=headers,
    )
    assert response.status_code == 200

    deposits = client.get("/api/wallet/deposits", headers=headers).json()["deposits"]
    assert [d["status"] for d in deposits] == ["pending"]
    # Nothing is credited before an admin approves.
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 100.0


def test_duplicate_utr_is_refused(client, order):
    headers, body = order()
    reference = utr()
    payload = {
        "amount": body["amount"],
        "utr": reference,
        "qr_id": body["qr"]["id"],
        "order_id": body["order_id"],
    }
    assert client.post("/api/wallet/deposit", json=payload, headers=headers).status_code == 200

    headers2, body2 = order()
    payload2 = {**payload, "qr_id": body2["qr"]["id"], "order_id": body2["order_id"]}
    response = client.post("/api/wallet/deposit", json=payload2, headers=headers2)
    assert response.status_code == 400
    assert "already been submitted" in response.json()["detail"]


@pytest.mark.parametrize("bad_utr", ["12345", "abcdefghijkl", "", "1234567890123"])
def test_utr_must_be_twelve_digits(client, order, bad_utr):
    headers, body = order()
    response = client.post(
        "/api/wallet/deposit",
        json={
            "amount": body["amount"],
            "utr": bad_utr,
            "qr_id": body["qr"]["id"],
            "order_id": body["order_id"],
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "12-digit UTR" in response.json()["detail"]


def test_deposit_rejects_a_forged_order_id(client, order):
    headers, body = order()
    response = client.post(
        "/api/wallet/deposit",
        json={
            "amount": body["amount"],
            "utr": utr(),
            "qr_id": body["qr"]["id"],
            "order_id": "not-an-order",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "Invalid or expired deposit order" in response.json()["detail"]


def test_payment_qr_renders_a_png(client, order, upi_qr):
    _, body = order()
    assert body["qr"]["id"] == upi_qr
    response = client.get(
        "/api/wallet/payment-qr",
        params={
            "amount": body["amount"],
            "qr_id": body["qr"]["id"],
            "order_id": body["order_id"],
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_payment_qr_rejects_a_bad_order_id(client, order, upi_qr):
    _, body = order()
    response = client.get(
        "/api/wallet/payment-qr",
        params={"amount": body["amount"], "qr_id": body["qr"]["id"], "order_id": "XYZ"},
    )
    assert response.status_code == 400


def test_payment_qr_needs_a_upi_id_on_the_record(client, order):
    """The seeded QRs have no upi_id, so there is no payload to encode."""
    _, body = order()
    response = client.get(
        "/api/wallet/payment-qr",
        params={
            "amount": body["amount"],
            "qr_id": body["qr"]["id"],
            "order_id": body["order_id"],
        },
    )
    assert response.status_code == 404


def test_withdraw_reserves_the_balance_immediately(client, register, db, unlock_withdrawals):
    headers, user = register()
    conn = db()
    conn.execute("UPDATE users SET balance = 1000 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 500, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 500.0

    withdrawals = client.get("/api/wallet/withdrawals", headers=headers).json()["withdrawals"]
    assert [w["status"] for w in withdrawals] == ["pending"]


def test_withdraw_enforces_the_minimum(client, register, unlock_withdrawals):
    headers, _ = register()
    response = client.post(
        "/api/wallet/withdraw", json={"amount": 50, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 400
    assert "Minimum withdrawal" in response.json()["detail"]


def test_withdraw_over_the_balance_is_refused(client, register, unlock_withdrawals):
    headers, _ = register()
    response = client.post(
        "/api/wallet/withdraw", json={"amount": 900, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 400
    assert "Insufficient" in response.json()["detail"]


def test_withdraw_needs_a_destination(client, register, db, unlock_withdrawals):
    headers, user = register()
    conn = db()
    conn.execute("UPDATE users SET balance = 1000 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    response = client.post(
        "/api/wallet/withdraw", json={"amount": 300, "upi_id": "   "}, headers=headers
    )
    assert response.status_code == 400


# ----------------- WITHDRAWAL RECHARGE GATE -----------------
def _fund(db, user_id, balance=2000):
    conn = db()
    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (balance, user_id))
    conn.commit()
    conn.close()


def test_withdraw_is_locked_until_the_account_has_recharged(client, register, db):
    """A wallet holding only the signup bonus, and whatever it won, cannot be
    cashed out -- that is the whole point of the gate."""
    headers, user = register()
    _fund(db, user["id"])

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 500, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 403
    assert "500" in response.json()["detail"]
    # Refused before anything is reserved.
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 2000.0


def test_an_approved_recharge_unlocks_withdrawals(client, register, admin_headers, db):
    headers, user = register()
    dep_id = submit_deposit(client, headers, 500)
    client.post(f"/api/admin/deposits/{dep_id}/approve", headers=admin_headers)

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 300, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 200


def test_a_recharge_below_the_threshold_does_not_unlock(client, register, admin_headers, db):
    headers, user = register()
    dep_id = submit_deposit(client, headers, 200)
    client.post(f"/api/admin/deposits/{dep_id}/approve", headers=admin_headers)
    _fund(db, user["id"])

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 300, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 403


def test_the_locked_message_is_the_one_the_dashboard_saved(client, register, admin_headers, db):
    client.put(
        "/api/admin/platform-settings",
        json={
            "deposits_enabled": True,
            "withdrawals_enabled": True,
            "withdrawal_min": 200,
            "withdrawal_min_deposit": 750,
            "withdrawal_locked_message": "Add ₹{minimum} first. You have ₹{deposited}.",
        },
        headers=admin_headers,
    )
    headers, user = register()
    _fund(db, user["id"])

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 300, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Add ₹750 first. You have ₹0."


def test_a_zero_threshold_turns_the_gate_off(client, register, admin_headers, db):
    client.put(
        "/api/admin/platform-settings",
        json={
            "deposits_enabled": True,
            "withdrawals_enabled": True,
            "withdrawal_min": 200,
            "withdrawal_min_deposit": 0,
        },
        headers=admin_headers,
    )
    headers, user = register()
    _fund(db, user["id"])

    response = client.post(
        "/api/wallet/withdraw", json={"amount": 300, "upi_id": "player@upi"}, headers=headers
    )
    assert response.status_code == 200


def test_paused_deposits_and_withdrawals_return_503(client, register, admin_headers):
    headers, _ = register()
    client.put(
        "/api/admin/platform-settings",
        json={"deposits_enabled": False, "withdrawals_enabled": False, "withdrawal_min": 200},
        headers=admin_headers,
    )
    assert (
        client.post("/api/wallet/deposit-order", json={"amount": 500}, headers=headers).status_code
        == 503
    )
    assert (
        client.post(
            "/api/wallet/withdraw", json={"amount": 500, "upi_id": "a@upi"}, headers=headers
        ).status_code
        == 503
    )
