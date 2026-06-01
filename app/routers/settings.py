"""
routers/settings.py — Administration Settings
===============================================

GET  /api/v1/settings/administration   Read current academic-year config & absence rules
PUT  /api/v1/settings/administration   Update config (admin only)

Auth:
  GET  → Admin or Teacher (both need to read thresholds)
  PUT  → Admin only
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin, require_admin_or_teacher
from app.models.settings import AdministrationSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["Settings"])


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class SemesterConfig(BaseModel):
    number: int
    start_date: date
    end_date: date


class AcademicYearConfig(BaseModel):
    label: str
    current_semester: int
    semesters: list[SemesterConfig]


class AbsenceRulesConfig(BaseModel):
    max_absences: int
    warning_threshold: int
    justification_deadline_days: int


class AdministrationSettingsResponse(BaseModel):
    academic_year: AcademicYearConfig
    absence_rules: AbsenceRulesConfig


class AdministrationSettingsUpdate(BaseModel):
    academic_year: AcademicYearConfig
    absence_rules: AbsenceRulesConfig

    @field_validator("absence_rules")
    @classmethod
    def warning_must_be_less_than_max(cls, v: AbsenceRulesConfig) -> AbsenceRulesConfig:
        if v.warning_threshold >= v.max_absences:
            raise ValueError(
                "warning_threshold must be strictly less than max_absences."
            )
        if v.warning_threshold < 1 or v.max_absences < 1:
            raise ValueError("Thresholds must be positive integers.")
        if v.justification_deadline_days < 0:
            raise ValueError("justification_deadline_days must be non-negative.")
        return v


class UpdateResponse(BaseModel):
    success: bool
    message: str


# ── Internal helper ────────────────────────────────────────────────────────────

async def get_settings(db: AsyncSession) -> AdministrationSettings:
    """Fetch the settings row, creating a default row if the table is empty."""
    result = await db.execute(
        select(AdministrationSettings).where(AdministrationSettings.singleton_guard == True).limit(1)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = AdministrationSettings(id=1, singleton_guard=True)
        db.add(settings)
        await db.flush()
    return settings


def _to_response(s: AdministrationSettings) -> AdministrationSettingsResponse:
    semesters: list[SemesterConfig] = []
    if s.sem1_start_date and s.sem1_end_date:
        semesters.append(SemesterConfig(number=1, start_date=s.sem1_start_date, end_date=s.sem1_end_date))
    if s.sem2_start_date and s.sem2_end_date:
        semesters.append(SemesterConfig(number=2, start_date=s.sem2_start_date, end_date=s.sem2_end_date))

    return AdministrationSettingsResponse(
        academic_year=AcademicYearConfig(
            label=s.academic_year_label,
            current_semester=s.current_semester,
            semesters=semesters,
        ),
        absence_rules=AbsenceRulesConfig(
            max_absences=s.max_absences,
            warning_threshold=s.warning_threshold,
            justification_deadline_days=s.justification_deadline_days,
        ),
    )


# ── GET /settings/administration ──────────────────────────────────────────────

@router.get(
    "/administration",
    response_model=AdministrationSettingsResponse,
    summary="Get current administration settings",
    description="""
Returns the current academic year configuration and absence rules.

**Response fields:**

| Field | Type | Description |
|---|---|---|
| `academic_year.label` | `string` | Display label, e.g. `"2025 – 2026"` |
| `academic_year.current_semester` | `int` | Active semester number (1 or 2) |
| `semesters[].number` | `int` | Semester number |
| `semesters[].start_date` | `string` | ISO date — semester start (YYYY-MM-DD) |
| `semesters[].end_date` | `string` | ISO date — semester end (YYYY-MM-DD) |
| `absence_rules.max_absences` | `int` | **Per-module** exclusion threshold (excluded at this many absences in one module) |
| `absence_rules.warning_threshold` | `int` | **Per-module** warning threshold (warned at this many absences in one module) |
| `absence_rules.justification_deadline_days` | `int` | Days allowed to submit a justification after an absence |

> **Rule:** 5 absences in the **same module** → exclusion. 20 total absences but no module
> with 5 → **not** excluded.

**Auth:** Admin or Teacher (JWT).
""",
)
async def get_administration_settings(
    current_user=Depends(require_admin_or_teacher),
    db: AsyncSession = Depends(get_db),
) -> AdministrationSettingsResponse:
    settings = await get_settings(db)
    return _to_response(settings)


# ── PUT /settings/administration ──────────────────────────────────────────────

@router.put(
    "/administration",
    response_model=UpdateResponse,
    summary="Update administration settings",
    description="""
Update the academic year configuration and/or absence rules. Triggered by the
**Edit** button on the admin settings page.

**Validation rules:**
- `warning_threshold` must be **strictly less than** `max_absences`.
- All threshold values must be positive integers.
- Dates must be valid ISO 8601 format (`YYYY-MM-DD`).
- Both `start_date` and `end_date` must be provided for each semester.

**Auth:** Admin only (JWT).
""",
)
async def update_administration_settings(
    payload: AdministrationSettingsUpdate,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpdateResponse:
    settings = await get_settings(db)

    # Academic year
    settings.academic_year_label = payload.academic_year.label
    settings.current_semester = payload.academic_year.current_semester

    # Semester dates — accept up to 2 semesters
    settings.sem1_start_date = None
    settings.sem1_end_date = None
    settings.sem2_start_date = None
    settings.sem2_end_date = None

    for sem in payload.academic_year.semesters:
        if sem.number == 1:
            settings.sem1_start_date = sem.start_date
            settings.sem1_end_date = sem.end_date
        elif sem.number == 2:
            settings.sem2_start_date = sem.start_date
            settings.sem2_end_date = sem.end_date
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Semester number must be 1 or 2, got {sem.number}.",
            )

    # Absence rules
    settings.max_absences = payload.absence_rules.max_absences
    settings.warning_threshold = payload.absence_rules.warning_threshold
    settings.justification_deadline_days = payload.absence_rules.justification_deadline_days
    settings.updated_at = datetime.now(timezone.utc)

    db.add(settings)
    await db.commit()

    logger.info(
        "Administration settings updated by admin %s: warning=%d max=%d",
        current_user.id,
        settings.warning_threshold,
        settings.max_absences,
    )

    return UpdateResponse(success=True, message="Administration settings updated successfully.")
