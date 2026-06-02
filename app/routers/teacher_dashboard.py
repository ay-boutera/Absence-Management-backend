"""
routers/teacher_dashboard.py — Teacher Dashboard Statistics
============================================================

GET  /api/v1/teacher/dashboard/stats                Summary stat cards
GET  /api/v1/teacher/dashboard/module-absence-rates  Absence rate per module (bar chart)
GET  /api/v1/teacher/dashboard/threshold-alerts      Students at/near the absence threshold

All endpoints are teacher-scoped — the teacher is resolved from the JWT token.

Threshold rules (configurable via admin settings, defaults below):
  warning_threshold  = 3  absences in the same module → warning
  max_absences       = 5  absences in the same module → exclusion
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.enums import JustificationStatus
from app.db import get_db
from app.helpers.absence import UNEXCUSED_ABSENCE
from app.helpers.permissions import require_teacher
from app.models import Absence, AcademicStudent, Justification, Module, Session
from app.routers.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teacher/dashboard", tags=["Teacher Dashboard"])

# ── Academic year helpers (same convention as admin dashboard) ─────────────────

def _current_academic_year() -> int:
    today = date_type.today()
    return today.year + 1 if today.month >= 9 else today.year


def _academic_year_range(year: int) -> tuple[date_type, date_type]:
    """year=2026  →  (2025-09-01, 2026-08-31)"""
    return date_type(year - 1, 9, 1), date_type(year, 8, 31)


# ── Response schemas ───────────────────────────────────────────────────────────

class TeacherDashboardStats(BaseModel):
    students_at_risk: int
    avg_absence_rate: float


class ModuleAbsenceItem(BaseModel):
    module: str
    label: str
    rate: float
    type: str = "module"  # "module" | "justified"


class ModuleAbsenceRatesResponse(BaseModel):
    year: int
    data: list[ModuleAbsenceItem]


class ThresholdAlertItem(BaseModel):
    student_id: str
    full_name: str
    initials: str
    module: str
    absences: int
    max_warning: int
    max_absences: int
    avatar_color: str


class ThresholdAlertsResponse(BaseModel):
    total: int
    alerts: list[ThresholdAlertItem]


# ── GET /teacher/dashboard/stats ──────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=TeacherDashboardStats,
    summary="Get teacher dashboard summary statistics",
    description="""
Returns the 2 summary stat cards shown on the teacher dashboard.

| Field | Type | Description |
|---|---|---|
| `students_at_risk` | `int` | Distinct students who reached the warning threshold (≥ `warning_threshold` absences) in **any single module** of this teacher |
| `avg_absence_rate` | `float` | Average absence rate across all this teacher's modules (e.g. `8.4` = 8.4%) |

**Absence rate per module** = `absent_entries / total_recorded_entries × 100`.
`avg_absence_rate` is the simple average of per-module rates.

**Threshold rule:** based on per-module absence counts from admin settings (`warning_threshold`).

**Auth:** Teacher only (JWT).
""",
)
async def get_teacher_dashboard_stats(
    current_user=Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> TeacherDashboardStats:
    settings = await get_settings(db)

    # 1. students_at_risk: distinct students with >= warning_threshold unexcused absences
    #    in any single module of this teacher.
    at_risk_subq = (
        select(Absence.student_matricule)
        .join(Session, Absence.session_id == Session.id)
        .where(
            and_(
                Session.teacher_id == current_user.id,
                UNEXCUSED_ABSENCE,
            )
        )
        .group_by(Absence.student_matricule, Session.module_id)
        .having(func.count() >= settings.warning_threshold)
        .distinct()
        .subquery()
    )

    students_at_risk: int = (
        await db.execute(select(func.count()).select_from(at_risk_subq))
    ).scalar() or 0

    # 2. avg_absence_rate: simple average of per-module absence rates.
    module_rows = (
        await db.execute(
            select(
                Session.module_id,
                func.count().label("total"),
                func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absent"),
            )
            .select_from(Absence)
            .join(Session, Absence.session_id == Session.id)
            .where(Session.teacher_id == current_user.id)
            .group_by(Session.module_id)
        )
    ).all()

    if module_rows:
        rates = [
            int(row.absent or 0) / row.total * 100.0
            for row in module_rows
            if row.total > 0
        ]
        avg_absence_rate = round(sum(rates) / len(rates), 1) if rates else 0.0
    else:
        avg_absence_rate = 0.0

    return TeacherDashboardStats(
        students_at_risk=students_at_risk,
        avg_absence_rate=avg_absence_rate,
    )


# ── GET /teacher/dashboard/module-absence-rates ───────────────────────────────

@router.get(
    "/module-absence-rates",
    response_model=ModuleAbsenceRatesResponse,
    summary="Get absence rate per module (bar chart)",
    description="""
Returns the absence rate per module for the bar chart on the teacher dashboard.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `year` | `int` | current academic year | Ending calendar year. `2026` → 2025–2026 year. |

**Response data per module:**

| Field | Type | Description |
|---|---|---|
| `module` | `string` | Short module code (x-axis label) |
| `label` | `string` | Full module name (tooltip) |
| `rate` | `float` | Absence rate percentage for this module |
| `type` | `string` | `"module"` for real modules; `"justified"` for the virtual justified-absences bar |

**The `"Justified"` virtual bar** represents the percentage of all absences (in this
teacher's sessions) that have an approved justification:
`approved_justifications / total_absences × 100`.

**Auth:** Teacher only (JWT).
""",
)
async def get_module_absence_rates(
    year: Optional[int] = Query(
        default=None,
        description="Academic year (ending calendar year). E.g. `2026` → 2025–2026.",
    ),
    current_user=Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ModuleAbsenceRatesResponse:
    resolved_year = year or _current_academic_year()
    start_date, end_date = _academic_year_range(resolved_year)

    # 1. Per-module absence rates within the academic year
    rows = (
        await db.execute(
            select(
                Module.code.label("code"),
                Module.nom.label("nom"),
                func.count().label("total"),
                func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absent"),
            )
            .select_from(Absence)
            .join(Session, Absence.session_id == Session.id)
            .join(Module, Session.module_id == Module.id)
            .where(
                and_(
                    Session.teacher_id == current_user.id,
                    Session.date >= start_date,
                    Session.date <= end_date,
                )
            )
            .group_by(Module.id, Module.code, Module.nom)
            .order_by(Module.nom)
        )
    ).all()

    data: list[ModuleAbsenceItem] = [
        ModuleAbsenceItem(
            module=row.code,
            label=row.nom,
            rate=round(
                (int(row.absent or 0) / row.total * 100.0) if row.total > 0 else 0.0, 1
            ),
            type="module",
        )
        for row in rows
    ]

    # 2. Virtual "Justified" bar — approved justifications / total absences × 100
    total_absences_in_period = sum(int(row.absent or 0) for row in rows)

    justified_count: int = (
        await db.execute(
            select(func.count())
            .select_from(Justification)
            .join(Absence, Justification.absence_id == Absence.id)
            .join(Session, Absence.session_id == Session.id)
            .where(
                and_(
                    Session.teacher_id == current_user.id,
                    Session.date >= start_date,
                    Session.date <= end_date,
                    Justification.status == JustificationStatus.APPROVED.value,
                )
            )
        )
    ).scalar() or 0

    justified_rate = round(
        (justified_count / total_absences_in_period * 100.0)
        if total_absences_in_period > 0
        else 0.0,
        1,
    )

    data.append(
        ModuleAbsenceItem(
            module="Justified",
            label="Justified Absences",
            rate=justified_rate,
            type="justified",
        )
    )

    return ModuleAbsenceRatesResponse(year=resolved_year, data=data)


# ── GET /teacher/dashboard/threshold-alerts ───────────────────────────────────

@router.get(
    "/threshold-alerts",
    response_model=ThresholdAlertsResponse,
    summary="Get students at or near the absence threshold",
    description="""
Returns all students who have reached or exceeded the **warning threshold**
(`warning_threshold` absences in a single module) across this teacher's sessions.

Sorted by absence count descending (most critical first).

| Field | Type | Description |
|---|---|---|
| `total` | `int` | Total number of alert entries (also shown as red badge in header) |
| `alerts[].student_id` | `string` | Student UUID |
| `alerts[].full_name` | `string` | Full name (`nom prenom`) |
| `alerts[].initials` | `string` | 2-letter initials for the avatar circle |
| `alerts[].module` | `string` | Module name where the threshold was reached |
| `alerts[].absences` | `int` | Number of absences recorded in that module |
| `alerts[].threshold` | `int` | Warning threshold from admin settings |
| `alerts[].avatar_color` | `string` | `#E53935` (red) if absences exceed exclusion limit; `#FB8C00` (orange) if at warning level |

**Color logic:**
- `absences > max_absences` → 🔴 red `#E53935` (excluded)
- `warning_threshold ≤ absences ≤ max_absences` → 🟠 orange `#FB8C00` (warning)

**Auth:** Teacher only (JWT).
""",
)
async def get_threshold_alerts(
    current_user=Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ThresholdAlertsResponse:
    settings = await get_settings(db)

    # 1. Get (student, module) pairs where the student has >= warning_threshold absences
    at_risk_rows = (
        await db.execute(
            select(
                Absence.student_matricule,
                Session.module_id,
                func.count().label("absence_count"),
            )
            .select_from(Absence)
            .join(Session, Absence.session_id == Session.id)
            .where(
                and_(
                    Session.teacher_id == current_user.id,
                    UNEXCUSED_ABSENCE,
                )
            )
            .group_by(Absence.student_matricule, Session.module_id)
            .having(func.count() >= settings.warning_threshold)
            .order_by(func.count().desc())
        )
    ).all()

    if not at_risk_rows:
        return ThresholdAlertsResponse(total=0, alerts=[])

    # 2. Fetch student and module data in bulk
    matricules = {row.student_matricule for row in at_risk_rows}
    module_ids = {row.module_id for row in at_risk_rows}

    students_result = await db.execute(
        select(AcademicStudent).where(AcademicStudent.matricule.in_(matricules))
    )
    students_map: dict[str, AcademicStudent] = {
        s.matricule: s for s in students_result.scalars().all()
    }

    modules_result = await db.execute(
        select(Module).where(Module.id.in_(module_ids))
    )
    modules_map: dict = {m.id: m for m in modules_result.scalars().all()}

    # 3. Build alert items
    alerts: list[ThresholdAlertItem] = []
    for row in at_risk_rows:
        student = students_map.get(row.student_matricule)
        module = modules_map.get(row.module_id)
        if not student or not module:
            continue

        absences = int(row.absence_count)

        # Color: red if exceeded exclusion limit, orange if at warning level
        avatar_color = "#E53935" if absences > settings.max_absences else "#FB8C00"

        nom = student.nom or ""
        prenom = student.prenom or ""
        full_name = f"{nom} {prenom}".strip()
        initials = (
            (nom[0] if nom else "") + (prenom[0] if prenom else "")
        ).upper() or "??"

        alerts.append(
            ThresholdAlertItem(
                student_id=str(student.id),
                full_name=full_name,
                initials=initials,
                module=module.nom,
                absences=absences,
                max_warning=settings.warning_threshold,
                max_absences=settings.max_absences,
                avatar_color=avatar_color,
            )
        )

    return ThresholdAlertsResponse(total=len(alerts), alerts=alerts)
