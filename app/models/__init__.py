from app.config import UserRole
from app.models.settings import AdministrationSettings
from app.models.notification import Notification
from app.config.enums import (
    AbsenceSourceEnum,
    AcademicYear,
    CompensationRequestStatus,
    CorrectionStatusEnum,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportType,
    SectionEnum,
    SessionStatusEnum,
    SessionType,
    SpecialityEnum,
)
from app.models.admin import Admin
from app.models.teacher import Teacher
from app.models.student import AcademicStudent, Student, StudentProfile, StudentUser
from app.models.password_reset import PasswordResetToken
from .absence import Absence, AbsenceCorrection
from .audit_log import AuditLog, ActionType
from .import_export_log import ImportExportLog
from .import_history import ImportHistory
from .module import Module
from .justification import Justification
from .planning_session import PlanningSession
from .salle import Salle
from .session import Session, SessionAttendanceSummary, session_groups, session_students
from .compensation_request import CompensationRequest
from .session_nonce import SessionNonce

__all__ = [
    # Configuration
    "AdministrationSettings",
    "Notification",

    # Shared role models
    "PasswordResetToken",
    "UserRole",

    # Specialized profiles
    "Admin",
    "Teacher",
    "Student",
    "StudentUser",
    "StudentProfile",

    # Audit / academic
    "AuditLog",
    "ActionType",
    "AcademicStudent",
    "Module",
    "Justification",
    "Salle",
    "PlanningSession",
    "Session",
    "SessionAttendanceSummary",
    "session_groups",
    "session_students",
    "Absence",
    "AbsenceCorrection",
    "CompensationRequest",
    "SessionNonce",
    "ImportHistory",
    "ImportExportLog",

    # Enums
    "AbsenceSourceEnum",
    "AcademicYear",
    "CompensationRequestStatus",
    "CorrectionStatusEnum",
    "ImportExportAction",
    "ImportExportDataType",
    "ImportExportFileType",
    "ImportType",
    "SectionEnum",
    "SessionStatusEnum",
    "SessionType",
    "SpecialityEnum",
]
