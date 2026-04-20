"""
models/teacher.py — Standalone Teacher Model
=============================================
Teacher has its own table with all auth fields embedded directly.
No dependency on a shared Account/users table.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.config import UserRole
from app.db import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # ── Core identity ─────────────────────────────────────────────────────────
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=True)

    # ── Credential login ──────────────────────────────────────────────────────
    hashed_password = Column(String(255), nullable=True)

    # ── Google OAuth ──────────────────────────────────────────────────────────
    google_id = Column(String(255), unique=True, nullable=True, index=True)
    avatar_url = Column(String(500), nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
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

    # ── Teacher-specific fields ───────────────────────────────────────────────
    employee_id = Column(String(50), unique=True, nullable=True, index=True)
    specialization = Column(String(200), nullable=True)

    # ── Teacher permissions ───────────────────────────────────────────────────
    can_mark_attendance = Column(Boolean, default=True, nullable=False)
    can_export_data = Column(Boolean, default=True, nullable=False)
    can_correct_attendance = Column(Boolean, default=True, nullable=False)
    correction_window_minutes = Column(Integer, default=15, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    password_reset_tokens = relationship(
        "TeacherPasswordResetToken",
        back_populates="teacher",
        cascade="all, delete-orphan",
    )
    def __repr__(self):
        return f"<Teacher {self.email} [{self.employee_id}]>"

    @property
    def role(self) -> UserRole:
        return UserRole.TEACHER


class TeacherPasswordResetToken(Base):
    __tablename__ = "teacher_password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teachers.id", ondelete="CASCADE"),
        nullable=False,
    )
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    teacher = relationship("Teacher", back_populates="password_reset_tokens")

    def __repr__(self):
        return (
            f"<TeacherPasswordResetToken teacher={self.teacher_id} used={self.is_used}>"
        )
