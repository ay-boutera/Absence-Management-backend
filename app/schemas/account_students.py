from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.config import UserRole
from app.schemas.auth import validate_password_complexity


SCHEMA_EXAMPLE_PASSWORD = "ExampleAuth1!"


def _schema_example_email(local_part: str) -> str:
    return f"{local_part}@esi-sba.dz"


class StudentAccountCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    student_id: str
    program: str
    level: str
    group: Optional[str] = None
    can_submit_justifications: bool = True
    can_view_attendance: bool = True
    can_confirm_rattrapage: bool = True
    is_enrolled: bool = True

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": _schema_example_email("student.one"),
                "password": SCHEMA_EXAMPLE_PASSWORD,
                "first_name": "Student",
                "last_name": "One",
                "phone": "+213550000001",
                "student_id": "ST-101",
                "program": "INFO",
                "level": "L3",
                "group": "G1",
                "can_submit_justifications": True,
                "can_view_attendance": True,
                "can_confirm_rattrapage": True,
                "is_enrolled": True,
            }
        }
    )


class StudentAccountUpdate(BaseModel):
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    student_id: Optional[str] = None
    program: Optional[str] = None
    level: Optional[str] = None
    group: Optional[str] = None
    can_submit_justifications: Optional[bool] = None
    can_view_attendance: Optional[bool] = None
    can_confirm_rattrapage: Optional[bool] = None
    is_enrolled: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "StudentAccountUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "+213550000099",
                "student_id": "ST-201",
                "program": "INFO",
                "level": "L4",
                "group": "G2",
                "is_enrolled": True,
            }
        }
    )


class StudentAccountResponse(BaseModel):
    id: UUID
    role: Literal[UserRole.STUDENT] = UserRole.STUDENT
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
    student_id: str
    program: str
    level: str
    group: Optional[str] = None
    can_submit_justifications: bool
    can_view_attendance: bool
    can_confirm_rattrapage: bool
    is_enrolled: bool

    model_config = ConfigDict(from_attributes=True)
