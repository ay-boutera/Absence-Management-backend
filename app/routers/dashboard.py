"""
routers/dashboard.py — Admin Dashboard Statistics
===================================================

GET  /api/v1/dashboard/stats               Summary stat cards
GET  /api/v1/dashboard/absences-by-level   Absence rate per study level (bar chart)
GET  /api/v1/dashboard/monthly-trends      Monthly absence counts (line chart)
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.absence import UNEXCUSED_ABSENCE
from app.helpers.permissions import require_admin
from app.models import Absence, AcademicStudent, Session
from app.routers.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# Month names aligned with the spec display labels
_MONTH_NAMES: dict[int, str] = {
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
    1: "Jan", 2: "Feb", 3: "March", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
}

# Sort key so months appear in academic-year order (Sep=0 … Aug=11)
_MONTH_SORT_KEY: dict[int, int] = {
    9: 0, 10: 1, 11: 2, 12: 3,
    1: 4, 2: 5, 3: 6, 4: 7,
    5: 8, 6: 9, 7: 10, 8: 11,
}


def _current_academic_year() -> int:
    """
    Return the ending calendar year of the current academic year.
    Sep–Dec of year N  →  academic year N+1
    Jan–Aug of year N  →  academic year N
    """
    today = date_type.today()
    return today.year + 1 if today.month >= 9 else today.year


def _academic_year_range(year: int) -> tuple[date_type, date_type]:
    """
    year=2026  →  (2025-09-01, 2026-08-31)
    """
    return date_type(year - 1, 9, 1), date_type(year, 8, 31)


# ── Response schemas ───────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total_students: int
    absence_rate: float
    students_at_warning: int
    students_at_exclusion: int


class LevelAbsenceItem(BaseModel):
    level: str
    rate: float


class AbsencesByLevelResponse(BaseModel):
    year: int
    data: list[LevelAbsenceItem]


class MonthlyTrendItem(BaseModel):
    month: str
    absences: int


class MonthlyTrendsResponse(BaseModel):
    year: int
    data: list[MonthlyTrendItem]


# ── GET /dashboard/stats ──────────────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=DashboardStats,
    summary="Get dashboard summary statistics",
    description="""
Returns the 4 summary stat cards displayed on the admin dashboard.

| Field | Type | Description |
|---|---|---|
| `total_students` | `int` | Total number of enrolled students across all levels |
| `absence_rate` | `float` | Overall absence rate as a percentage (e.g. `63.2` = 63.2%) |
| `students_at_warning` | `int` | Students with ≥ `warning_threshold` absences in any single module, but below the exclusion limit |
| `students_at_exclusion` | `int` | Students with ≥ `max_absences` absences in any single module |

**Absence rate formula:** `absences_recorded_absent / total_recorded_attendance_entries × 100`

**Threshold rule (per-module):** 5 absences in the *same module* → excluded.
20 total absences spread across modules with none reaching 5 → **not** excluded.
Thresholds are configurable via `PUT /api/v1/settings/administration`.

**Auth:** Admin only (JWT).
""",
)
async def get_dashboard_stats(
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DashboardStats:
    settings = await get_settings(db)

    # 1. Total enrolled students
    total_students: int = (
        await db.execute(select(func.count()).select_from(AcademicStudent))
    ).scalar() or 0

    # 2. Overall absence rate across all recorded attendance entries
    stats_row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absent"),
            ).select_from(Absence)
        )
    ).one()
    total_records: int = stats_row.total or 0
    absent_records: int = int(stats_row.absent or 0)
    absence_rate = round(
        (absent_records / total_records * 100.0) if total_records > 0 else 0.0, 1
    )

    # 3. students_at_exclusion: distinct students with >= max_absences in any single module
    #    Only unexcused absences count toward thresholds.
    exclusion_subq = (
        select(Absence.student_matricule)
        .join(Session, Absence.session_id == Session.id)
        .where(UNEXCUSED_ABSENCE)
        .group_by(Absence.student_matricule, Session.module_id)
        .having(func.count() >= settings.max_absences)
        .distinct()
        .subquery()
    )
    students_at_exclusion: int = (
        await db.execute(select(func.count()).select_from(exclusion_subq))
    ).scalar() or 0

    # 4. students_at_warning: distinct students with >= warning_threshold in any module,
    #    but with NO module reaching the exclusion threshold.
    #    = (all students at warning level) - (students already at exclusion level)
    warning_subq = (
        select(Absence.student_matricule)
        .join(Session, Absence.session_id == Session.id)
        .where(UNEXCUSED_ABSENCE)
        .group_by(Absence.student_matricule, Session.module_id)
        .having(func.count() >= settings.warning_threshold)
        .distinct()
        .subquery()
    )
    all_at_warning: int = (
        await db.execute(select(func.count()).select_from(warning_subq))
    ).scalar() or 0
    students_at_warning = max(all_at_warning - students_at_exclusion, 0)

    return DashboardStats(
        total_students=total_students,
        absence_rate=absence_rate,
        students_at_warning=students_at_warning,
        students_at_exclusion=students_at_exclusion,
    )


# ── GET /dashboard/absences-by-level ─────────────────────────────────────────

@router.get(
    "/absences-by-level",
    response_model=AbsencesByLevelResponse,
    summary="Get absence rate per study level (bar chart)",
    description="""
Returns the absence rate per study level for the bar chart on the admin dashboard.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `year` | `int` | current academic year | Academic year ending year. `2026` → 2025–2026 year (Sep 2025 – Aug 2026). |

**Response data:**

Each item contains a study level (e.g. `1CP`, `2CP`, `1CS`, `2CS`, `3CS`) and the
absence rate percentage for that level within the requested academic year.

| Field | Type | Description |
|---|---|---|
| `year` | `int` | The academic year used for filtering |
| `data[].level` | `string` | Study level identifier |
| `data[].rate` | `float` | Absence rate percentage for that level |

**Absence rate formula (per level):** `absences_absent / total_recorded_entries × 100`

**Auth:** Admin only (JWT).
""",
)
async def get_absences_by_level(
    year: Optional[int] = Query(
        default=None,
        description="Academic year (ending calendar year). E.g. `2026` → 2025–2026 academic year.",
    ),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AbsencesByLevelResponse:
    resolved_year = year or _current_academic_year()
    start_date, end_date = _academic_year_range(resolved_year)

    rows = (
        await db.execute(
            select(
                AcademicStudent.niveau.label("level"),
                func.count().label("total"),
                func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absent"),
            )
            .select_from(Absence)
            .join(Session, Absence.session_id == Session.id)
            .join(AcademicStudent, Absence.student_matricule == AcademicStudent.matricule)
            .where(
                and_(
                    Session.date >= start_date,
                    Session.date <= end_date,
                )
            )
            .group_by(AcademicStudent.niveau)
            .order_by(AcademicStudent.niveau)
        )
    ).all()

    data = [
        LevelAbsenceItem(
            level=row.level,
            rate=round(
                (int(row.absent or 0) / row.total * 100.0) if row.total > 0 else 0.0, 1
            ),
        )
        for row in rows
    ]

    return AbsencesByLevelResponse(year=resolved_year, data=data)


# ── GET /dashboard/monthly-trends ─────────────────────────────────────────────

@router.get(
    "/monthly-trends",
    response_model=MonthlyTrendsResponse,
    summary="Get monthly absence counts (line chart)",
    description="""
Returns the total number of absences per month for the line chart on the admin dashboard.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `year` | `int` | current academic year | Academic year ending year. `2026` → 2025–2026 year (Sep 2025 – Aug 2026). |

**Response data:**

Months are returned in academic-year order (Sep → Aug). Only months that have at
least one recorded absence are included.

| Field | Type | Description |
|---|---|---|
| `year` | `int` | The academic year used for filtering |
| `data[].month` | `string` | Month abbreviation (Sep, Oct, Nov, Dec, Jan, Feb, March, Apr, May, …) |
| `data[].absences` | `int` | Total number of absent records for that month |

**Auth:** Admin only (JWT).
""",
)
async def get_monthly_trends(
    year: Optional[int] = Query(
        default=None,
        description="Academic year (ending calendar year). E.g. `2026` → 2025–2026 academic year.",
    ),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MonthlyTrendsResponse:
    resolved_year = year or _current_academic_year()
    start_date, end_date = _academic_year_range(resolved_year)

    rows = (
        await db.execute(
            select(
                extract("month", Session.date).label("month_num"),
                func.count(Absence.id).label("absences"),
            )
            .select_from(Absence)
            .join(Session, Absence.session_id == Session.id)
            .where(
                and_(
                    Absence.is_absent == True,
                    Session.date >= start_date,
                    Session.date <= end_date,
                )
            )
            .group_by(extract("month", Session.date))
        )
    ).all()

    month_map: dict[int, int] = {int(row.month_num): int(row.absences) for row in rows}

    # Sort months in academic-year order (Sep first)
    sorted_months = sorted(month_map.keys(), key=lambda m: _MONTH_SORT_KEY.get(m, m + 12))

    data = [
        MonthlyTrendItem(month=_MONTH_NAMES[m], absences=month_map[m])
        for m in sorted_months
        if m in _MONTH_NAMES
    ]

    return MonthlyTrendsResponse(year=resolved_year, data=data)
