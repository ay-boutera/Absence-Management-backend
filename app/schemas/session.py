from __future__ import annotations

from datetime import date, time
from typing import Literal, Optional
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


# ── Feature 1.1 — GET /sessions/{id}/attendance ───────────────────────────────

class StudentAttendanceRecord(BaseModel):
    """Full student info with attendance state for the attendance view."""
    student_id: UUID
    matricule: str
    nom: str
    prenom: str
    email: str
    avatar_url: Optional[str] = None
    is_present: bool
    participation: Optional[str] = None
    total_absences: int

    model_config = {"from_attributes": True}


class AttendanceListResponse(BaseModel):
    session_id: UUID
    total: int
    records: list[StudentAttendanceRecord] = Field(default_factory=list)


# ── Feature 1.2 — PUT /sessions/{id}/attendance ───────────────────────────────

class AttendanceRecordIn(BaseModel):
    student_matricule: str
    is_present: bool
    participation: Optional[str] = None


class AttendanceSubmit(BaseModel):
    records: list[AttendanceRecordIn]


class AttendanceSubmitResult(BaseModel):
    updated: int
    created: int


# ── Feature 1.3 — GET /sessions/my-sessions ───────────────────────────────────

class MySessionOut(BaseModel):
    id: UUID
    date: date
    start_time: time
    end_time: time
    type: str
    status: str
    lesson_name: str
    room: Optional[str] = None
    groups: list[str] = Field(default_factory=list)
    has_attendance: bool

    model_config = {"from_attributes": True}


class MySessionListResponse(BaseModel):
    total: int
    sessions: list[MySessionOut] = Field(default_factory=list)


# ── Feature 2.1 — POST /sessions/{id}/groups ─────────────────────────────────

class AddGroupToSessionRequest(BaseModel):
    group_name: str = Field(..., description="Group name to add (e.g. 'G1', 'G2')")


class AddGroupToSessionResponse(BaseModel):
    session_id: UUID
    group_name: str
    added: Literal[True] = True


# ── Feature 2.2 — POST /sessions/{id}/students ───────────────────────────────

class AddStudentToSessionRequest(BaseModel):
    student_matricule: str = Field(..., description="Student matricule to link directly to the session")


class AddStudentToSessionResponse(BaseModel):
    session_id: UUID
    student_matricule: str
    added: Literal[True] = True
