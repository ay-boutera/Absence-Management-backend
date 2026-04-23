from __future__ import annotations

from datetime import date, time
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.config.enums import SessionStatusEnum, SessionType


class ModuleInfo(BaseModel):
    id: UUID
    code: str
    nom: str

    model_config = {"from_attributes": True}


class SalleInfo(BaseModel):
    id: UUID
    code: str
    nom: Optional[str] = None

    model_config = {"from_attributes": True}


class TeacherInfo(BaseModel):
    id: UUID
    employee_id: Optional[str] = None
    first_name: str
    last_name: str

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: UUID
    date: date
    start_time: time
    end_time: time
    type: str
    status: str
    is_makeup: bool

    group: Optional[str] = None
    year: Optional[str] = None
    section: Optional[str] = None
    speciality: Optional[str] = None
    semester: Optional[str] = None

    module: ModuleInfo
    teacher: TeacherInfo
    room: Optional[SalleInfo] = None

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    total: int
    sessions: list[SessionOut] = Field(default_factory=list)


class AttendanceSummaryOut(BaseModel):
    session_id: UUID
    total_students: int
    present_count: int
    absent_count: int
    pending_count: int

    model_config = {"from_attributes": True}


class StudentAttendanceOut(BaseModel):
    """Student info with their current attendance status for a session."""
    matricule: str
    nom: str
    prenom: str
    groupe: str
    is_absent: Optional[bool] = None
    absence_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


class StudentListResponse(BaseModel):
    total: int
    students: list[StudentAttendanceOut] = Field(default_factory=list)
