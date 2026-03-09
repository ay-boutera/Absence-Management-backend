"""
services/auth_service.py — Authentication Business Logic
=========================================================
This service layer sits between the router (HTTP) and the database.
The router handles HTTP concerns (cookies, status codes).
The service handles business logic (validate credentials, create tokens, etc.).

Why a service layer?
    - Keeps routers thin (easy to read)
    - Business logic is testable without HTTP context
    - Logic can be reused across multiple routers
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from fastapi import HTTPException, status

from app.models import User, PasswordResetToken, UserRole, AuditLog, ActionType
from app.schemas import (
    LoginRequest,
    PasswordResetConfirm,
    ChangePasswordRequest,
    UserCreate,
)

from app.helpers.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.helpers.email import validate_esi_email
from app.services.redis_service import RedisService
from app.services.email_service import send_password_reset_email
from app.config import settings


class AuthService:
    """All authentication operations as methods on one class."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.redis = RedisService()

    # ── Internal: Audit Log Helper ────────────────────────────────────────────
    async def _log(
        self,
        action: ActionType,
        user_id: Optional[any] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[any] = None,
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        log = AuditLog(
            user_id=str(user_id) if user_id else None,  # ← str()
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,  # ← str()
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(log)
        await self.db.flush()  # ← flush immédiat
        # The actual commit happens when get_db() exits (after the route handler)

    # ── FR-01: Login ──────────────────────────────────────────────────────────
    async def login(
        self,
        credentials: LoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[User, str, str]:
        """
        Authenticate a user with identifier + password.

        The identifier can be:
            - Email address (for Admin and Teacher)
            - Student matricule / student_id (for Students)

        Returns: (user, access_token, refresh_token)
        Raises: HTTPException 401 on failure
        """
        # ── ESI-SBA email domain check ───────────────────────────────────────
        # Enforce firstletter.lastname@esi-sba.dz for email identifiers.
        # Student matricules (e.g. "20231234") are not emails — skip check.
        if "@" in credentials.identifier:
            validate_esi_email(credentials.identifier)

        # Look up by email OR by student_id (via JOIN with student_profiles)
        # We do a LEFT JOIN so that a user without a student profile is still found
        from app.models.user import StudentProfile

        result = await self.db.execute(
            select(User)
            .outerjoin(StudentProfile, User.id == StudentProfile.user_id)
            .where(
                or_(
                    User.email == credentials.identifier,
                    StudentProfile.student_id == credentials.identifier,
                )
            )
        )
        user = result.scalar_one_or_none()

        # IMPORTANT: Always run verify_password even if user not found.
        # This prevents timing attacks that could reveal valid usernames.
        # (If we returned immediately when user is None, the response would
        #  be faster, and an attacker could tell the user doesn't exist.)
        dummy_hash = "$2b$12$LQv3c1yqBWVHxkd0Lq3uQuE3EzJ7Z6kK.W1uK6nK.W1uK6nK.W1uK"
        stored_hash = user.hashed_password if user else dummy_hash

        password_ok = verify_password(credentials.password, stored_hash)

        if not user or not password_ok:
            # Log the failed attempt (don't reveal whether user exists)
            await self._log(
                ActionType.LOGIN_FAILED,
                details=f"Failed login attempt for identifier: {credentials.identifier}",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        if not user.is_active:
            await self._log(
                ActionType.LOGIN_FAILED,
                user_id=user.id,
                details="Login attempt on deactivated account",
                ip_address=ip_address,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated. Contact the administration.",
            )

        # Create tokens
        token_data = {"sub": str(user.id), "role": user.role.value}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        # Update last activity
        user.last_activity = datetime.now(timezone.utc)
        self.db.add(user)

        # Audit log
        await self._log(
            ActionType.LOGIN_SUCCESS,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return user, access_token, refresh_token

    # ── FR-01: Registration ───────────────────────────────────────────────────
    async def register(
        self,
        data: UserCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> User:
        """
        Create a new credential-based user account.
        1. Validates email domain
        2. Checks if email is already taken
        3. Hashes password
        4. Saves user to DB
        """
        validate_esi_email(data.email)

        # 2. Duplicate check
        result = await self.db.execute(select(User).where(User.email == data.email))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered.",
            )

        # 3. Create user
        new_user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            role=data.role,
            is_active=True,
        )
        self.db.add(new_user)
        await self.db.flush()

        # 4. Audit log
        await self._log(
            ActionType.ACCOUNT_CREATED,
            user_id=new_user.id,
            resource_type="user",
            resource_id=new_user.id,
            details=f"Manual registration: {new_user.email}",
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return new_user

    # ── FR-01: Logout ─────────────────────────────────────────────────────────

    async def logout(
        self,
        access_token: str,
        refresh_token: Optional[str],
        user: User,
        ip_address: Optional[str] = None,
    ) -> None:
        """
        Invalidate both tokens by adding them to the Redis blacklist.
        After this, neither token is usable — even if they haven't expired yet.
        """
        await self.redis.blacklist_token(access_token)
        if refresh_token:
            await self.redis.blacklist_token(refresh_token)

        await self._log(
            ActionType.LOGOUT,
            user_id=user.id,
            ip_address=ip_address,
        )

    # ── FR-01: Refresh Token ──────────────────────────────────────────────────
    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """
        Exchange a valid refresh token for a new access token + refresh token.
        The old refresh token is blacklisted (token rotation).

        Returns: (new_access_token, new_refresh_token)
        """
        # Validate the refresh token
        payload = decode_token(refresh_token)

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type.",
            )

        # Check it hasn't been revoked
        if await self.redis.is_token_blacklisted(refresh_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked.",
            )

        user_id = payload.get("sub")
        role = payload.get("role")

        # Blacklist the old refresh token (token rotation)
        await self.redis.blacklist_token(refresh_token)

        # Issue new tokens
        token_data = {"sub": user_id, "role": role}
        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)

        await self._log(
            ActionType.TOKEN_REFRESHED,
            user_id=user_id,
        )

        return new_access, new_refresh

    # ── FR-04: Request Password Reset ─────────────────────────────────────────
    async def request_password_reset(
        self,
        email: str,
        ip_address: Optional[str] = None,
    ) -> None:
        """
        Generate a single-use reset token and email it to the user.

        IMPORTANT: This function always returns 200 OK, even if the email
        doesn't exist in the database. This prevents user enumeration attacks
        (an attacker could learn which emails are registered if we return
        different responses for found/not found).
        """
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user and user.is_active:
            # Generate a cryptographically secure random token
            # secrets.token_urlsafe(32) = 43 URL-safe base64 characters
            raw_token = secrets.token_urlsafe(32)

            reset_token = PasswordResetToken(
                user_id=user.id,
                token=raw_token,
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES),
            )
            self.db.add(reset_token)

            # Send the email asynchronously
            full_name = f"{user.first_name} {user.last_name}"
            await send_password_reset_email(user.email, full_name, raw_token)

            await self._log(
                ActionType.PASSWORD_RESET_REQUESTED,
                user_id=user.id,
                ip_address=ip_address,
            )

        # Always return success (user enumeration prevention)

    # ── FR-04: Confirm Password Reset ─────────────────────────────────────────
    async def confirm_password_reset(
        self,
        data: PasswordResetConfirm,
        ip_address: Optional[str] = None,
    ) -> None:
        """
        Validate the reset token and set the new password.
        The token is marked as used after this — it cannot be used again.
        """
        result = await self.db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token == data.token)
        )
        reset_token = result.scalar_one_or_none()

        if not reset_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        # Check it's not already used
        if reset_token.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset link has already been used.",
            )

        # Check expiry
        if datetime.now(timezone.utc) > reset_token.expires_at.replace(
            tzinfo=timezone.utc
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset link has expired. Please request a new one.",
            )

        # Load the user
        result = await self.db.execute(
            select(User).where(User.id == reset_token.user_id)
        )
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found or deactivated.",
            )

        # Set the new password
        user.hashed_password = hash_password(data.new_password)
        self.db.add(user)

        # Mark token as used (single-use guarantee)
        reset_token.is_used = True
        self.db.add(reset_token)

        await self._log(
            ActionType.PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            ip_address=ip_address,
        )

    # ── Change Password (authenticated) ───────────────────────────────────────
    async def change_password(
        self,
        user: User,
        data: ChangePasswordRequest,
        ip_address: Optional[str] = None,
    ) -> None:
        """
        Authenticated user changes their own password.
        Requires the current password as confirmation.
        """
        if not verify_password(data.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )

        user.hashed_password = hash_password(data.new_password)
        self.db.add(user)

        await self._log(
            ActionType.PASSWORD_CHANGED,
            user_id=user.id,
            ip_address=ip_address,
        )
