"""Admin authentication, moderation queues and settings validation."""

import io
import itertools

import pytest

_utr_counter = itertools.count(200000000000)


def utr():
    return str(next(_utr_counter))


def submit_deposit(client, headers, amount):
    order = client.post(
        "/api/wallet/deposit-order", json={"amount": amount}, headers=headers
    ).json()
    response = client.post(
        "/api/wallet/deposit",
        json={
            "amount": amount,
            "utr": utr(),
            "qr_id": order["qr"]["id"],
            "order_id": order["order_id"],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["deposit_id"]


# ----------------- ACCESS CONTROL -----------------
def test_admin_routes_reject_a_missing_key(client):
    assert client.get("/api/admin/dashboard").status_code == 401


def test_admin_routes_reject_a_wrong_key(client):
    response = client.get("/api/admin/dashboard", headers={"X-Admin-Key": "wrong"})
    assert response.status_code == 401


def test_a_player_token_is_not_an_admin_token(client, register):
    headers, _ = register()
    assert client.get("/api/admin/dashboard", headers=headers).status_code == 401


def test_admin_login_requires_the_admin_flag(client, register, admin_headers):
    _, user = register(password="admin-pass-1")
    response = client.post(
        "/api/admin/login", json={"phone": user["phone"], "password": "admin-pass-1"}
    )
    assert response.status_code == 403

    client.post("/api/admin/grant-admin", json={"phone": user["phone"]}, headers=admin_headers)
    response = client.post(
        "/api/admin/login", json={"phone": user["phone"], "password": "admin-pass-1"}
    )
    assert response.status_code == 200

    token_headers = {"Authorization": f"Bearer {response.json()['token']}"}
    assert client.get("/api/admin/dashboard", headers=token_headers).status_code == 200


def test_rotating_the_access_key_revokes_the_old_one(client, admin_headers):
    new_key = "rotated-admin-key-9876543210ab"
    response = client.post(
        "/api/admin/rotate-access-key", json={"api_key": new_key}, headers=admin_headers
    )
    assert response.status_code == 200
    assert client.get("/api/admin/dashboard", headers=admin_headers).status_code == 401
    assert client.get("/api/admin/dashboard", headers={"X-Admin-Key": new_key}).status_code == 200


def test_rotation_rejects_a_short_key(client, admin_headers):
    response = client.post(
        "/api/admin/rotate-access-key", json={"api_key": "too-short"}, headers=admin_headers
    )
    assert response.status_code == 422


# ----------------- DASHBOARD -----------------
def test_dashboard_bundles_every_panel(client, admin_headers, register):
    register()
    body = client.get("/api/admin/dashboard", headers=admin_headers).json()
    assert set(body) >= {
        "metrics",
        "platform_settings",
        "users",
        "deposits",
        "withdrawals",
        "qr_codes",
        "game_access_min_deposit",
        "server_time",
    }
    assert body["metrics"]["users_count"] == 1
    assert body["game_access_min_deposit"] == 300.0


def test_metrics_matches_the_dashboard_block(client, admin_headers):
    metrics = client.get("/api/admin/metrics", headers=admin_headers).json()
    dashboard = client.get("/api/admin/dashboard", headers=admin_headers).json()["metrics"]
    assert metrics["users_count"] == dashboard["users_count"]
    assert metrics["prediction_mode"] == dashboard["prediction_mode"]


# ----------------- SETTINGS VALIDATION -----------------
@pytest.mark.parametrize("mode", ["auto_least", "manual", "random"])
def test_prediction_mode_accepts_known_modes(client, admin_headers, mode):
    response = client.post(
        "/api/admin/prediction-mode", json={"mode": mode}, headers=admin_headers
    )
    assert response.status_code == 200
    assert client.get("/api/admin/metrics", headers=admin_headers).json()["prediction_mode"] == mode


def test_prediction_mode_rejects_an_unknown_mode(client, admin_headers):
    response = client.post(
        "/api/admin/prediction-mode", json={"mode": "always_house_wins"}, headers=admin_headers
    )
    assert response.status_code == 422


def test_force_result_stores_the_number_and_switches_to_manual(client, admin_headers):
    assert (
        client.post("/api/admin/force-result", json={"number": 3}, headers=admin_headers).status_code
        == 200
    )
    assert client.get("/api/admin/metrics", headers=admin_headers).json()["prediction_mode"] == "manual"


@pytest.mark.parametrize("number", [-1, 10, 99])
def test_force_result_rejects_out_of_range(client, admin_headers, number):
    response = client.post(
        "/api/admin/force-result", json={"number": number}, headers=admin_headers
    )
    assert response.status_code == 422


def test_platform_settings_round_trip(client, admin_headers):
    response = client.put(
        "/api/admin/platform-settings",
        json={"deposits_enabled": True, "withdrawals_enabled": False, "withdrawal_min": 350},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert client.get("/api/wallet/settings").json() == {
        "deposits_enabled": True,
        "withdrawals_enabled": False,
        "withdrawal_min": 350.0,
    }


def test_platform_settings_rejects_a_nonsense_minimum(client, admin_headers):
    response = client.put(
        "/api/admin/platform-settings",
        json={"deposits_enabled": True, "withdrawals_enabled": True, "withdrawal_min": 0},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_user_status_rejects_an_unknown_value(client, admin_headers, register):
    _, user = register()
    response = client.put(
        f"/api/admin/users/{user['id']}/status",
        json={"status": "shadowbanned"},
        headers=admin_headers,
    )
    assert response.status_code == 422


# ----------------- DEPOSIT MODERATION -----------------
def test_approving_a_deposit_credits_the_wallet_once(client, admin_headers, register):
    headers, _ = register()
    dep_id = submit_deposit(client, headers, 500)

    assert (
        client.post(f"/api/admin/deposits/{dep_id}/approve", headers=admin_headers).status_code == 200
    )
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 600.0

    # A second approval must not credit again.
    assert (
        client.post(f"/api/admin/deposits/{dep_id}/approve", headers=admin_headers).status_code == 400
    )
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 600.0


def test_rejecting_a_deposit_credits_nothing(client, admin_headers, register):
    headers, _ = register()
    dep_id = submit_deposit(client, headers, 500)
    assert (
        client.post(f"/api/admin/deposits/{dep_id}/reject", headers=admin_headers).status_code == 200
    )
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 100.0


# ----------------- WITHDRAWAL MODERATION -----------------
def test_rejecting_a_withdrawal_refunds_exactly_once(client, admin_headers, register, db, unlock_withdrawals):
    headers, user = register()
    conn = db()
    conn.execute("UPDATE users SET balance = 1000 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()

    wth_id = client.post(
        "/api/wallet/withdraw", json={"amount": 400, "upi_id": "p@upi"}, headers=headers
    ).json()["withdrawal_id"]
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 600.0

    assert (
        client.post(f"/api/admin/withdrawals/{wth_id}/reject", headers=admin_headers).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 1000.0

    assert (
        client.post(f"/api/admin/withdrawals/{wth_id}/reject", headers=admin_headers).status_code
        == 400
    )
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 1000.0


def test_approving_a_withdrawal_keeps_the_funds_reserved(client, admin_headers, register, db, unlock_withdrawals):
    headers, user = register()
    conn = db()
    conn.execute("UPDATE users SET balance = 1000 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()

    wth_id = client.post(
        "/api/wallet/withdraw", json={"amount": 400, "upi_id": "p@upi"}, headers=headers
    ).json()["withdrawal_id"]
    client.post(f"/api/admin/withdrawals/{wth_id}/approve", headers=admin_headers)
    assert client.get("/api/auth/me", headers=headers).json()["user"]["balance"] == 600.0


# ----------------- GAME ACCESS -----------------
def test_game_access_needs_the_approved_deposit_threshold(client, admin_headers, register):
    headers, user = register()
    response = client.put(
        f"/api/admin/users/{user['id']}/game-access",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "approved recharge" in response.json()["detail"]

    dep_id = submit_deposit(client, headers, 500)
    client.post(f"/api/admin/deposits/{dep_id}/approve", headers=admin_headers)

    response = client.put(
        f"/api/admin/users/{user['id']}/game-access",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me", headers=headers).json()["user"]["game_access_enabled"] is True


def test_game_access_on_an_unknown_user_is_404(client, admin_headers):
    response = client.put(
        "/api/admin/users/USR-nope/game-access", json={"enabled": False}, headers=admin_headers
    )
    assert response.status_code == 404


def test_deleting_a_user_with_a_pending_withdrawal_is_refused(client, admin_headers, register, db):
    headers, user = register()
    conn = db()
    conn.execute("UPDATE users SET balance = 1000 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    client.post("/api/wallet/withdraw", json={"amount": 400, "upi_id": "p@upi"}, headers=headers)

    response = client.delete(f"/api/admin/users/{user['id']}", headers=admin_headers)
    assert response.status_code == 400
    assert "pending withdrawals" in response.json()["detail"]


# ----------------- QR MANAGEMENT -----------------
def test_manual_qr_rejects_a_javascript_url(client, admin_headers):
    response = client.post(
        "/api/admin/qr-codes",
        json={"name": "Bad", "qr_url": "javascript:alert(1)"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_manual_qr_accepts_an_https_url(client, admin_headers):
    response = client.post(
        "/api/admin/qr-codes",
        json={"name": "Good", "qr_url": "https://example.com/qr.png", "upi_id": "shop@upi"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    ids = [q["id"] for q in client.get("/api/admin/qr-codes", headers=admin_headers).json()["qr_codes"]]
    assert response.json()["qr_id"] in ids


def test_uploaded_qr_is_stored_and_removed_with_the_record(client, admin_headers, app_env):
    import os

    import config

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    response = client.post(
        "/api/admin/qr-codes/upload",
        data={"name": "Uploaded", "upi_id": "shop@upi", "min_amount": 100, "max_amount": 5000},
        files={"qr_file": ("qr.png", io.BytesIO(png), "image/png")},
        headers=admin_headers,
    )
    assert response.status_code == 200
    qr_id, qr_url = response.json()["qr_id"], response.json()["qr_url"]
    filename = os.path.basename(qr_url)
    stored = os.path.join(config.QR_UPLOAD_DIR, filename)
    assert os.path.exists(stored)

    assert client.delete(f"/api/admin/qr-codes/{qr_id}", headers=admin_headers).status_code == 200
    assert not os.path.exists(stored)


def test_upload_rejects_a_non_image(client, admin_headers):
    response = client.post(
        "/api/admin/qr-codes/upload",
        data={"name": "Bad"},
        files={"qr_file": ("payload.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_deactivating_a_qr_removes_it_from_the_pool(client, admin_headers):
    pool = client.get("/api/wallet/qr-pool").json()
    target = pool["qr_codes"][0]["id"]
    response = client.post(
        f"/api/admin/qr-codes/{target}/activate?enabled=false", headers=admin_headers
    )
    assert response.status_code == 200
    remaining = [q["id"] for q in client.get("/api/wallet/qr-pool").json()["qr_codes"]]
    assert target not in remaining


def test_activating_an_unknown_qr_is_404(client, admin_headers):
    assert (
        client.post("/api/admin/qr-codes/QR-nope/activate", headers=admin_headers).status_code == 404
    )
