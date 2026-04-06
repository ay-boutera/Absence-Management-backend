"""
core/permissions.py — Role-Based Access Control (RBAC) Dependencies
=====================================================================
Implements FR-02 / EF-02: three roles with strictly defined permissions.

How FastAPI dependencies work:
    A dependency is a function decorated with Depends() in a route.
    FastAPI calls it automatically before the route handler runs.
    If the dependency raises an exception, the route never executes.

Example usage in a route:
    @router.get("/admin-only")
    async def admin_route(current_user: User = Depends(require_admin)):
        ...   # Only admins can reach this code

The dependency chain:
    get_current_user()      — reads & validates the JWT from cookie
        ↓ used by
    require_active_user()   — also checks is_active == True
        ↓ used by
    require_admin()         — also checks role == "admin"
    require_teacher()       — also checks role == "teacher"
    require_student()       — also checks role == "student"
    require_admin_or_teacher() — checks role in ["admin", "teacher"]
"""

from typing import Callable
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone

from app.db import get_db
from app.models.user import User, UserRole
from app.helpers.security import get_token_from_cookie, decode_token, ACCESS_COOKIE_NAME
from app.services.redis_service import RedisService
from app.config import settings

import uuid


# ── Base Dependency: Extract & Validate JWT ────────────────────────────────────
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    1. Extract access_token from the HttpOnly cookie
    2. Decode and verify the JWT
    3. Check the token is not blacklisted (logged out)
    4. Load and return the User from the database
    """
    # Step 1: Get token from cookie
    token = get_token_from_cookie(request, ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    # Step 2: Decode JWT (raises 401 if invalid/expired)
    payload = decode_token(token)

    # Ensure it's an access token (not a refresh token)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing subject.",
        )

    # Step 3: Check Redis blacklist (handles logout)
    redis = RedisService()
    if await redis.is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please log in again.",
        )

    # Step 4: Load user from DB
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    return user


# ── Active User Check ─────────────────────────────────────────────────────────
async def require_active_user(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Builds on get_current_user.
    Additionally checks:
        1. Account is active (not soft-deleted)
        2. Session has not expired due to inactivity (FR-05 / EF-05)
    Also updates last_activity on every authenticated request.
    """
    # Check soft-delete
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact the administration.",
        )

    # Check inactivity timeout (FR-05)
    if current_user.last_activity:
        inactivity_limit = timedelta(minutes=settings.SESSION_INACTIVITY_MINUTES)
        time_since_activity = datetime.now(
            timezone.utc
        ) - current_user.last_activity.replace(tzinfo=timezone.utc)
        if time_since_activity > inactivity_limit:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired due to inactivity. Please log in again.",
            )

    # Update last_activity timestamp
    current_user.last_activity = datetime.now(timezone.utc)
    db.add(current_user)
    # Note: commit happens in get_db() after the route handler returns

    return current_user


# ── Role Dependencies ─────────────────────────────────────────────────────────
async def require_admin(
    current_user: User = Depends(require_active_user),
) -> User:
    """Only Administration role can access routes protected by this dependency."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator role required.",
        )
    return current_user


async def require_teacher(
    current_user: User = Depends(require_active_user),
) -> User:
    """Only Teacher/Invigilator role can access routes protected by this dependency."""
    if current_user.role != UserRole.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Teacher role required.",
        )
    return current_user


async def require_student(
    current_user: User = Depends(require_active_user),
) -> User:
    """Only Student role can access routes protected by this dependency."""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Student role required.",
        )
    return current_user

    return current_user


async def require_admin_or_teacher(
    current_user: User = Depends(require_active_user),
) -> User:
    """Admin OR Teacher can access. Used for routes both roles share."""
    if current_user.role not in (UserRole.ADMIN, UserRole.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator or Teacher role required.",
        )
    return current_user


def require_role(allowed_role: UserRole) -> Callable:
    """Returns a dependency that checks if the user has a specific role."""

    async def role_dependency(
        current_user: User = Depends(require_active_user),
    ) -> User:
        if current_user.role != allowed_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. {allowed_role.value} role required.",
            )
        return current_user

    return role_dependency


async def get_current_user_bearer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Bearer-based auth dependency.
    Expects Authorization: Bearer <access_token>
    """
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    token_role = payload.get("role")
    if not token_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing role.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact the administration.",
        )

    if user.role.value != token_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token role does not match user role.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_admin_bearer(
    current_user: User = Depends(get_current_user_bearer),
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator role required.",
        )
    return current_user


async def require_admin_or_teacher_bearer(
    current_user: User = Depends(get_current_user_bearer),
) -> User:
    if current_user.role not in (UserRole.ADMIN, UserRole.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator or Teacher role required.",
        )
    return current_user
