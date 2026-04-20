from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.config import UserRole
from app.schemas.auth import validate_password_complexity


SCHEMA_EXAMPLE_PASSWORD = "ExampleAuth1!"


def _schema_example_email(local_part: str) -> str:
    return f"{local_part}@esi-sba.dz"


class AdminAccountCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    department: Optional[str] = None
    can_import_data: bool = True
    can_export_data: bool = True
    can_manage_users: bool = True
    can_manage_system_config: bool = True
    can_view_audit_logs: bool = True

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": _schema_example_email("admin.one"),
                "password": SCHEMA_EXAMPLE_PASSWORD,
                "first_name": "Nadia",
                "last_name": "Admin",
                "phone": "+213550000003",
                "department": "Pedagogy",
                "can_import_data": True,
                "can_export_data": True,
                "can_manage_users": True,
                "can_manage_system_config": True,
                "can_view_audit_logs": True,
            }
        }
    )


class AdminAccountUpdate(BaseModel):
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    admin_level: Optional[str] = None
    can_import_data: Optional[bool] = None
    can_export_data: Optional[bool] = None
    can_manage_users: Optional[bool] = None
    can_manage_system_config: Optional[bool] = None
    can_view_audit_logs: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "AdminAccountUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Updated",
                "last_name": "Name",
                "phone": "+213550000099",
                "department": "Pedagogy",
                "admin_level": "regular",
                "can_import_data": True,
            }
        }
    )


class AdminAccountResponse(BaseModel):
    id: UUID
    role: Literal[UserRole.ADMIN] = UserRole.ADMIN
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
    department: str
    admin_level: str
    can_import_data: bool
    can_export_data: bool
    can_manage_users: bool
    can_manage_system_config: bool
    can_view_audit_logs: bool

    model_config = ConfigDict(from_attributes=True)


class UserStatusUpdate(BaseModel):
    is_active: bool

    model_config = ConfigDict(json_schema_extra={"example": {"is_active": False}})
