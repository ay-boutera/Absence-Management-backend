"""
models/user.py — Database Models for Users
============================================
Supports TWO login methods simultaneously:
    1. Credential login  — email + password  (hashed_password column)
    2. Google OAuth      — Google account     (google_id column)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config import UserRole


# ── User Model ─────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # ── Core identity ─────────────────────────────────────────────────────────
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)

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
    student_profile = relationship(
        "StudentProfile", back_populates="user", uselist=False
    )
    password_reset_tokens = relationship("PasswordResetToken", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")

    def __repr__(self):
        return f"<User {self.email} [{self.role}]>"


# ── Student Profile Model ──────────────────────────────────────────────────────
class StudentProfile(Base):
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

    user = relationship("User", back_populates="student_profile")

    def __repr__(self):
        return f"<StudentProfile {self.student_id} ({self.program})>"


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

    user = relationship("User", back_populates="password_reset_tokens")

    def __repr__(self):
        return f"<PasswordResetToken user={self.user_id} used={self.is_used}>"
