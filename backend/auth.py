"""
Authentication helpers: JWT issuance/verification and password hashing.

Passwords use PBKDF2-HMAC-SHA256 with a per-user random salt. Accounts created
before this scheme existed may carry an unsalted SHA-256 digest; those still
verify, and `needs_rehash` lets the login route upgrade them in place.
"""

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

import env_file  # noqa: F401  -- loads .env.local before the secrets are read

ALGORITHM = "HS256"
APP_ENV = os.environ.get("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"

PBKDF2_ITERATIONS = 260000
PBKDF2_PREFIX = "pbkdf2_sha256"


def _require_secret(name: str, dev_default: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(
            f"{name} is not set. Refusing to start in production with a known default."
        )
    return dev_default


# In production these must come from the environment; a shipped default would
# let anyone forge a token for any account.
SECRET_KEY = _require_secret("JWT_SECRET_KEY", "dev-only-insecure-jwt-key")
PASSWORD_SALT = _require_secret("PASSWORD_SALT", "dev-only-insecure-salt")


def _legacy_hash(password: str) -> str:
    return hashlib.sha256((password + PASSWORD_SALT).encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "$".join(
        [
            PBKDF2_PREFIX,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False

    if hashed_password.startswith(PBKDF2_PREFIX + "$"):
        try:
            _, iterations, salt_b64, digest_b64 = hashed_password.split("$", 3)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
        except (ValueError, TypeError):
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(candidate, expected)

    # Legacy unsalted SHA-256 record.
    return hmac.compare_digest(_legacy_hash(plain_password), hashed_password)


def needs_rehash(hashed_password: str) -> bool:
    """True for stored hashes that should be upgraded on the next successful login."""
    return not (hashed_password or "").startswith(PBKDF2_PREFIX + "$")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=7))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
