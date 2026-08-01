"""Request bodies for every route.

Validation that can be expressed as a constraint lives here so a bad value is
rejected as a 422 before any handler opens a database connection. Rules that
need to read state (wallet limits, approved-deposit gates) stay in the routes.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

import config


# ----------------- AUTH -----------------
class RegisterRequest(BaseModel):
    phone: str = Field(min_length=4, max_length=32)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    referral_code: Optional[str] = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    phone: str
    password: str


class AdminLoginRequest(BaseModel):
    phone: str
    password: str


class GrantAdminRequest(BaseModel):
    phone: str
    is_admin: bool = True


class AdminKeyRotationRequest(BaseModel):
    api_key: str = Field(min_length=24)


class LocalPushRequest(BaseModel):
    # Commit message for the one-click local deploy. Blank -> a timestamp.
    message: str = Field(default="", max_length=300)


class AdminCredentialsRequest(BaseModel):
    # The current password gates every change, so a hijacked session token
    # alone cannot lock the real admin out.
    current_password: str = Field(min_length=1)
    new_phone: Optional[str] = Field(default=None, min_length=4, max_length=32)
    new_password: Optional[str] = Field(default=None, min_length=6, max_length=128)


# ----------------- GAME -----------------
class BetRequest(BaseModel):
    select_type: Literal["color", "number", "size"]
    selection: str
    amount: float = Field(gt=0)
    multiplier: int
    room: str = "parity"
    period: Optional[str] = None

    @field_validator("multiplier")
    @classmethod
    def _known_multiplier(cls, value: int) -> int:
        if value not in config.BET_MULTIPLIERS:
            raise ValueError("Invalid bet multiplier.")
        return value


# ----------------- WALLET -----------------
class DepositOrderRequest(BaseModel):
    amount: float


class DepositRequest(BaseModel):
    amount: float
    utr: str
    qr_id: Optional[str] = None
    order_id: Optional[str] = None


class WithdrawRequest(BaseModel):
    amount: float
    upi_id: str


# ----------------- ADMIN -----------------
class PlatformSettingsReq(BaseModel):
    deposits_enabled: bool
    withdrawals_enabled: bool
    withdrawal_min: float = Field(ge=1, le=1_000_000)


class PredictionModeReq(BaseModel):
    mode: Literal["auto_least", "manual", "random"]


class ForceResultReq(BaseModel):
    # The engine silently falls back to 7 for anything outside 0-9, which made
    # an out-of-range force look like it had been accepted.
    number: int = Field(ge=0, le=9)


class UserStatusReq(BaseModel):
    status: Literal["active", "disabled"]


class UserGameAccessReq(BaseModel):
    enabled: bool


class AddQRReq(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: Optional[str] = "Scan with any UPI App"
    qr_url: str
    upi_id: Optional[str] = None
    min_amount: float = Field(default=100, ge=1)
    max_amount: float = Field(default=50000, ge=1)

    @field_validator("qr_url")
    @classmethod
    def _safe_qr_url(cls, value: str) -> str:
        value = value.strip()
        # This string is rendered as an <img src> in the player's browser, so a
        # javascript: or data: URL here would be stored XSS on the deposit page.
        if not (value.startswith("https://") or value.startswith("/uploads/qr/")):
            raise ValueError("QR URL must be an https:// link or an uploaded /uploads/qr/ path")
        return value

    @field_validator("max_amount")
    @classmethod
    def _ordered_limits(cls, value: float, info) -> float:
        minimum = info.data.get("min_amount")
        if minimum is not None and value < minimum:
            raise ValueError("max_amount must be greater than or equal to min_amount")
        return value
