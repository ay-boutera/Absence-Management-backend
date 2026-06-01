"""
models/settings.py — Administration Settings (singleton table)
==============================================================
One row only, protected by a unique constraint on `singleton_guard`.
Stores the academic year configuration and absence rules.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, SmallInteger, String

from app.db import Base


class AdministrationSettings(Base):
    __tablename__ = "administration_settings"

    # Always id=1; autoincrement disabled to enforce singleton semantics.
    id = Column(Integer, primary_key=True, autoincrement=False)

    # Unique constraint ensures only one row can ever exist.
    singleton_guard = Column(Boolean, unique=True, nullable=False, default=True)

    # ── Academic year ──────────────────────────────────────────────────────────
    academic_year_label = Column(String(50), nullable=False, default="2025 – 2026")
    current_semester = Column(SmallInteger, nullable=False, default=1)

    # Semester 1 date range
    sem1_start_date = Column(Date, nullable=True)
    sem1_end_date = Column(Date, nullable=True)

    # Semester 2 date range
    sem2_start_date = Column(Date, nullable=True)
    sem2_end_date = Column(Date, nullable=True)

    # ── Absence rules (per-module thresholds) ─────────────────────────────────
    # warning_threshold: absences in a single module that trigger a warning
    warning_threshold = Column(SmallInteger, nullable=False, default=3)
    # max_absences: absences in a single module that trigger exclusion
    max_absences = Column(SmallInteger, nullable=False, default=5)
    # Days a student has to submit a justification after an absence
    justification_deadline_days = Column(SmallInteger, nullable=False, default=3)

    # ── Audit ──────────────────────────────────────────────────────────────────
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AdministrationSettings year={self.academic_year_label!r} "
            f"warning={self.warning_threshold} max={self.max_absences}>"
        )
