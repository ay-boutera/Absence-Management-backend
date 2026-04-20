from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole, settings
from app.db import get_db
from app.helpers.role_users import RoleUser, get_user_by_id, user_role
from app.helpers.security import ACCESS_COOKIE_NAME, decode_token, get_token_from_cookie
from app.models import Admin, Teacher


async def _resolve_user_from_token(token: str, db: AsyncSession) -> RoleUser:
    payload = decode_token(token)

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

    try:
        parsed_user_id = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await get_user_by_id(db, parsed_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_role = payload.get("role")
    if token_role and token_role != user_role(user).value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token role does not match user role.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RoleUser:
    token = get_token_from_cookie(request, ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_user_from_token(token, db)


async def require_active_user(
    request: Request,
    current_user: RoleUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleUser:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact the administration.",
        )

    if current_user.last_activity:
        inactivity_limit = timedelta(minutes=settings.SESSION_INACTIVITY_MINUTES)
        last_activity = current_user.last_activity
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        time_since_activity = datetime.now(timezone.utc) - last_activity
        if time_since_activity > inactivity_limit:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired due to inactivity. Please log in again.",
            )

    current_user.last_activity = datetime.now(timezone.utc)
    db.add(current_user)
    return current_user


async def require_admin(current_user: RoleUser = Depends(require_active_user)) -> RoleUser:
    if user_role(current_user) != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator role required.",
        )
    return current_user


async def require_super_admin(current_user: RoleUser = Depends(require_active_user)) -> RoleUser:
    if user_role(current_user) != UserRole.ADMIN or not isinstance(current_user, Admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator role required.",
        )

    if (current_user.admin_level or "regular").lower() != "super":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Super admin role required.",
        )

    return current_user


async def require_teacher(current_user: RoleUser = Depends(require_active_user)) -> RoleUser:
    if user_role(current_user) != UserRole.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Teacher role required.",
        )
    return current_user


async def require_student(current_user: RoleUser = Depends(require_active_user)) -> RoleUser:
    if user_role(current_user) != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Student role required.",
        )
    return current_user


async def require_admin_or_teacher(
    current_user: RoleUser = Depends(require_active_user),
) -> RoleUser:
    if user_role(current_user) not in (UserRole.ADMIN, UserRole.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator or Teacher role required.",
        )
    return current_user


def require_role(allowed_role: UserRole) -> Callable:
    async def role_dependency(current_user: RoleUser = Depends(require_active_user)) -> RoleUser:
        if user_role(current_user) != allowed_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. {allowed_role.value} role required.",
            )
        return current_user

    return role_dependency


async def get_current_user_bearer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RoleUser:
    authorization = request.headers.get("Authorization")
    token = ""

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Authorization header format.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        token = get_token_from_cookie(request, ACCESS_COOKIE_NAME) or ""
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    user = await _resolve_user_from_token(token, db)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact the administration.",
        )
    return user


async def require_admin_bearer(
    current_user: RoleUser = Depends(get_current_user_bearer),
) -> RoleUser:
    if user_role(current_user) != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator role required.",
        )
    return current_user


async def require_admin_or_teacher_bearer(
    current_user: RoleUser = Depends(get_current_user_bearer),
) -> RoleUser:
    if user_role(current_user) not in (UserRole.ADMIN, UserRole.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator or Teacher role required.",
        )
    return current_user


async def require_can_import_data_bearer(
    current_user: RoleUser = Depends(get_current_user_bearer),
) -> RoleUser:
    if user_role(current_user) != UserRole.ADMIN or not isinstance(current_user, Admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only administrators can import data.",
        )

    if not current_user.can_import_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Import permission is disabled for this admin account.",
        )

    return current_user


async def require_can_export_data_bearer(
    current_user: RoleUser = Depends(get_current_user_bearer),
) -> RoleUser:
    role = user_role(current_user)

    if role == UserRole.ADMIN:
        if isinstance(current_user, Admin) and not current_user.can_export_data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Export permission is disabled for this admin account.",
            )
        return current_user

    if role == UserRole.TEACHER:
        if isinstance(current_user, Teacher) and not current_user.can_export_data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Export permission is disabled for this teacher account.",
            )
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied. Administrator or Teacher role required.",
    )
