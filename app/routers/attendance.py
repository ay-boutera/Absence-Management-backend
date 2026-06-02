"""
routers/attendance.py — QR-Code Attendance Endpoints
=====================================================

GET  /api/v1/sessions/{session_id}/qr-code   Teacher fetches (or auto-refreshes) the current QR nonce.
POST /api/v1/attendance/mark-present         Student marks themselves present by submitting a scanned nonce.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.enums import AbsenceSourceEnum, UserRole
from app.db import get_db
from app.helpers.permissions import require_role, require_student_bearer
from app.helpers.role_users import user_role
from app.models import (
    Absence,
    Session,
    session_groups,
    session_students,
)
from app.models.student import AcademicStudent
from app.models.session_nonce import SessionNonce, _generate_nonce, _nonce_expiry, NONCE_TTL_SECONDS

router = APIRouter(tags=["Attendance"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class QRCodeResponse(BaseModel):
    session_id: UUID
    nonce: str
    expires_at: datetime
    ttl_seconds: int


class MarkPresentRequest(BaseModel):
    session_id: UUID
    nonce: str


class MarkPresentResponse(BaseModel):
    success: bool
    session_id: UUID
    student_matricule: str
    message: str


# ── GET /sessions/{session_id}/qr-code ────────────────────────────────────────

@router.get(
    "/sessions/{session_id}/qr-code",
    response_model=QRCodeResponse,
    summary="Get (or auto-refresh) the QR nonce for a session (Teacher only)",
    description="""
Returns the current QR attendance nonce for the given session. If no nonce
exists or the existing one has expired, a new one is automatically generated.

**How to use:** The teacher's website should call this endpoint every ~25 seconds
and render the returned `{ session_id, nonce }` as a QR code. The student's
mobile app scans the QR code and posts both values to `POST /attendance/mark-present`.

Each nonce is valid for **30 seconds**. Students who scan after expiry will receive
a clear error telling them to scan the refreshed code.

**Auth:** Teacher only (cookie). The teacher must own the session.
""",
)
async def get_session_qr_code(
    session_id: UUID,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")
    if session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You are not assigned to this session.",
        )

    now = datetime.now(timezone.utc)

    existing = (
        await db.execute(
            select(SessionNonce).where(SessionNonce.session_id == session_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = SessionNonce(
            session_id=session_id,
            nonce=_generate_nonce(),
            expires_at=_nonce_expiry(),
        )
        db.add(existing)
    else:
        # Normalise tzinfo before comparing
        expires_at = existing.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            existing.nonce = _generate_nonce()
            existing.expires_at = _nonce_expiry()
            db.add(existing)

    await db.flush()

    return QRCodeResponse(
        session_id=session_id,
        nonce=existing.nonce,
        expires_at=existing.expires_at,
        ttl_seconds=NONCE_TTL_SECONDS,
    )


# ── POST /attendance/mark-present ─────────────────────────────────────────────

@router.post(
    "/attendance/mark-present",
    response_model=MarkPresentResponse,
    summary="Mark student present via QR code scan (Student only)",
    description="""
Called by the student mobile app after scanning the QR code shown on the
teacher's screen.

**Validation steps:**
1. The `nonce` must match the active nonce stored for `session_id`.
2. The nonce must not be expired (lifetime: 30 seconds).
3. The authenticated student must be enrolled in the session (via group or direct link).

On success, an absence record is created or updated to `is_present = true`.

**Auth:** Student only (Bearer token — sent automatically by the mobile app).
""",
)
async def mark_present(
    data: MarkPresentRequest,
    current_user=Depends(require_student_bearer),
    db: AsyncSession = Depends(get_db),
):
    # ── 1. Validate nonce ──────────────────────────────────────────────────────
    nonce_row = (
        await db.execute(
            select(SessionNonce).where(SessionNonce.session_id == data.session_id)
        )
    ).scalar_one_or_none()

    if nonce_row is None or nonce_row.nonce != data.nonce:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid QR code. Ask your teacher to display the latest QR code.",
        )

    now = datetime.now(timezone.utc)
    expires_at = nonce_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="QR code has expired. Please scan the latest code shown by your teacher.",
        )

    # ── 2. Resolve academic record ─────────────────────────────────────────────
    # Student.student_id is the matricule stored in AcademicStudent.matricule
    academic_student = (
        await db.execute(
            select(AcademicStudent).where(
                AcademicStudent.matricule == current_user.student_id
            )
        )
    ).scalar_one_or_none()

    if academic_student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Academic record not found. Contact the administration.",
        )

    # ── 3. Verify session exists ───────────────────────────────────────────────
    session = (
        await db.execute(select(Session).where(Session.id == data.session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    # ── 4. Check enrolment ─────────────────────────────────────────────────────
    if not await _is_student_enrolled(db, session, academic_student.matricule):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not enrolled in this session.",
        )

    # ── 5. Upsert attendance record ────────────────────────────────────────────
    existing_absence = (
        await db.execute(
            select(Absence).where(
                and_(
                    Absence.session_id == data.session_id,
                    Absence.student_matricule == academic_student.matricule,
                )
            )
        )
    ).scalar_one_or_none()

    if existing_absence is None:
        db.add(
            Absence(
                session_id=data.session_id,
                student_matricule=academic_student.matricule,
                recorded_by=None,
                is_absent=False,
                source=AbsenceSourceEnum.QR,
                synced_at=now,
            )
        )
    else:
        existing_absence.is_absent = False
        existing_absence.source = AbsenceSourceEnum.QR
        existing_absence.synced_at = now
        db.add(existing_absence)

    await db.flush()

    return MarkPresentResponse(
        success=True,
        session_id=data.session_id,
        student_matricule=academic_student.matricule,
        message="You have been successfully marked as present.",
    )


# ── Internal helper ────────────────────────────────────────────────────────────

async def _is_student_enrolled(
    db: AsyncSession,
    session: Session,
    matricule: str,
) -> bool:
    """Return True if the student is directly linked to the session or belongs to one of its groups."""
    direct = (
        await db.execute(
            select(session_students).where(
                and_(
                    session_students.c.session_id == session.id,
                    session_students.c.student_matricule == matricule,
                )
            )
        )
    ).first()
    if direct is not None:
        return True

    all_groups: list[str] = [session.group] if session.group else []
    extra_rows = (
        await db.execute(
            select(session_groups.c.group_name).where(
                session_groups.c.session_id == session.id
            )
        )
    ).all()
    all_groups += [row[0] for row in extra_rows]
    normalised = [g.lower().strip() for g in all_groups if g]
    if not normalised:
        return False

    student = (
        await db.execute(
            select(AcademicStudent).where(
                and_(
                    AcademicStudent.matricule == matricule,
                    func.trim(func.lower(AcademicStudent.groupe)).in_(normalised),
                )
            )
        )
    ).scalar_one_or_none()

    return student is not None
