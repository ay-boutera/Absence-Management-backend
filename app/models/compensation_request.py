import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SQLAlchemyEnum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.config.enums import CompensationRequestStatus
from app.db import Base


class CompensationRequest(Base):
    """
    A student's request to attend a session belonging to another group
    in order to compensate for a missed session.
    """

    __tablename__ = "compensation_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_matricule = Column(
        String(50),
        ForeignKey("students.matricule", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reason = Column(Text, nullable=False)

    status = Column(
        SQLAlchemyEnum(
            CompensationRequestStatus,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=CompensationRequestStatus.PENDING,
    )
    rejection_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    session = relationship("Session", back_populates="compensation_requests")
    student = relationship("AcademicStudent", back_populates="compensation_requests")

    def __repr__(self) -> str:
        return (
            f"<CompensationRequest student={self.student_matricule} "
            f"session={self.session_id} status={self.status}>"
        )
