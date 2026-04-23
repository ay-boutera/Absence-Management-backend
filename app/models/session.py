import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    SmallInteger,
    String,
    Table,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base
from app.config.enums import SessionStatusEnum, SessionType


# ── Association tables ─────────────────────────────────────────────────────────

session_groups = Table(
    "session_groups",
    Base.metadata,
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("group_name", String(50), primary_key=True),
)

session_students = Table(
    "session_students",
    Base.metadata,
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "student_matricule",
        String(50),
        ForeignKey("students.matricule", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Session(Base):
    """
    A concrete class session on a specific date, derived from a PlanningSession template.

    Generated when the admin imports the planning (given a semester start date)
    or on-demand when a teacher queries today's sessions. One Session per
    teacher per planning occurrence — teachers see their own session list;
    absences are recorded against these rows.
    """

    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Origin template ──────────────────────────────────────────────────────
    planning_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("planning_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── FKs to reference tables ───────────────────────────────────────────────
    module_id = Column(
        UUID(as_uuid=True),
        ForeignKey("modules.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    room_id = Column(
        UUID(as_uuid=True),
        ForeignKey("salles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Academic context (denormalized from planning for fast filtering) ──────
    group = Column(String(50), nullable=True)
    year = Column(String(10), nullable=True)
    section = Column(String(5), nullable=True)
    speciality = Column(String(20), nullable=True)
    semester = Column(String(2), nullable=True)

    # ── Schedule ──────────────────────────────────────────────────────────────
    date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # ── Metadata ──────────────────────────────────────────────────────────────
    type = Column(
        SQLAlchemyEnum(SessionType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    status = Column(
        SQLAlchemyEnum(SessionStatusEnum, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=SessionStatusEnum.SCHEDULED,
    )
    is_makeup = Column(Boolean, default=False, nullable=False)

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

    # ── Relationships ─────────────────────────────────────────────────────────
    planning_session = relationship("PlanningSession")
    module = relationship("Module", back_populates="sessions")
    teacher = relationship("Teacher", back_populates="sessions")
    room = relationship("Salle", back_populates="sessions")
    absences = relationship("Absence", back_populates="session", cascade="all, delete-orphan")
    attendance_summary = relationship(
        "SessionAttendanceSummary",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "planning_session_id",
            "teacher_id",
            "date",
            name="uq_session_planning_teacher_date",
        ),
    )

    def __repr__(self) -> str:
        return f"<Session {self.date} {self.start_time}–{self.end_time} teacher={self.teacher_id}>"


class SessionAttendanceSummary(Base):
    """
    Denormalized attendance counts for a session, refreshed by a DB trigger
    on every INSERT/UPDATE in the absences table (US-24).
    """

    __tablename__ = "session_attendance_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    total_students = Column(SmallInteger, default=0, nullable=False)
    present_count = Column(SmallInteger, default=0, nullable=False)
    absent_count = Column(SmallInteger, default=0, nullable=False)
    pending_count = Column(SmallInteger, default=0, nullable=False)

    last_updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("Session", back_populates="attendance_summary")

    def __repr__(self) -> str:
        return (
            f"<SessionAttendanceSummary session={self.session_id} "
            f"absent={self.absent_count}/{self.total_students}>"
        )
