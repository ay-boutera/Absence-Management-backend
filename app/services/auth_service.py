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

import re
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, select
from fastapi import HTTPException, status

from app.models import Account, PasswordResetToken, UserRole, AuditLog, ActionType
from app.models.user import Admin, Teacher, Student
from app.schemas import (
    AdminAccountUpdate,
    AccountCreate,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    StudentAccountUpdate,
    TeacherAccountUpdate,
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


logger = logging.getLogger(__name__)


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
        normalized_user_id: Optional[UUID] = None
        if user_id is not None:
            if isinstance(user_id, UUID):
                normalized_user_id = user_id
            else:
                try:
                    normalized_user_id = UUID(str(user_id))
                except ValueError:
                    normalized_user_id = None

        log = AuditLog(
            user_id=normalized_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(log)
        await self.db.flush()
        # The actual commit happens when get_db() exits (after the route handler)

    # ── FR-01: Login ──────────────────────────────────────────────────────────
    async def login(
        self,
        credentials: LoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[Account, str, str]:
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

        # Look up by email OR by student_id (via JOIN with specialized student model)
        # We do a LEFT JOIN so that a non-student user is still found by email.

        result = await self.db.execute(
            select(Account)
            .outerjoin(Student, Account.id == Student.user_id)
            .where(
                or_(
                    Account.email == credentials.identifier,
                    Student.student_id == credentials.identifier,
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
        data: AccountCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        allow_full_firstname_email: bool = False,
    ) -> Account:
        """
        Create a new credential-based user account.
        1. Validates email domain
        2. Checks if email is already taken
        3. Hashes password
        4. Saves user to DB
        """
        if allow_full_firstname_email:
            email = data.email.strip().lower()
            loose_pattern = re.compile(r"^[a-z]+\.[a-z]+(-[a-z]+)*@esi-sba\.dz$")
            if not loose_pattern.match(email):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Access is restricted to ESI-SBA institutional accounts. "
                        "Your email must follow the format: first.last@esi-sba.dz "
                    ),
                )
        else:
            validate_esi_email(data.email)

        # 2. Duplicate check
        result = await self.db.execute(select(Account).where(Account.email == data.email))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered.",
            )

        if data.role == UserRole.STUDENT and data.student_id:
            student_result = await self.db.execute(
                select(Student).where(Student.student_id == data.student_id)
            )
            if student_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Student ID is already registered.",
                )

        if data.role == UserRole.TEACHER and data.employee_id:
            teacher_result = await self.db.execute(
                select(Teacher).where(Teacher.employee_id == data.employee_id)
            )
            if teacher_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Employee ID is already registered.",
                )

        # 3. Create user
        new_user = Account(
            email=data.email,
            hashed_password=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            role=data.role,
            is_active=True,
        )
        self.db.add(new_user)
        await self.db.flush()

        if data.role == UserRole.ADMIN:
            self.db.add(
                Admin(
                    user_id=new_user.id,
                    department=data.department or "Administration",
                    admin_level=data.admin_level or "regular",
                )
            )
        elif data.role == UserRole.TEACHER:
            self.db.add(
                Teacher(
                    user_id=new_user.id,
                    employee_id=data.employee_id,
                    specialization=data.specialization,
                )
            )
        else:
            self.db.add(
                Student(
                    user_id=new_user.id,
                    student_id=data.student_id,
                    program=data.program,
                    level=data.level,
                    group=data.group,
                )
            )
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

    # ── Account Management (Admin) ───────────────────────────────────────────
    async def get_account_by_id(self, account_id: UUID) -> Account:
        result = await self.db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found.",
            )
        return account

    async def _apply_common_account_updates(self, account: Account, payload: dict) -> None:
        if "email" in payload:
            if not payload["email"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email cannot be empty.",
                )
            email = validate_esi_email(payload["email"])
            if email != account.email:
                email_result = await self.db.execute(
                    select(Account).where(
                        and_(Account.email == email, Account.id != account.id)
                    )
                )
                if email_result.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Email is already registered.",
                    )
            account.email = email

        for field in ("first_name", "last_name"):
            if field in payload:
                value = payload[field]
                if value is None or not str(value).strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"{field} cannot be empty.",
                    )
                setattr(account, field, str(value).strip())

        if "phone" in payload:
            phone = payload["phone"]
            account.phone = None if phone is None else str(phone).strip() or None

    async def update_student_account(
        self, account_id: UUID, data: StudentAccountUpdate
    ) -> Account:
        account = await self.get_account_by_id(account_id)
        if account.role != UserRole.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not a student account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(account, payload)

        student_result = await self.db.execute(
            select(Student).where(Student.user_id == account.id)
        )
        student_profile = student_result.scalar_one_or_none()
        if student_profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student profile not found for this account.",
            )

        for required_field in ("student_id", "program", "level"):
            if required_field in payload and (
                payload[required_field] is None
                or not str(payload[required_field]).strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{required_field} cannot be empty.",
                )

        new_student_id = (
            str(payload["student_id"]).strip()
            if "student_id" in payload and payload["student_id"] is not None
            else None
        )
        if new_student_id and new_student_id != student_profile.student_id:
            duplicate_student = await self.db.execute(
                select(Student).where(
                    and_(
                        Student.student_id == new_student_id,
                        Student.user_id != account.id,
                    )
                )
            )
            if duplicate_student.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Student ID is already registered.",
                )

        student_fields = {"student_id", "program", "level", "group"} & payload.keys()
        for field in student_fields:
            value = payload[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(student_profile, field, value)

        self.db.add(student_profile)
        self.db.add(account)
        await self.db.flush()
        return account

    async def update_teacher_account(
        self, account_id: UUID, data: TeacherAccountUpdate
    ) -> Account:
        account = await self.get_account_by_id(account_id)
        if account.role != UserRole.TEACHER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not a teacher account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(account, payload)

        teacher_result = await self.db.execute(
            select(Teacher).where(Teacher.user_id == account.id)
        )
        teacher_profile = teacher_result.scalar_one_or_none()
        if teacher_profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Teacher profile not found for this account.",
            )

        if "employee_id" in payload and (
            payload["employee_id"] is None
            or not str(payload["employee_id"]).strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="employee_id cannot be empty.",
            )

        new_employee_id = (
            str(payload["employee_id"]).strip()
            if "employee_id" in payload and payload["employee_id"] is not None
            else None
        )
        if new_employee_id and new_employee_id != teacher_profile.employee_id:
            duplicate_teacher = await self.db.execute(
                select(Teacher).where(
                    and_(
                        Teacher.employee_id == new_employee_id,
                        Teacher.user_id != account.id,
                    )
                )
            )
            if duplicate_teacher.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Employee ID is already registered.",
                )

        teacher_fields = {"employee_id", "specialization"} & payload.keys()
        for field in teacher_fields:
            value = payload[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(teacher_profile, field, value)

        self.db.add(teacher_profile)
        self.db.add(account)
        await self.db.flush()
        return account

    async def update_admin_account(
        self, account_id: UUID, data: AdminAccountUpdate
    ) -> Account:
        account = await self.get_account_by_id(account_id)
        if account.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not an admin account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(account, payload)

        admin_result = await self.db.execute(
            select(Admin).where(Admin.user_id == account.id)
        )
        admin_profile = admin_result.scalar_one_or_none()
        if admin_profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Admin profile not found for this account.",
            )

        for required_field in ("department", "admin_level"):
            if required_field in payload and (
                payload[required_field] is None
                or not str(payload[required_field]).strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{required_field} cannot be empty.",
                )

        admin_fields = {"department", "admin_level"} & payload.keys()
        for field in admin_fields:
            value = payload[field]
            if isinstance(value, str):
                value = value.strip()
            setattr(admin_profile, field, value)

        self.db.add(admin_profile)
        self.db.add(account)
        await self.db.flush()
        return account

    async def set_account_active_state(self, account_id: UUID, is_active: bool) -> Account:
        account = await self.get_account_by_id(account_id)
        account.is_active = is_active
        self.db.add(account)
        await self.db.flush()
        return account

    # ── FR-01: Logout ─────────────────────────────────────────────────────────

    async def logout(
        self,
        access_token: str,
        refresh_token: Optional[str],
        user: Account,
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
        result = await self.db.execute(select(Account).where(Account.email == email))
        user = result.scalar_one_or_none()

        if user and user.is_active:
            # Generate a cryptographically secure random token
            # secrets.token_urlsafe(32) = 43 URL-safe base64 characters
            raw_token = secrets.token_urlsafe(32)

            try:
                full_name = f"{user.first_name} {user.last_name}"
                email_sent = await send_password_reset_email(
                    str(user.email), full_name, raw_token
                )
            except Exception:
                logger.exception(
                    "Password reset email delivery failed for user_id=%s", user.id
                )
                return

            if not email_sent:
                logger.warning(
                    "Password reset email not sent for user_id=%s", user.id
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Password reset email could not be delivered right now. "
                        "Please verify your email address and try again in a few minutes."
                    ),
                )

            # Persist token only after successful email dispatch.
            reset_token = PasswordResetToken(
                user_id=user.id,
                token=raw_token,
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES),
            )
            self.db.add(reset_token)

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
            select(Account).where(Account.id == reset_token.user_id)
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
        user: Account,
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
