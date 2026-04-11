import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Time,
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


class ImportExportAction(str, Enum):
    IMPORT = "import"
    EXPORT = "export"


class ImportExportFileType(str, Enum):
    CSV = "csv"
    PDF = "pdf"
    EXCEL = "excel"


class ImportExportDataType(str, Enum):
    STUDENTS = "students"
    TEACHERS = "teachers"
    ATTENDANCE = "attendance"
    SCHEDULE = "schedule"
    JUSTIFICATIONS = "justifications"


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
    annee = Column(String(10), nullable=True)
    has_td = Column(Boolean, nullable=False, default=False)
    has_tp = Column(Boolean, nullable=False, default=False)

    planning_sessions = relationship("PlanningSession", back_populates="module")


class Salle(Base):
    __tablename__ = "salles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)

    planning_sessions = relationship("PlanningSession", back_populates="salle_ref")


class PlanningSession(Base):
    __tablename__ = "planning_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_seance = Column(String(100), unique=True, nullable=False, index=True)
    code_module = Column(
        String(50),
        ForeignKey("modules.code", ondelete="RESTRICT"),
        nullable=False,
    )
    type_seance = Column(SQLAlchemyEnum(SessionType), nullable=False)
    date = Column(Date, nullable=False)
    heure_debut = Column(Time, nullable=False)
    heure_fin = Column(Time, nullable=False)
    salle = Column(
        String(50),
        ForeignKey("salles.code", ondelete="RESTRICT"),
        nullable=False,
    )
    id_enseignant = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    module = relationship("Module", back_populates="planning_sessions")
    salle_ref = relationship("Salle", back_populates="planning_sessions")
    absences = relationship("Absence", back_populates="session")


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
    session = relationship("PlanningSession", back_populates="absences")


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
