import uuid

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class Absence(Base):
    __tablename__ = "absences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_matricule = Column(
        String(50),
        ForeignKey("students.matricule", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    planning_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("planning_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    statut_justificatif = Column(String(50), nullable=True)

    student = relationship("app.models.student.AcademicStudent", back_populates="absences")
    session = relationship("PlanningSession")
