from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Feature 3 — PATCH /students/{id}/status ──────────────────────────────────

class StudentStatusUpdate(BaseModel):
    status: Literal["normal", "exclu", "bloque", "abandonné"]


class AcademicStudentStatusOut(BaseModel):
    id: UUID
    matricule: str
    nom: str
    prenom: str
    filiere: str
    niveau: str
    groupe: str
    email: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Admin Attendance List — GET /students ─────────────────────────────────────

class StudentAttendanceListOut(BaseModel):
    """One row in the admin attendance list."""
    id: UUID
    matricule: str
    nom: str
    prenom: str
    email: str
    filiere: str
    niveau: str
    groupe: str
    status: str
    absences_count: int

    model_config = ConfigDict(from_attributes=True)


class PaginatedStudentAttendanceList(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[StudentAttendanceListOut]


# ── Student Profile — GET /students/{matricule} ─────────────────────────────

class AbsenceHistoryItem(BaseModel):
    """One absence record in the student's history."""
    absence_id: UUID
    session_id: UUID
    date: datetime
    start_time: str
    end_time: str
    module_name: Optional[str] = None
    teacher_name: Optional[str] = None
    is_absent: bool
    justification_status: Optional[str] = None
    # ── Group context ──
    session_group: Optional[str] = None   # which group this session belongs to
    is_own_group: bool = True             # False when student was in another group's session

    model_config = ConfigDict(from_attributes=True)


class StudentProfileOut(BaseModel):
    """Complete student profile for the detail page."""
    # ── Personal info ──
    id: UUID
    matricule: str
    nom: str
    prenom: str
    email: str

    # ── Academic info ──
    filiere: str
    niveau: str
    groupe: str
    status: str

    # ── Account info (from student_users if linked) ──
    avatar_url: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    created_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None

    # ── Attendance summary (own group sessions only) ──
    total_absences: int        # absences in own group's sessions
    total_sessions: int        # total sessions for the student's group
    attendance_rate: float     # percentage 0-100
    cross_session_count: int = 0  # times student appeared in another group's session

    # ── Absence history (all records, flagged by is_own_group) ──
    absence_history: List[AbsenceHistoryItem]

    model_config = ConfigDict(from_attributes=True)

