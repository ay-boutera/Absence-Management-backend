from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.config.enums import AbsenceMotifEnum, SessionType


# ── Shared building blocks ─────────────────────────────────────────────────────

class AbsenceRateStat(BaseModel):
    total_records: int
    absences: int
    rate: float = Field(..., description="Absence rate in percent (0–100)")


class ByFiliereStat(AbsenceRateStat):
    filiere: str


class ByModuleStat(AbsenceRateStat):
    module_id: UUID
    module_code: str
    module_nom: str


class BySessionTypeStat(AbsenceRateStat):
    session_type: str


class WeeklyTrendPoint(BaseModel):
    year: int
    week: int
    absences: int
    total_records: int
    rate: float


# ── US-45 / US-50: Admin global dashboard ─────────────────────────────────────

class AdminDashboardResponse(BaseModel):
    date_from: Optional[date]
    date_to: Optional[date]
    filiere_filter: Optional[str]
    module_filter: Optional[UUID]
    session_type_filter: Optional[str]
    total_sessions: int
    total_records: int
    total_absences: int
    overall_rate: float
    by_filiere: list[ByFiliereStat]
    by_module: list[ByModuleStat]
    by_session_type: list[BySessionTypeStat]
    weekly_trend: list[WeeklyTrendPoint]


# ── US-46: Motif breakdown ────────────────────────────────────────────────────

class MotifStat(BaseModel):
    motif: str
    label: str
    count: int
    percentage: float


class MotifBreakdownResponse(BaseModel):
    total_absences_with_motif: int
    breakdown: list[MotifStat]


# ── US-47: Teacher dashboard ──────────────────────────────────────────────────

class TeacherModuleStat(BaseModel):
    module_id: UUID
    module_code: str
    module_nom: str
    total_sessions: int
    total_records: int
    total_absences: int
    absence_rate: float
    justified_count: int
    unjustified_count: int
    justified_ratio: float


class TeacherDashboardResponse(BaseModel):
    teacher_id: UUID
    date_from: Optional[date]
    date_to: Optional[date]
    by_module: list[TeacherModuleStat]
    weekly_trend: list[WeeklyTrendPoint]
    overall_justified_ratio: float


# ── US-48: Threshold alerts ────────────────────────────────────────────────────

class ThresholdAlert(BaseModel):
    student_matricule: str
    student_name: str
    module_id: UUID
    module_code: str
    module_nom: str
    absence_count: int
    total_sessions: int
    absence_rate: float
    threshold_pct: float


class ThresholdAlertsResponse(BaseModel):
    threshold_pct: float
    alerts: list[ThresholdAlert]


# ── US-51: Period comparison ───────────────────────────────────────────────────

class PeriodStats(BaseModel):
    period_from: date
    period_to: date
    total_absences: int
    total_records: int
    absence_rate: float
    by_filiere: list[ByFiliereStat]
    by_module: list[ByModuleStat]


class PeriodComparisonResponse(BaseModel):
    period1: PeriodStats
    period2: PeriodStats
    rate_delta: float = Field(..., description="period2.rate - period1.rate")
    count_delta: int = Field(..., description="period2.absences - period1.absences")


# ── US-52: Student self-stats ─────────────────────────────────────────────────

class StudentModuleStat(BaseModel):
    module_id: UUID
    module_code: str
    module_nom: str
    total_sessions: int
    total_absences: int
    absence_rate: float
    justified_count: int
    is_at_risk: bool


class StudentStatsResponse(BaseModel):
    student_matricule: str
    total_sessions: int
    total_absences: int
    overall_rate: float
    justified_count: int
    unjustified_count: int
    by_module: list[StudentModuleStat]


# ── US-53: Realtime polling ────────────────────────────────────────────────────

class RealtimeStatsResponse(BaseModel):
    absences_today: int
    active_sessions_now: int
    last_absence_recorded_at: Optional[datetime]
    timestamp: datetime


# ── US-54: Weekly report ───────────────────────────────────────────────────────

class WeeklyReportRequest(BaseModel):
    recipients: list[str] = Field(..., min_length=1)
