"""
models/student.py — Standalone Student Model
=============================================
Student has its own table with all auth fields embedded directly.
No dependency on a shared Account/users table.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.config import UserRole
from app.db import Base


class Student(Base):
    __tablename__ = "student_users"

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

    # ── Student-specific fields ───────────────────────────────────────────────
    student_id = Column(String(50), unique=True, nullable=False, index=True)
    program = Column(String(100), nullable=False)
    level = Column(String(50), nullable=False)
    group = Column(String(50), nullable=True)

    # ── Student permissions ───────────────────────────────────────────────────
    can_submit_justifications = Column(Boolean, default=True, nullable=False)
    can_view_attendance = Column(Boolean, default=True, nullable=False)
    can_confirm_rattrapage = Column(Boolean, default=True, nullable=False)
    is_enrolled = Column(Boolean, default=True, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    password_reset_tokens = relationship(
        "StudentPasswordResetToken",
        back_populates="student",
        cascade="all, delete-orphan",
    )
    def __repr__(self):
        return f"<Student {self.student_id} ({self.program})>"

    @property
    def role(self) -> UserRole:
        return UserRole.STUDENT


class AcademicStudent(Base):
    __tablename__ = "students"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matricule = Column(String(50), unique=True, nullable=False, index=True)
    nom = Column(String(120), nullable=False)
    prenom = Column(String(120), nullable=False)
    filiere = Column(String(120), nullable=False)
    niveau = Column(String(50), nullable=False)
    groupe = Column(String(50), nullable=False)
    email = Column(String(255), nullable=False)
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

    absences = relationship("Absence", back_populates="student")


# Aliases kept for backward compatibility if referenced elsewhere
StudentUser = Student
StudentProfile = Student


class StudentPasswordResetToken(Base):
    __tablename__ = "student_password_reset_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    student = relationship("app.models.student.Student", back_populates="password_reset_tokens")

    def __repr__(self):
        return (
            f"<StudentPasswordResetToken student={self.student_id} used={self.is_used}>"
        )
