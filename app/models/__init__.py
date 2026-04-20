from app.config import UserRole
from app.config.enums import (
    AcademicYear,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportType,
    SectionEnum,
    SessionType,
    SpecialityEnum,
)
from app.models.admin import Admin
from app.models.teacher import Teacher
from app.models.student import AcademicStudent, Student, StudentProfile, StudentUser
from app.models.password_reset import PasswordResetToken
from .absence import Absence
from .audit_log import AuditLog, ActionType
from .import_export_log import ImportExportLog
from .import_history import ImportHistory
from .module import Module
from .planning_session import PlanningSession
from .salle import Salle

__all__ = [
    # Shared role models
    "PasswordResetToken",
    "UserRole",

    # Specialized profiles
    "Admin",
    "Teacher",
    "Student",
    "StudentUser",  # backward-compat alias
    "StudentProfile",

    # Audit / academic
    "AuditLog",
    "ActionType",
    "AcademicStudent",
    "Module",
    "Salle",
    "PlanningSession",
    "Absence",
    "ImportHistory",
    "ImportExportLog",
    "ImportExportAction",
    "ImportExportDataType",
    "ImportExportFileType",
    "ImportType",
    "AcademicYear",
    "SectionEnum",
    "SpecialityEnum",
    "SessionType",
]
