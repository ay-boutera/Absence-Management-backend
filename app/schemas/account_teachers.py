from datetime import datetime
from typing import Literal, Optional
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

    model_config = ConfigDict(from_attributes=True)
