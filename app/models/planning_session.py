import uuid

from sqlalchemy import Column, Enum as SQLAlchemyEnum, ForeignKey, String, Time, UniqueConstraint, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config.enums import AcademicYear, SectionEnum, SpecialityEnum

planning_session_teachers = Table(
    "planning_session_teachers",
    Base.metadata,
    Column("planning_session_id", UUID(as_uuid=True), ForeignKey("planning_sessions.id", ondelete="CASCADE"), primary_key=True),
    Column("teacher_id", UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True),
)


class PlanningSession(Base):
    __tablename__ = "planning_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    year = Column(
        SQLAlchemyEnum(AcademicYear, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    section = Column(
        SQLAlchemyEnum(SectionEnum, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    speciality = Column(
        SQLAlchemyEnum(SpecialityEnum, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    semester = Column(String(2), nullable=False)

    day = Column(String(20), nullable=False)
    time_start = Column(Time, nullable=False)
    time_end = Column(Time, nullable=False)

    type = Column(String(20), nullable=False)
    subject = Column(String(255), nullable=False)
    room = Column(String(100), nullable=True)
    group = Column(String(20), nullable=True)

    teachers = relationship("Teacher", secondary=planning_session_teachers)

    __table_args__ = (
        UniqueConstraint(
            "year",
            "section",
            "speciality",
            "semester",
            "day",
            "time_start",
            "type",
            "subject",
            "group",
            name="uq_planning_session",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PlanningSession {self.year}/{self.section} {self.day} "
            f"{self.time_start}–{self.time_end} {self.subject}>"
        )
