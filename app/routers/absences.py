"""
routers/absences.py — Absence Recording Endpoints
===================================================

POST /api/v1/absences            One-tap UPSERT: mark student absent or present. Toggles is_absent on re-tap (US-19).
GET  /api/v1/absences            List absences for a session (teacher/admin).

POST /api/v1/absences/corrections  Request a correction (US-22, US-23).
PATCH /api/v1/absences/corrections/{id}  Admin review (approve / reject).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.enums import AbsenceSourceEnum, CorrectionStatusEnum
from app.db import get_db
from app.helpers.permissions import get_current_user_bearer, require_role
from app.models import (
    Absence,
    AbsenceCorrection,
    Session,
    Teacher,
    UserRole,
)
from app.schemas.absence import (
    AbsenceCreate,
    AbsenceOut,
    AbsenceUpsertResponse,
    CorrectionCreate,
    CorrectionOut,
    CorrectionReview,
)

router = APIRouter(tags=["Absences"])

_FREE_WINDOW_MINUTES = 15


def _within_free_window(session: Session) -> bool:
    """True if the current time is within 15 min of the session's end time."""
    now = datetime.now(timezone.utc)
    session_end = datetime.combine(session.date, session.end_time).replace(tzinfo=timezone.utc)
    delta = now - session_end
    return delta.total_seconds() <= _FREE_WINDOW_MINUTES * 60


# ── POST /absences ─────────────────────────────────────────────────────────────
@router.post(
    "/absences",
    response_model=AbsenceUpsertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mark student absent / present (US-19)",
    description="""
One-tap absence marking with UPSERT semantics:

- **First tap** on a student → `INSERT` with `is_absent=true`.
- **Second tap** → toggles `is_absent` (present↔absent).

Returns `created=true` if a new row was inserted, `false` if updated.

**Auth:** Teacher (JWT).
""",
)
async def upsert_absence(
    data: AbsenceCreate,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    # Validate session exists
    session = (await db.execute(select(Session).where(Session.id == data.session_id))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")

    existing = (
        await db.execute(
            select(Absence).where(
                Absence.session_id == data.session_id,
                Absence.student_matricule == data.student_matricule,
            )
        )
    ).scalar_one_or_none()

    was_created = existing is None

    if existing is None:
        absence = Absence(
            session_id=data.session_id,
            student_matricule=data.student_matricule,
            recorded_by=current_user.id,
            is_absent=data.is_absent,
            source=data.source,
            synced_at=datetime.now(timezone.utc),
        )
        db.add(absence)
    else:
        existing.is_absent = data.is_absent
        existing.source = data.source
        existing.synced_at = datetime.now(timezone.utc)
        existing.recorded_by = current_user.id
        db.add(existing)
        absence = existing

    await db.flush()
    await db.refresh(absence)

    return AbsenceUpsertResponse(
        id=absence.id,
        session_id=absence.session_id,
        student_matricule=absence.student_matricule,
        is_absent=absence.is_absent,
        source=absence.source,
        created=was_created,
    )


# ── GET /absences ──────────────────────────────────────────────────────────────
@router.get(
    "/absences",
    response_model=list[AbsenceOut],
    summary="List absences for a session",
)
async def list_absences(
    session_id: UUID = Query(..., description="Session ID to list absences for"),
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    absences = list(
        (await db.execute(select(Absence).where(Absence.session_id == session_id))).scalars().all()
    )
    return absences


# ── POST /absences/corrections ─────────────────────────────────────────────────
@router.post(
    "/absences/corrections",
    response_model=CorrectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Request an absence correction (US-22 / US-23)",
    description="""
Submit a correction request for an existing absence record.

- If the request is within 15 minutes of the session's end time, the
  correction is **auto-approved** immediately (US-22 free window).
- Otherwise it is queued as **PENDING** for Admin review (US-23).

**Auth:** Teacher (JWT).
""",
)
async def request_correction(
    data: CorrectionCreate,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    absence = (
        await db.execute(
            select(Absence).where(Absence.id == data.absence_id)
        )
    ).scalar_one_or_none()
    if absence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Absence introuvable.")

    session = (await db.execute(select(Session).where(Session.id == absence.session_id))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")

    in_window = _within_free_window(session)
    corr_status = CorrectionStatusEnum.APPROVED if in_window else CorrectionStatusEnum.PENDING

    correction = AbsenceCorrection(
        absence_id=data.absence_id,
        requested_by=current_user.id,
        original_value=absence.is_absent,
        new_value=data.new_value,
        reason=data.reason,
        status=corr_status,
        reviewed_at=datetime.now(timezone.utc) if in_window else None,
    )
    db.add(correction)

    if in_window:
        absence.is_absent = data.new_value
        db.add(absence)

    await db.flush()
    await db.refresh(correction)
    return correction


# ── PATCH /absences/corrections/{id} ──────────────────────────────────────────
@router.patch(
    "/absences/corrections/{correction_id}",
    response_model=CorrectionOut,
    summary="Admin: approve or reject a correction (US-23)",
)
async def review_correction(
    correction_id: UUID,
    data: CorrectionReview,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    correction = (
        await db.execute(select(AbsenceCorrection).where(AbsenceCorrection.id == correction_id))
    ).scalar_one_or_none()
    if correction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Correction introuvable.")

    if correction.status != CorrectionStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cette correction est déjà {correction.status.value.lower()}.",
        )

    correction.status = data.status
    correction.reviewed_by = current_user.id
    correction.reviewed_at = datetime.now(timezone.utc)
    db.add(correction)

    if data.status == CorrectionStatusEnum.APPROVED:
        absence = (await db.execute(select(Absence).where(Absence.id == correction.absence_id))).scalar_one_or_none()
        if absence:
            absence.is_absent = correction.new_value
            db.add(absence)

    await db.flush()
    await db.refresh(correction)
    return correction
