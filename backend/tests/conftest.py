"""Test fixtures.

Each test runs in its own throwaway Postgres *schema*, created before the test
and dropped after, so tests never see each other's rows and never touch real
tables. That is the Postgres equivalent of the per-test SQLite file this
replaced -- and it means the whole suite can share one cheap Neon branch.

Set TEST_DATABASE_URL (a Neon branch, or any local Postgres). Without it the
suite skips rather than risking a run against the production database.

Nothing from the app is imported at module scope: `config`, `auth` and
`database` all read os.environ when first imported.
"""

import os
import sys
import uuid

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

ADMIN_KEY = "test-admin-access-key-0123456789"

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

APP_MODULES = (
    "main",
    "routers",
    "routers.admin",
    "routers.auth",
    "routers.game",
    "routers.pages",
    "routers.wallet",
    "deps",
    "settings_store",
    "schemas",
    "game_engine",
    "database",
    "auth",
    "config",
)


def _forget_app_modules():
    for name in APP_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set; see backend/README.md")

    schema = f"test_{uuid.uuid4().hex[:12]}"

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DB_SCHEMA", schema)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-value-for-pytest")
    monkeypatch.setenv("PASSWORD_SALT", "test-password-salt")
    monkeypatch.setenv("ALLOW_DEMO_USER", "false")
    monkeypatch.setenv("SERVE_FRONTEND", "false")
    monkeypatch.setenv("PUBLIC_API_URL", "")

    _forget_app_modules()

    # The schema has to exist before the pool opens with search_path set to it.
    import psycopg

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')

    import main as main_module

    try:
        yield main_module
    finally:
        import database

        database.close_pool()
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as teardown:
            teardown.execute(f'DROP SCHEMA "{schema}" CASCADE')
        _forget_app_modules()


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    with TestClient(app_env.app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture()
def register(client):
    """Create a player and return (headers, user payload)."""

    def _register(password="secret123", username="Player"):
        phone = f"+9198{uuid.uuid4().int % 100000000:08d}"
        response = client.post(
            "/api/auth/register",
            json={"phone": phone, "username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        headers = {"Authorization": f"Bearer {body['token']}"}
        return headers, {**body["user"], "phone": phone, "password": password}

    return _register


@pytest.fixture()
def db(app_env):
    """Open a connection to the test schema."""
    from database import get_db_connection

    return get_db_connection


@pytest.fixture()
def unlock_withdrawals(db):
    """Switch the recharge-before-withdrawal gate off.

    It is the first thing /api/wallet/withdraw checks after the pause switch,
    so a test about the minimum, the balance or the destination would only
    ever reach the gate. Tests that are about the gate itself leave it alone.
    """
    conn = db()
    conn.execute(
        "INSERT INTO system_settings (key, value) VALUES ('withdrawal_min_deposit', '0') "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
    )
    conn.commit()
    conn.close()
