import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class SessionType(str, Enum):
    COURS = "cours"
    TD = "TD"
    TP = "TP"
    EXAMEN = "examen"


class ImportType(str, Enum):
    STUDENTS = "students"
    PLANNING = "planning"
    TEACHERS = "teachers"


class ImportExportAction(str, Enum):
    IMPORT = "import"
    EXPORT = "export"


class ImportExportFileType(str, Enum):
    CSV = "csv"
    PDF = "pdf"
    EXCEL = "excel"


class ImportExportDataType(str, Enum):
    STUDENTS = "students"
    ATTENDANCE = "attendance"
    SCHEDULE = "schedule"
    JUSTIFICATIONS = "justifications"


class AcademicYear(str, Enum):
    CP_1 = "1CP"
    CP_2 = "2CP"
    CS_1 = "1CS"
    CS_2 = "2CS"
    CS_3 = "3CS"


class SectionEnum(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"
    J = "J"
    K = "K"
    L = "L"
    M = "M"
    N = "N"
    O = "O"
    P = "P"
    Q = "Q"
    R = "R"
    S = "S"
    T = "T"
    U = "U"
    V = "V"
    W = "W"
    X = "X"
    Y = "Y"
    Z = "Z"


class SpecialityEnum(str, Enum):
    ISI = "ISI"
    SIW = "SIW"
    IASD = "IASD"
    CYS = "CyS"


class Student(Base):
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


class Module(Base):
    __tablename__ = "modules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    nom = Column(String(255), nullable=False)


class Salle(Base):
    __tablename__ = "salles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)


# ── New PlanningSession: weekly recurring timetable ────────────────────────────
class PlanningSession(Base):
    """
    Represents a recurring weekly session in the timetable.

    Upsert key (UniqueConstraint uq_planning_session):
        year, section, speciality, semester, day, time_start, type, subject, group, teacher_id
    """

    __tablename__ = "planning_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Academic context
    year = Column(SQLAlchemyEnum(AcademicYear, values_callable=lambda obj: [e.value for e in obj]), nullable=False)        # 1CP,2CP,1CS,2CS,3CS
    section = Column(SQLAlchemyEnum(SectionEnum, values_callable=lambda obj: [e.value for e in obj]), nullable=True)       # A-Z
    speciality = Column(SQLAlchemyEnum(SpecialityEnum, values_callable=lambda obj: [e.value for e in obj]), nullable=True) # ISI,SIW,IASD,CyS
    semester = Column(String(2), nullable=False)                       # S1 | S2

    # Schedule
    day = Column(String(20), nullable=False)           # Dimanche…Jeudi
    time_start = Column(Time, nullable=False)          # HH:MM
    time_end = Column(Time, nullable=False)            # HH:MM

    # Session content
    type = Column(String(20), nullable=False)          # Cours,TD,TP,TD/TP,Cours/TP,…
    subject = Column(String(255), nullable=False)      # module name (free text)
    room = Column(String(100), nullable=True)          # SALLE 01, Amphi. D, etc.
    group = Column(String(20), nullable=True)          # Gx

    # Teacher (nullable — e.g. TBD sessions)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    teacher = relationship("Teacher", foreign_keys=[teacher_id])

    __table_args__ = (
        UniqueConstraint(
            "year", "section", "speciality", "semester", "day",
            "time_start", "type", "subject", "group", "teacher_id",
            name="uq_planning_session",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PlanningSession {self.year}/{self.section} {self.day} "
            f"{self.time_start}–{self.time_end} {self.subject}>"
        )


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

    student = relationship("Student", back_populates="absences")
    session = relationship("PlanningSession")


class ImportHistory(Base):
    __tablename__ = "import_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename = Column(String(255), nullable=False)
    import_type = Column(SQLAlchemyEnum(ImportType), nullable=False)
    total_rows = Column(Integer, nullable=False)
    success_count = Column(Integer, nullable=False)
    error_count = Column(Integer, nullable=False)


class ImportExportLog(Base):
    __tablename__ = "import_export_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    performed_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = Column(SQLAlchemyEnum(ImportExportAction), nullable=False)
    file_type = Column(SQLAlchemyEnum(ImportExportFileType), nullable=False)
    file_name = Column(String(255), nullable=False)
    data_type = Column(SQLAlchemyEnum(ImportExportDataType), nullable=False)
    row_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    error_details = Column(JSON, default=dict, nullable=False)
    file_path = Column(String(500), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    performed_by = relationship("Account", back_populates="import_export_logs")
