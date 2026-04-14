"""
models/admin.py — Standalone Admin Model
=========================================
Admin has its own table with all auth fields embedded directly.
No dependency on a shared Account/users table.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.config import UserRole
from app.db import Base


class Admin(Base):
    __tablename__ = "admins"

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

    # ── Admin-specific fields ─────────────────────────────────────────────────
    department = Column(String(100), nullable=False, default="Administration")
    admin_level = Column(String(20), nullable=False, default="regular")

    # ── Admin permissions ─────────────────────────────────────────────────────
    can_import_data = Column(Boolean, default=True, nullable=False)
    can_export_data = Column(Boolean, default=True, nullable=False)
    can_manage_users = Column(Boolean, default=True, nullable=False)
    can_manage_system_config = Column(Boolean, default=True, nullable=False)
    can_view_audit_logs = Column(Boolean, default=True, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    password_reset_tokens = relationship(
        "AdminPasswordResetToken", back_populates="admin", cascade="all, delete-orphan"
    )
    def __repr__(self):
        return f"<Admin {self.email} [{self.admin_level}]>"

    @property
    def role(self) -> UserRole:
        return UserRole.ADMIN


class AdminPasswordResetToken(Base):
    __tablename__ = "admin_password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(
        UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    admin = relationship("Admin", back_populates="password_reset_tokens")

    def __repr__(self):
        return f"<AdminPasswordResetToken admin={self.admin_id} used={self.is_used}>"
