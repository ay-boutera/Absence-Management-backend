import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum as SQLAlchemyEnum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.config.enums import JustificationScopeType, JustificationStatus
from app.db import Base


class Justification(Base):
    __tablename__ = "justifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    scope_type = Column(
        SQLAlchemyEnum(JustificationScopeType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )

    absence_id = Column(
        UUID(as_uuid=True),
        ForeignKey("absences.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    reason = Column(Text, nullable=False)

    document_url = Column(Text, nullable=False)
    document_name = Column(Text, nullable=False)
    cloudinary_public_id = Column(Text, nullable=False)

    status = Column(
        SQLAlchemyEnum(JustificationStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=JustificationStatus.PENDING,
    )

    rejection_reason = Column(Text, nullable=True)
    reviewed_by = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

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

    student = relationship("AcademicStudent")
    absence = relationship("Absence")
    session = relationship("Session")

    __table_args__ = (
        UniqueConstraint("student_id", "absence_id", name="uq_justification_student_absence"),
        UniqueConstraint("student_id", "session_id", name="uq_justification_student_session"),
    )
