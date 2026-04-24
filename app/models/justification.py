import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config.enums import JustificationStatusEnum


class Justification(Base):
    """
    Student-submitted justification document for an absence.

    One justification per absence (unique on absence_id).
    Status lifecycle: EN_ATTENTE → JUSTIFIEE | REJETEE.
    File is stored permanently on disk for traceability (US-33).
    """

    __tablename__ = "justifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    absence_id = Column(
        UUID(as_uuid=True),
        ForeignKey("absences.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    student_matricule = Column(
        String(50),
        ForeignKey("students.matricule", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── File info ─────────────────────────────────────────────────────────────
    file_path = Column(String(500), nullable=False)   # path on disk
    file_name = Column(String(255), nullable=False)   # original filename
    file_type = Column(String(10), nullable=False)    # pdf / jpg / png
    file_size = Column(Integer, nullable=False)       # bytes

    # ── Status ────────────────────────────────────────────────────────────────
    status = Column(
        SQLAlchemyEnum(
            JustificationStatusEnum,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=JustificationStatusEnum.EN_ATTENTE,
    )
    admin_comment = Column(Text, nullable=True)

    # ── Deadline (72 h after session end, stored at submission time) ──────────
    deadline = Column(DateTime(timezone=True), nullable=False)

    # ── Timestamps ────────────────────────────────────────────────────────────
    submitted_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("admins.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    absence = relationship("Absence", back_populates="justification")
    student = relationship(
        "AcademicStudent",
        primaryjoin="Justification.student_matricule == AcademicStudent.matricule",
        foreign_keys=[student_matricule],
    )
    reviewer = relationship("Admin", foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return (
            f"<Justification absence={self.absence_id} "
            f"status={self.status} student={self.student_matricule}>"
        )
