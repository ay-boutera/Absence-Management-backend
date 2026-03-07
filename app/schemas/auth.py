import re
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED
# ═══════════════════════════════════════════════════════════════════════════════


class MessageResponse(BaseModel):
    message: str


class TokenRefreshResponse(BaseModel):
    message: str = "Token refreshed successfully"


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIAL AUTH SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Password complexity rule ───────────────────────────────────────────────────
# EF-04: ≥8 chars, at least 1 uppercase, 1 digit, 1 special character
_PASSWORD_RE = re.compile(r'^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>]).{8,}$')


def validate_password_complexity(password: str) -> str:
    if not _PASSWORD_RE.match(password):
        raise ValueError(
            "Password must be at least 8 characters and contain "
            "at least one uppercase letter, one digit, and one special character."
        )
    return password


# ── FR-01: Login ──────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    """Credential login: email (admin/teacher) OR student matricule + password."""

    identifier: str  # email or student_id
    password: str


class LoginResponse(BaseModel):
    """
    Returned after any successful login (credential OR OAuth).
    JWT tokens are in HttpOnly cookies — never in this body.
    """

    message: str = "Login successful"
    user_id: str
    role: str
    full_name: str
    avatar_url: Optional[str] = None


# ── FR-04: Password Reset ─────────────────────────────────────────────────────
class PasswordResetRequest(BaseModel):
    """Step 1 — user submits their email to receive a reset link."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Step 2 — user submits the token from the email + their new password."""

    token: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordResetConfirm":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


# ── Change Password (authenticated) ──────────────────────────────────────────
class ChangePasswordRequest(BaseModel):
    """Authenticated credential user changing their own password."""

    current_password: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class OAuthStateResponse(BaseModel):
    """
    Returned by GET /auth/google.
    The frontend uses this URL to redirect the browser to Google's consent screen.
    """

    authorization_url: str


class OAuthLoginResponse(BaseModel):
    """
    Returned after Google calls back to /auth/google/callback.
    Same shape as LoginResponse — the frontend handles both identically.
    is_new_user=True lets the frontend show a "Welcome!" message on first login.
    """

    message: str = "Login successful"
    user_id: str
    role: str
    full_name: str
    avatar_url: Optional[str] = None
    is_new_user: bool = False  # True on first-ever Google login
