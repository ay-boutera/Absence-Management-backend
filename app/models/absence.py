import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config.enums import AbsenceSourceEnum, CorrectionStatusEnum, JustificationStatusEnum


class Absence(Base):
    """
    Records whether a student was absent (or present) in a given Session.

    UNIQUE on (session_id, student_matricule) — one record per student per session.
    On re-tap the row is UPSERTED (is_absent toggled). source=PWA for web-app
    recordings; MANUAL for admin edits; IMPORT for bulk uploads.
    """

    __tablename__ = "absences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_matricule = Column(
        String(50),
        ForeignKey("students.matricule", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The teacher (or admin) who recorded this entry
    recorded_by = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    is_absent = Column(Boolean, default=True, nullable=False)
    participation = Column(String(10), nullable=True)
    source = Column(
        SQLAlchemyEnum(AbsenceSourceEnum, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=AbsenceSourceEnum.PWA,
    )
    synced_at = Column(DateTime(timezone=True), nullable=True)

    statut_justificatif = Column(String(50), nullable=True)

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

    session = relationship("Session", back_populates="absences")
    student = relationship("app.models.student.AcademicStudent", back_populates="absences")
    corrections = relationship("AbsenceCorrection", back_populates="absence", cascade="all, delete-orphan")
    justification = relationship("Justification", back_populates="absence", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("session_id", "student_matricule", name="uq_absence_session_student"),
    )

    def __repr__(self) -> str:
        return f"<Absence session={self.session_id} student={self.student_matricule} absent={self.is_absent}>"


class AbsenceCorrection(Base):
    """
    Correction request for an existing Absence record.

    Within the free correction window (≤15 min after session end): status is
    set to APPROVED immediately.  Beyond that window: stays PENDING until an
    Admin approves or rejects it (US-22, US-23).
    """

    __tablename__ = "absence_corrections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    absence_id = Column(
        UUID(as_uuid=True),
        ForeignKey("absences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The teacher who requested the correction
    requested_by = Column(
        UUID(as_uuid=True),
        ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The admin who reviewed (null until reviewed)
    reviewed_by = Column(
        UUID(as_uuid=True),
        nullable=True,
    )

    original_value = Column(Boolean, nullable=False)
    new_value = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False)

    status = Column(
        SQLAlchemyEnum(CorrectionStatusEnum, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CorrectionStatusEnum.PENDING,
    )

    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    requested_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    absence = relationship("Absence", back_populates="corrections")
    requester = relationship("Teacher", foreign_keys=[requested_by])

    def __repr__(self) -> str:
        return (
            f"<AbsenceCorrection absence={self.absence_id} "
            f"status={self.status} {self.original_value}→{self.new_value}>"
        )
