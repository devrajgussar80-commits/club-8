"""Single place where the backend reads its environment.

Every module imports these names instead of calling ``os.environ`` again, so
one grep shows what the deployment has to set and production refuses to boot
with a placeholder secret in place.
"""

import os

import env_file  # noqa: F401  -- loads .env.local before anything reads os.environ

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(BACKEND_DIR)

APP_ENV = os.environ.get("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"

UPLOAD_ROOT = os.environ.get("UPLOAD_DIR", os.path.join(BACKEND_DIR, "uploads"))
QR_UPLOAD_DIR = os.path.join(UPLOAD_ROOT, "qr")

# Render injects RENDER_EXTERNAL_URL with the service's own public URL, so
# there is nothing to set by hand there. PUBLIC_API_URL stays as the override
# for hosts that do not, or for a custom domain.
PUBLIC_API_URL = (
    os.environ.get("PUBLIC_API_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or ""
).rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

if IS_PRODUCTION and not ADMIN_API_KEY:
    raise RuntimeError("ADMIN_API_KEY is not set. Refusing to expose the admin API without a key.")

# The demo fallback hands out a real account to unauthenticated callers, so it
# can never be on in production regardless of how the env var is set.
ALLOW_DEMO_USER = (
    os.environ.get("ALLOW_DEMO_USER", "false").lower() == "true" and not IS_PRODUCTION
)
DEMO_USER_ID = "USR9842"

SERVE_FRONTEND = os.environ.get("SERVE_FRONTEND", "true").lower() == "true"

FRONTEND_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if origin.strip()
]

# A single approved-recharge threshold gates every game. Admin flips one switch
# per user and that unlocks WinGo, Aviator, Chicken Road and Mines together.
GAME_ACCESS_MIN_DEPOSIT = 300.0

# Deposit orders are handed a QR and then verified against it. Both ends of the
# order id format live here because the wallet routes validate it twice.
ORDER_ID_PATTERN = r"ORD[A-Z0-9]{10,24}"

BET_MULTIPLIERS = (1, 5, 10, 20, 50, 100)
BET_TYPES = ("color", "number", "size")
BET_COLORS = ("green", "red", "violet")
BET_SIZES = ("Big", "Small")

PREDICTION_MODES = ("auto_least", "manual", "random")
USER_STATUSES = ("active", "disabled")

QR_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
QR_UPLOAD_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def qr_public_url(qr_id: str) -> str:
    """Absolute URL that serves an uploaded QR out of the database."""
    if PUBLIC_API_URL:
        return f"{PUBLIC_API_URL}/api/qr-image/{qr_id}"
    return f"/api/qr-image/{qr_id}"
