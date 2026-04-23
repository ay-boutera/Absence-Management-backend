"""
schemas/planning.py — Pydantic models for the Planning endpoints.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Teacher info embedded in session responses ─────────────────────────────────
class TeacherInfo(BaseModel):
    id: UUID
    employee_id: Optional[str] = None
    first_name: str
    last_name: str

    model_config = {"from_attributes": True}


# ── Single session in schedule response ───────────────────────────────────────
class PlanningSessionOut(BaseModel):
    id: UUID
    day: str
    time_start: str          # formatted HH:MM
    time_end: str            # formatted HH:MM
    type: str
    subject: str
    room: Optional[str] = None
    group: Optional[str] = None
    year: str
    section: Optional[str] = None
    speciality: Optional[str] = None
    semester: str
    teachers: list[TeacherInfo] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ── Response envelope for GET /planning/my-schedule ──────────────────────────
class ScheduleResponse(BaseModel):
    total: int = Field(..., example=44)
    sessions: list[PlanningSessionOut] = Field(default_factory=list)


# ── Response envelope for POST /import/planning (extends ImportResponse) ──────
class PlanningImportResponse(BaseModel):
    imported: int = Field(..., example=44)
    updated: int = Field(..., example=3)
    errors: int = Field(..., example=0)
    error_report: list[dict] = Field(default_factory=list)
    history_id: Optional[UUID] = None
    sessions_generated: int = Field(default=0, example=704, description="Number of concrete Session rows created")
