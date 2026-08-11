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


class TeamCreateRequest(BaseModel):
    phone: str = Field(min_length=4, max_length=32)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    # Target win rate 0-100 for single-player games.
    win_rate: float = Field(default=80, ge=0, le=100)
    # Optional group to drop them into at creation, so an admin does not have
    # to make the account and then go and assign it as a second step.
    group_id: Optional[str] = Field(default=None, max_length=64)


class TeamUpdateRequest(BaseModel):
    win_rate: float = Field(ge=0, le=100)
    # Portal access, independent of the win rate. None leaves it as it is, so
    # a dashboard that only sends a win rate cannot revoke someone's login.
    is_employee: Optional[bool] = Field(default=None)
    # Which group they belong to. None leaves it alone; "" clears it. The two
    # have to be distinguishable, which is why unassigning is not just None.
    group_id: Optional[str] = Field(default=None, max_length=64)


class GroupRequest(BaseModel):
    """Create or rename a staff group."""

    name: str = Field(min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=200)


class EmployeeLoginRequest(BaseModel):
    phone: str = Field(min_length=4, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class EmployeeRegisterRequest(BaseModel):
    """A self-signup from the portal. Grants nothing until an admin approves."""

    phone: str = Field(min_length=4, max_length=32)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    # Free text the applicant can leave for whoever reviews the queue.
    note: Optional[str] = Field(default=None, max_length=200)


class TeamReviewRequest(BaseModel):
    """An admin's decision on a pending signup."""

    note: Optional[str] = Field(default=None, max_length=200)
    # Applied on approval only, so the account arrives configured rather than
    # needing a second edit straight afterwards.
    win_rate: float = Field(default=80, ge=0, le=100)
    group_id: Optional[str] = Field(default=None, max_length=64)


class TeamDetailsRequest(BaseModel):
    """Edit an employee's name and login number."""

    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    phone: Optional[str] = Field(default=None, min_length=4, max_length=32)


class TeamPasswordRequest(BaseModel):
    """Set an employee's password.

    There is no "read the current one" counterpart and there cannot be: they
    are stored as PBKDF2 hashes, which are one-way by design. The dashboard
    shows what it just set instead, once, so an admin can pass it on.
    """

    password: str = Field(min_length=6, max_length=128)


class TeamStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


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
    deposit_min: float = Field(default=500, ge=1, le=10_000_000)
    deposit_max: float = Field(default=50_000, ge=1, le=10_000_000)
    withdrawal_min: float = Field(ge=1, le=1_000_000)
    withdrawal_max: float = Field(default=100_000, ge=1, le=10_000_000)
    # Approved deposits an account needs before it can withdraw. 0 is off.
    withdrawal_min_deposit: float = Field(default=500, ge=0, le=10_000_000)
    # Shown to a player who tries before then. Optional so a dashboard that
    # does not send it keeps whatever wording is already saved.
    withdrawal_locked_message: str | None = Field(default=None, max_length=600)
    # Shown to a player whose wallet is below withdrawal_min. Optional for the
    # same reason.
    withdrawal_min_message: str | None = Field(default=None, max_length=600)


class BonusRunSettingsReq(BaseModel):
    """The signup-bonus run (backend/luck.py), as the dashboard edits it."""

    enabled: bool
    win_rate: float = Field(default=60, ge=0, le=100)
    signup_bonus: float = Field(default=100, ge=0, le=1_000_000)
    target_min: float = Field(default=1_700, ge=0, le=10_000_000)
    target_max: float = Field(default=3_000, ge=0, le=10_000_000)


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
