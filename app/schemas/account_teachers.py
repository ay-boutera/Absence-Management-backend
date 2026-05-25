from datetime import datetime
from typing import Literal, Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.config import UserRole
from app.schemas.auth import validate_password_complexity


SCHEMA_EXAMPLE_PASSWORD = "ExampleAuth1!"


def _schema_example_email(local_part: str) -> str:
    return f"{local_part}@esi-sba.dz"


class TeacherAccountCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    employee_id: str
    specialization: Optional[str] = None
    can_mark_attendance: bool = True
    can_export_data: bool = True
    can_correct_attendance: bool = True
    correction_window_minutes: int = 15

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": _schema_example_email("teacher.one"),
                "password": SCHEMA_EXAMPLE_PASSWORD,
                "first_name": "Teacher",
                "last_name": "One",
                "phone": "+213550000002",
                "employee_id": "EMP-101",
                "specialization": "Mathematics",
                "can_mark_attendance": True,
                "can_export_data": True,
                "can_correct_attendance": True,
                "correction_window_minutes": 15,
            }
        }
    )


class TeacherAccountUpdate(BaseModel):
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    employee_id: Optional[str] = None
    specialization: Optional[str] = None
    can_mark_attendance: Optional[bool] = None
    can_export_data: Optional[bool] = None
    can_correct_attendance: Optional[bool] = None
    correction_window_minutes: Optional[int] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TeacherAccountUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "+213550000099",
                "employee_id": "EMP-212",
                "specialization": "Computer Science",
                "correction_window_minutes": 20,
            }
        }
    )


class TeacherAccountResponse(BaseModel):
    id: UUID
    role: Literal[UserRole.TEACHER] = UserRole.TEACHER
    first_name: str
    last_name: str
    email: EmailStr
    phone: Optional[str] = None
    google_id: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_activity: Optional[datetime] = None
    employee_id: Optional[str] = None
    specialization: Optional[str] = None
    can_mark_attendance: bool
    can_export_data: bool
    can_correct_attendance: bool
    correction_window_minutes: int
    subjects: list[str] = []
    groups: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class AttendanceGroupStats(BaseModel):
    niveau: str
    subject: str
    group: str
    total_sessions: int = 0
    total_absences: int = 0
    attendance_rate: float


class SubjectGroupStats(BaseModel):
    subject_name: str
    niveau: str
    groups: List[str]


class TeacherProfileResponse(BaseModel):
    employee_id: str
    last_name: str
    first_name: str
    email: EmailStr
    specialization: Optional[str] = None
    role: str = "TEACHER"
    avatar_url: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool

    total_subjects: int
    total_groups: int
    total_sessions: int
    total_absences: int
    overall_attendance_rate: float

    attendance_by_group: List[AttendanceGroupStats]
    subjects: List[SubjectGroupStats]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "employee_id": "ENS002",
                "last_name": "NOUR ELFOUAD",
                "first_name": "TRARI",
                "email": "nf.trari@esi-sba.dz",
                "specialization": "math",
                "role": "TEACHER",
                "avatar_url": None,
                "phone": None,
                "is_active": True,
                "total_subjects": 1,
                "total_groups": 3,
                "total_sessions": 21,
                "total_absences": 3,
                "overall_attendance_rate": 85.7,
                "attendance_by_group": [
                    {
                        "niveau": "1CS",
                        "subject": "Archi",
                        "group": "G6",
                        "total_sessions": 7,
                        "total_absences": 1,
                        "attendance_rate": 85.7,
                    },
                    {
                        "niveau": "1CS",
                        "subject": "Archi",
                        "group": "G2",
                        "total_sessions": 7,
                        "total_absences": 1,
                        "attendance_rate": 85.7,
                    },
                    {
                        "niveau": "1CS",
                        "subject": "Archi",
                        "group": "G3",
                        "total_sessions": 7,
                        "total_absences": 1,
                        "attendance_rate": 85.7,
                    },
                ],
                "subjects": [
                    {
                        "subject_name": "Archi",
                        "niveau": "1CS",
                        "groups": ["G2", "G3", "G6"],
                    }
                ],
            }
        }
    )
