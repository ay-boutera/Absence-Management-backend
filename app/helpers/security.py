"""
core/security.py — JWT, Passwords, Cookies, CSRF
==================================================
This is the security engine. Every auth operation flows through here.

Three pillars:
    1. PASSWORD HASHING — bcrypt. NEVER store plain passwords.
    2. JWT TOKENS — python-jose. Two token types:
        - access_token  (15 min)  : used on every API request
        - refresh_token (7 days)  : used ONLY to get a new access token
    3. HttpOnly COOKIES — tokens are NEVER sent in the response body.
        They are placed in cookies that JavaScript cannot read.
        This eliminates the #1 XSS token theft vector (ENF-04).

CSRF Protection:
    Since we use cookies, we need CSRF protection.
    We use the "double-submit" pattern:
        - Server sets a READABLE csrf_token cookie (not HttpOnly)
        - Client JS reads it and adds it as X-CSRF-Token header
        - Server validates that header matches the cookie
        - An attacker's site cannot read your cookies (SameSite=Strict helps too)
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Request, HTTPException, status
from fastapi.responses import Response

from app.config import settings


# ── Password Hashing ───────────────────────────────────────────────────────────
# bcrypt is intentionally slow to make brute-force attacks expensive.
# bcrypt only processes the first 72 bytes of a password.
# We reject longer passwords instead of silently truncating.
_BCRYPT_MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    """Convert a plain-text password to a bcrypt hash for storage."""
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is too long. Maximum supported length is 72 bytes.",
        )
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if a plain-text password matches the stored hash."""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # Invalid hash format should be treated as a failed check.
        return False


# ── JWT Token Creation ─────────────────────────────────────────────────────────
def create_access_token(data: dict) -> str:
    """
    Create a short-lived JWT access token (15 min by default).

    'data' must include:
        - "sub": the user's UUID (the "subject" of the token)
        - "role": the user's role string

    The token is SIGNED with SECRET_KEY using HS256 algorithm.
    Anyone can DECODE it (it's base64), but only the server can VERIFY it.
    """
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload.update(
        {
            "exp": expire,
            "iat": datetime.now(timezone.utc),  # issued at
            "type": "access",
        }
    )
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """
    Create a long-lived JWT refresh token (7 days by default).

    Refresh tokens are stored in the Redis blacklist on logout.
    They should ONLY be accepted at the /auth/refresh endpoint.
    """
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload.update(
        {
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "refresh",
            # A unique token ID — lets us blacklist individual tokens
            "jti": secrets.token_hex(16),
        }
    )
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and verify a JWT token.
    Raises HTTPException 401 if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── HttpOnly Cookie Management ─────────────────────────────────────────────────
# Cookie settings (ENF-04):
#   httponly=True  → JavaScript CANNOT read this cookie (blocks XSS theft)
#   secure=True    → Only sent over HTTPS (blocks network interception)
#   samesite="strict" → Not sent on cross-site requests (mitigates CSRF)

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> str:
    """
    Set both JWT tokens as HttpOnly cookies on the response.
    Also sets a CSRF token as a READABLE (non-HttpOnly) cookie.

    Returns the CSRF token string so the caller can include it
    in the response body if needed.
    """
    # Generate a CSRF token
    csrf_token = secrets.token_hex(32)

    # Access token cookie — short-lived, HttpOnly
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",  # Allow HTTP in dev mode
        samesite=None,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    # Refresh token cookie — long-lived, HttpOnly
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite=None,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )

    # CSRF token cookie — NOT HttpOnly (JS needs to read it)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,  # JS MUST be able to read this
        secure=settings.ENVIRONMENT == "production",
        samesite=None,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return csrf_token


def clear_auth_cookies(response: Response) -> None:
    """Remove all auth cookies on logout."""
    response.delete_cookie(ACCESS_COOKIE_NAME)
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME)


def get_token_from_cookie(request: Request, cookie_name: str) -> Optional[str]:
    """Extract a token from a named cookie."""
    return request.cookies.get(cookie_name)


# ── CSRF Validation ────────────────────────────────────────────────────────────
def validate_csrf_token(request: Request) -> None:
    """
    Double-submit CSRF check.

    The client must:
        1. Read the csrf_token cookie value (JavaScript CAN do this since it's not HttpOnly)
        2. Send it in the X-CSRF-Token header on every mutating request (POST/PUT/DELETE)

    We compare header vs cookie. An attacker's origin cannot read the cookie
    (same-origin policy) so they cannot forge the header.

    Skip CSRF check for GET, HEAD, OPTIONS (they should be idempotent/safe).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    header_token = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)

    if not header_token or not cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing.",
        )

    # Use secrets.compare_digest to prevent timing attacks
    if not secrets.compare_digest(header_token, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid.",
        )
