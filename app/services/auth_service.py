import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.helpers.email import validate_esi_email
from app.helpers.role_users import (
    RoleUser,
    email_exists_for_other,
    get_student_by_student_id,
    get_user_by_email,
    get_user_by_id,
    user_role,
)
from app.helpers.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import ActionType, AuditLog, PasswordResetToken
from app.models.admin import Admin
from app.models.student import Student
from app.models.teacher import Teacher
from app.schemas import (
    AdminAccountCreate,
    AdminAccountUpdate,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    StudentAccountCreate,
    StudentAccountUpdate,
    TeacherAccountCreate,
    TeacherAccountUpdate,
)
from app.services.email_service import send_password_reset_email

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _log(
        self,
        action: ActionType,
        user_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID] = None,
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(log)
        await self.db.flush()

    async def login(
        self,
        credentials: LoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[RoleUser, str, str]:
        identifier = credentials.identifier.strip()

        if "@" in identifier:
            validate_esi_email(identifier)
            user = await get_user_by_email(self.db, identifier)
        else:
            user = await get_student_by_student_id(self.db, identifier)

        dummy_hash = "$2b$12$LQv3c1yqBWVHxkd0Lq3uQuE3EzJ7Z6kK.W1uK6nK.W1uK6nK.W1uK"
        stored_hash = user.hashed_password if user else dummy_hash
        password_ok = verify_password(credentials.password, stored_hash)

        if not user or not password_ok:
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

        role = user_role(user)
        token_data = {"sub": str(user.id), "role": role.value}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        user.last_activity = datetime.now(timezone.utc)
        self.db.add(user)

        await self._log(
            ActionType.LOGIN_SUCCESS,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return user, access_token, refresh_token

    async def _prepare_registration_email(
        self,
        email_value: str,
        *,
        allow_full_firstname_email: bool = False,
    ) -> str:
        email = email_value.strip().lower()
        if allow_full_firstname_email:
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
            validate_esi_email(email)

        existing = await get_user_by_email(self.db, email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered.",
            )
        return email

    @staticmethod
    def _build_common_registration_kwargs(
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        phone: Optional[str],
    ) -> dict:
        return {
            "email": email,
            "hashed_password": hash_password(password),
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "phone": None if phone is None else str(phone).strip() or None,
            "is_active": True,
        }

    async def _log_account_created(
        self,
        user: RoleUser,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        await self._log(
            ActionType.ACCOUNT_CREATED,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            details=f"Manual registration: {user.email}",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def register_student(
        self,
        data: StudentAccountCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Student:
        email = await self._prepare_registration_email(str(data.email))
        student_id = str(data.student_id).strip()
        if not student_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="student_id cannot be empty.",
            )

        existing_student = await get_student_by_student_id(self.db, student_id)
        if existing_student is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Student ID is already registered.",
            )

        user = Student(
            **self._build_common_registration_kwargs(
                email=email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                phone=data.phone,
            ),
            student_id=student_id,
            program=str(data.program).strip(),
            level=str(data.level).strip(),
            group=None if data.group is None else str(data.group).strip() or None,
            can_submit_justifications=data.can_submit_justifications,
            can_view_attendance=data.can_view_attendance,
            can_confirm_rattrapage=data.can_confirm_rattrapage,
            is_enrolled=data.is_enrolled,
        )
        self.db.add(user)
        await self.db.flush()
        await self._log_account_created(user, ip_address=ip_address, user_agent=user_agent)
        return user

    async def register_teacher(
        self,
        data: TeacherAccountCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Teacher:
        email = await self._prepare_registration_email(str(data.email))
        employee_id = str(data.employee_id).strip()
        if not employee_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="employee_id cannot be empty.",
            )

        result = await self.db.execute(
            select(Teacher).where(Teacher.employee_id == employee_id)
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee ID is already registered.",
            )

        user = Teacher(
            **self._build_common_registration_kwargs(
                email=email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                phone=data.phone,
            ),
            employee_id=employee_id,
            specialization=(
                None
                if data.specialization is None
                else str(data.specialization).strip() or None
            ),
            can_mark_attendance=data.can_mark_attendance,
            can_export_data=data.can_export_data,
            can_correct_attendance=data.can_correct_attendance,
            correction_window_minutes=data.correction_window_minutes,
        )
        self.db.add(user)
        await self.db.flush()
        await self._log_account_created(user, ip_address=ip_address, user_agent=user_agent)
        return user

    async def register_admin(
        self,
        data: AdminAccountCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        *,
        allow_full_firstname_email: bool = False,
        forced_admin_level: Optional[str] = None,
    ) -> Admin:
        email = await self._prepare_registration_email(
            str(data.email),
            allow_full_firstname_email=allow_full_firstname_email,
        )
        admin_level = forced_admin_level
        if admin_level is None:
            admin_level = (
                None if data.admin_level is None else str(data.admin_level).strip() or None
            )
        admin_level = admin_level or "regular"

        user = Admin(
            **self._build_common_registration_kwargs(
                email=email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                phone=data.phone,
            ),
            department=(
                None if data.department is None else str(data.department).strip() or None
            )
            or "Administration",
            admin_level=admin_level,
            can_import_data=data.can_import_data,
            can_export_data=data.can_export_data,
            can_manage_users=data.can_manage_users,
            can_manage_system_config=data.can_manage_system_config,
            can_view_audit_logs=data.can_view_audit_logs,
        )
        self.db.add(user)
        await self.db.flush()
        await self._log_account_created(user, ip_address=ip_address, user_agent=user_agent)
        return user

    async def get_account_by_id(self, account_id: UUID) -> RoleUser:
        user = await get_user_by_id(self.db, account_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found.",
            )
        return user

    async def _apply_common_account_updates(self, user: RoleUser, payload: dict) -> None:
        if "email" in payload:
            if not payload["email"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email cannot be empty.",
                )
            email = validate_esi_email(payload["email"])
            if await email_exists_for_other(self.db, email, user.id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email is already registered.",
                )
            user.email = email

        for field in ("first_name", "last_name"):
            if field in payload:
                value = payload[field]
                if value is None or not str(value).strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"{field} cannot be empty.",
                    )
                setattr(user, field, str(value).strip())

        if "phone" in payload:
            phone = payload["phone"]
            user.phone = None if phone is None else str(phone).strip() or None

    async def update_student_account(
        self, account_id: UUID, data: StudentAccountUpdate
    ) -> RoleUser:
        user = await self.get_account_by_id(account_id)
        if not isinstance(user, Student):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not a student account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(user, payload)

        for required_field in ("student_id", "program", "level"):
            if required_field in payload and (
                payload[required_field] is None or not str(payload[required_field]).strip()
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
        if new_student_id and new_student_id != user.student_id:
            duplicate_student = await get_student_by_student_id(self.db, new_student_id)
            if duplicate_student is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Student ID is already registered.",
                )

        for field in {
            "student_id",
            "program",
            "level",
            "group",
            "can_submit_justifications",
            "can_view_attendance",
            "can_confirm_rattrapage",
            "is_enrolled",
        } & payload.keys():
            value = payload[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(user, field, value)

        self.db.add(user)
        await self.db.flush()
        return user

    async def update_teacher_account(
        self, account_id: UUID, data: TeacherAccountUpdate
    ) -> RoleUser:
        user = await self.get_account_by_id(account_id)
        if not isinstance(user, Teacher):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not a teacher account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(user, payload)

        if "employee_id" in payload and (
            payload["employee_id"] is None or not str(payload["employee_id"]).strip()
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
        if new_employee_id and new_employee_id != user.employee_id:
            duplicate_teacher = await self.db.execute(
                select(Teacher).where(
                    and_(Teacher.employee_id == new_employee_id, Teacher.id != user.id)
                )
            )
            if duplicate_teacher.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Employee ID is already registered.",
                )

        if "correction_window_minutes" in payload and (
            payload["correction_window_minutes"] is None
            or int(payload["correction_window_minutes"]) <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="correction_window_minutes must be a positive integer.",
            )

        for field in {
            "employee_id",
            "specialization",
            "can_mark_attendance",
            "can_export_data",
            "can_correct_attendance",
            "correction_window_minutes",
        } & payload.keys():
            value = payload[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(user, field, value)

        self.db.add(user)
        await self.db.flush()
        return user

    async def update_admin_account(
        self, account_id: UUID, data: AdminAccountUpdate
    ) -> RoleUser:
        user = await self.get_account_by_id(account_id)
        if not isinstance(user, Admin):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target account is not an admin account.",
            )

        payload = data.model_dump(exclude_unset=True)
        await self._apply_common_account_updates(user, payload)

        for required_field in ("department", "admin_level"):
            if required_field in payload and (
                payload[required_field] is None or not str(payload[required_field]).strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{required_field} cannot be empty.",
                )

        for field in {
            "department",
            "admin_level",
            "can_import_data",
            "can_export_data",
            "can_manage_users",
            "can_manage_system_config",
            "can_view_audit_logs",
        } & payload.keys():
            value = payload[field]
            if isinstance(value, str):
                value = value.strip()
            setattr(user, field, value)

        self.db.add(user)
        await self.db.flush()
        return user

    async def set_account_active_state(self, account_id: UUID, is_active: bool) -> RoleUser:
        user = await self.get_account_by_id(account_id)
        user.is_active = is_active
        self.db.add(user)
        await self.db.flush()
        return user

    async def logout(
        self,
        access_token: str,
        refresh_token: Optional[str],
        user: RoleUser,
        ip_address: Optional[str] = None,
    ) -> None:
        await self._log(
            ActionType.LOGOUT,
            user_id=user.id,
            ip_address=ip_address,
        )

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        payload = decode_token(refresh_token)

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type.",
            )

        user_id = payload.get("sub")
        role = payload.get("role")
        if not user_id or not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token payload.",
            )

        user = await get_user_by_id(self.db, UUID(user_id))
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive.",
            )

        token_data = {"sub": user_id, "role": role}
        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)

        await self._log(ActionType.TOKEN_REFRESHED, user_id=user.id)
        return new_access, new_refresh

    async def request_password_reset(
        self,
        email: str,
        ip_address: Optional[str] = None,
    ) -> None:
        user = await get_user_by_email(self.db, email)

        if user and user.is_active:
            raw_token = secrets.token_urlsafe(32)

            try:
                full_name = f"{user.first_name} {user.last_name}"
                email_sent = await send_password_reset_email(str(user.email), full_name, raw_token)
            except Exception:
                logger.exception("Password reset email delivery failed for user_id=%s", user.id)
                return

            if not email_sent:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Password reset email could not be delivered right now. "
                        "Please verify your email address and try again in a few minutes."
                    ),
                )

            reset_token = PasswordResetToken(
                user_id=user.id,
                role=user_role(user),
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

    async def confirm_password_reset(
        self,
        data: PasswordResetConfirm,
        ip_address: Optional[str] = None,
    ) -> None:
        result = await self.db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token == data.token)
        )
        reset_token = result.scalar_one_or_none()

        if not reset_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        if reset_token.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset link has already been used.",
            )

        expires_at = reset_token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset link has expired. Please request a new one.",
            )

        user = await get_user_by_id(self.db, reset_token.user_id)
        if not user or not user.is_active or user_role(user) != reset_token.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found or deactivated.",
            )

        user.hashed_password = hash_password(data.new_password)
        self.db.add(user)

        reset_token.is_used = True
        self.db.add(reset_token)

        await self._log(
            ActionType.PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            ip_address=ip_address,
        )

    async def change_password(
        self,
        user: RoleUser,
        data: ChangePasswordRequest,
        ip_address: Optional[str] = None,
    ) -> None:
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
