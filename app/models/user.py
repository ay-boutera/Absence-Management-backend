"""
models/user.py — Account & Role Models
======================================
Supports TWO login methods simultaneously:
    1. Credential login  — email + password  (hashed_password column)
    2. Google OAuth      — Google account     (google_id column)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config import UserRole


# ── Shared Auth Account Model ──────────────────────────────────────────────────
class Account(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # ── Core identity ─────────────────────────────────────────────────────────
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=True)

    # ── Credential login (nullable — absent if user only uses Google OAuth) ───
    hashed_password = Column(String(255), nullable=True)

    # ── Google OAuth (nullable — absent if user only uses credentials) ────────
    google_id = Column(String(255), unique=True, nullable=True, index=True)

    # Profile photo URL returned by Google (used in the UI navbar)
    avatar_url = Column(String(500), nullable=True)

    # ── Role & status ─────────────────────────────────────────────────────────
    role = Column(Enum(UserRole), nullable=False, default=UserRole.STUDENT)
    is_active = Column(Boolean, default=True, nullable=False)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_activity = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    admin_profile = relationship("Admin", back_populates="user", uselist=False)
    teacher_profile = relationship("Teacher", back_populates="user", uselist=False)
    student_profile = relationship("StudentUser", back_populates="user", uselist=False)
    password_reset_tokens = relationship("PasswordResetToken", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")
    import_export_logs = relationship("ImportExportLog", back_populates="performed_by")

    def __repr__(self):
        return f"<Account {self.email} [{self.role}]>"


# ── Admin Model ────────────────────────────────────────────────────────────────
class Admin(Base):
    __tablename__ = "admin_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    department = Column(String(100), nullable=False, default="Administration")
    admin_level = Column(String(20), nullable=False, default="regular")

    can_import_data = Column(Boolean, default=True, nullable=False)
    can_export_data = Column(Boolean, default=True, nullable=False)
    can_manage_users = Column(Boolean, default=True, nullable=False)
    can_manage_system_config = Column(Boolean, default=True, nullable=False)
    can_view_audit_logs = Column(Boolean, default=True, nullable=False)

    user = relationship("Account", back_populates="admin_profile")


# ── Teacher Model ──────────────────────────────────────────────────────────────
class Teacher(Base):
    __tablename__ = "teacher_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    employee_id = Column(String(50), unique=True, nullable=True, index=True)
    specialization = Column(String(200), nullable=True)
    subjects = Column(Text, nullable=True)   # pipe-separated module codes e.g. "MATH01|PHY02"
    groups = Column(Text, nullable=True)     # pipe-separated group names e.g. "G1|G2|G3"

    can_mark_attendance = Column(Boolean, default=True, nullable=False)
    can_export_data = Column(Boolean, default=True, nullable=False)
    can_correct_attendance = Column(Boolean, default=True, nullable=False)
    correction_window_minutes = Column(Integer, default=15, nullable=False)

    user = relationship("Account", back_populates="teacher_profile")


# ── Student Model ──────────────────────────────────────────────────────────────
class StudentUser(Base):
    __tablename__ = "student_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    student_id = Column(String(50), unique=True, nullable=False, index=True)
    program = Column(String(100), nullable=False)
    level = Column(String(50), nullable=False)
    group = Column(String(50), nullable=True)

    can_submit_justifications = Column(Boolean, default=True, nullable=False)
    can_view_attendance = Column(Boolean, default=True, nullable=False)
    can_confirm_rattrapage = Column(Boolean, default=True, nullable=False)
    is_enrolled = Column(Boolean, default=True, nullable=False)

    user = relationship("Account", back_populates="student_profile")

    def __repr__(self):
        return f"<Student {self.student_id} ({self.program})>"


Student = StudentUser
StudentProfile = StudentUser


class UserRoleHelper:
    @staticmethod
    def is_admin(user: Account) -> bool:
        return str(getattr(user.role, "value", user.role)) == UserRole.ADMIN.value

    @staticmethod
    def is_teacher(user: Account) -> bool:
        return str(getattr(user.role, "value", user.role)) == UserRole.TEACHER.value

    @staticmethod
    def is_student(user: Account) -> bool:
        return str(getattr(user.role, "value", user.role)) == UserRole.STUDENT.value


# ── Password Reset Token Model ─────────────────────────────────────────────────
# Only relevant for credential-auth users who have a hashed_password.
# OAuth-only users (google_id set, no hashed_password) don't use this.
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("Account", back_populates="password_reset_tokens")

    def __repr__(self):
        return f"<PasswordResetToken user={self.user_id} used={self.is_used}>"


