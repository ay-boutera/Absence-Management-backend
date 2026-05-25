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
    student_id: str
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    level: str
    group: str
    total_absences: int
    status: str
    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "student_id": "212132049",
                "full_name": "John Doe",
                "email": "j.doe@esi-sba.dz",
                "avatar_url": None,
                "level": "1CS",
                "group": "G3",
                "total_absences": 0,
                "status": "normal",
                "is_active": True
            }
        }
    )


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
    is_cross_session: bool = False

    model_config = ConfigDict(from_attributes=True)


class ModuleAttendanceItem(BaseModel):
    """Attendance summary for one module."""
    module_name: str
    total_sessions: int
    absences: int
    attendance_rate: Optional[float] = None

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
    module_attendance: List[ModuleAttendanceItem] = []

    # ── Absence history (all records, flagged by is_own_group) ──
    absence_history: List[AbsenceHistoryItem]

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "e4c5f24b-1bfa-45fd-9b9c-630ba72e54aa",
                "matricule": "232332029109",
                "nom": "ABADELIA",
                "prenom": "Mohammed imad eddine",
                "email": "mie.abadelia@esi-sba.dz",
                "filiere": "CS",
                "niveau": "1CS",
                "groupe": "G3",
                "status": "normal",
                "avatar_url": None,
                "phone": None,
                "is_active": True,
                "created_at": "2026-05-25T13:24:36.016766Z",
                "last_activity": None,
                "total_absences": 0,
                "total_sessions": 1,
                "attendance_rate": 100.0,
                "cross_session_count": 0,
                "module_attendance": [
                    {
                        "module_name": "Archi",
                        "total_sessions": 1,
                        "absences": 0,
                        "attendance_rate": 100.0
                    },
                    {
                        "module_name": "ACSI",
                        "total_sessions": 0,
                        "absences": 0,
                        "attendance_rate": None
                    }
                ],
                "absence_history": [
                    {
                        "absence_id": "b8091a68-ad52-4f3e-871e-8077cdc993aa",
                        "session_id": "a05509e4-290d-416f-9311-100ad3fc11cc",
                        "date": "2026-05-20T00:00:00",
                        "start_time": "08:00:00",
                        "end_time": "15:00:00",
                        "module_name": "Archi",
                        "teacher_name": "TRARI NOUR ELFOUAD",
                        "is_absent": False,
                        "justification_status": None,
                        "session_group": "G3",
                        "is_own_group": True,
                        "is_cross_session": False
                    }
                ]
            }
        },
    )
