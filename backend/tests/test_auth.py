"""Registration, login, token handling and the profile payload."""


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "club-8-api"}


def test_register_returns_token_and_signup_bonus(client, register):
    headers, user = register()
    assert user["balance"] == 100.0
    assert user["game_access_enabled"] is False

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["id"] == user["id"]


def test_register_rejects_duplicate_phone(client, register):
    _, user = register()
    response = client.post(
        "/api/auth/register",
        json={"phone": user["phone"], "username": "Someone else", "password": "secret123"},
    )
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]


def test_register_rejects_short_password(client):
    response = client.post(
        "/api/auth/register",
        json={"phone": "+919000000001", "username": "Shorty", "password": "abc"},
    )
    assert response.status_code == 422


def test_login_round_trip(client, register):
    _, user = register(password="hunter2000")
    response = client.post(
        "/api/auth/login", json={"phone": user["phone"], "password": "hunter2000"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == user["id"]


def test_login_rejects_wrong_password(client, register):
    _, user = register()
    response = client.post(
        "/api/auth/login", json={"phone": user["phone"], "password": "not-the-password"}
    )
    assert response.status_code == 400


def test_legacy_sha256_hash_verifies_then_upgrades(client, register, db):
    """Pre-PBKDF2 accounts must still log in, and be rewritten on the way out."""
    import auth as auth_helpers

    _, user = register()
    legacy = auth_helpers._legacy_hash("legacy-pass")
    conn = db()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (legacy, user["id"]))
    conn.commit()
    conn.close()

    response = client.post(
        "/api/auth/login", json={"phone": user["phone"], "password": "legacy-pass"}
    )
    assert response.status_code == 200

    conn = db()
    stored = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
    ).fetchone()[0]
    conn.close()
    assert stored.startswith("pbkdf2_sha256$")


def test_profile_never_returns_the_password_hash(client, register):
    headers, _ = register()
    assert "password_hash" not in client.get("/api/auth/me", headers=headers).json()["user"]


def test_protected_routes_require_a_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_disabled_account_cannot_use_its_token(client, register, admin_headers, db):
    headers, user = register()
    client.put(
        f"/api/admin/users/{user['id']}/status",
        json={"status": "disabled"},
        headers=admin_headers,
    )
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 403
