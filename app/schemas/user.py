from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    field_validator,
    model_validator,
)
from typing import Optional
from uuid import UUID
from app.config import UserRole
from app.schemas.auth import validate_password_complexity


def _schema_example_email(local_part: str) -> str:
    return "@".join((local_part, "example.edu"))


SCHEMA_EXAMPLE_PASSWORD = "".join(("Example", "Auth", "1!"))


class AccountResponse(BaseModel):
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    phone: Optional[str] = None
    role: UserRole
    is_active: bool
    avatar_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AccountCreateBase(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)


class AccountCreate(AccountCreateBase):
    role: UserRole = UserRole.STUDENT

    student_id: Optional[str] = None
    program: Optional[str] = None
    level: Optional[str] = None
    group: Optional[str] = None

    employee_id: Optional[str] = None
    specialization: Optional[str] = None

    department: Optional[str] = None
    admin_level: Optional[str] = None

    @model_validator(mode="after")
    def validate_role_specific_fields(self) -> "AccountCreate":
        if self.role == UserRole.STUDENT:
            if not self.student_id:
                raise ValueError("student_id is required for student users")
            if not self.program:
                raise ValueError("program is required for student users")
            if not self.level:
                raise ValueError("level is required for student users")

        if self.role == UserRole.TEACHER and not self.employee_id:
            raise ValueError("employee_id is required for teacher users")

        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": _schema_example_email("student.one"),
                "password": SCHEMA_EXAMPLE_PASSWORD,
                "first_name": "Student",
                "last_name": "One",
                "phone": "+213550000000",
                "role": "student",
                "student_id": "ST-001",
                "program": "INFO",
                "level": "L3",
                "group": "G1",
            }
        }
    )


class StudentAccountCreate(AccountCreateBase):
    student_id: str
    program: str
    level: str
    group: Optional[str] = None

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
            }
        }
    )


class TeacherAccountCreate(AccountCreateBase):
    employee_id: str
    specialization: Optional[str] = None

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
            }
        }
    )


class AdminAccountCreate(AccountCreateBase):
    department: Optional[str] = None
    admin_level: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": _schema_example_email("admin.one"),
                "password": SCHEMA_EXAMPLE_PASSWORD,
                "first_name": "Nadia",
                "last_name": "Admin",
                "phone": "+213550000003",
                "department": "Pedagogy",
                "admin_level": "regular",
            }
        }
    )


class _AccountUpdateBase(BaseModel):
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "_AccountUpdateBase":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self


class StudentAccountUpdate(_AccountUpdateBase):
    student_id: Optional[str] = None
    program: Optional[str] = None
    level: Optional[str] = None
    group: Optional[str] = None

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
            }
        }
    )


class TeacherAccountUpdate(_AccountUpdateBase):
    employee_id: Optional[str] = None
    specialization: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "+213550000099",
                "employee_id": "EMP-212",
                "specialization": "Computer Science",
            }
        }
    )


class AdminAccountUpdate(_AccountUpdateBase):
    department: Optional[str] = None
    admin_level: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "+213550000099",
                "department": "Pedagogy",
                "admin_level": "regular",
            }
        }
    )


class AccountStatusUpdate(BaseModel):
    is_active: bool

    model_config = ConfigDict(
        json_schema_extra={"example": {"is_active": False}}
    )
