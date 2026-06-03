"""
routers/compensation_requests.py — Compensation Request Endpoints
==================================================================

POST   /api/v1/compensation-requests           Student submits a makeup request
GET    /api/v1/compensation-requests           Teacher fetches pending/all requests for their sessions
PATCH  /api/v1/compensation-requests/{id}/approve  Teacher approves → student added to session
PATCH  /api/v1/compensation-requests/{id}/reject   Teacher rejects
GET    /api/v1/compensation-requests/me        Student sees their own requests
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import CompensationRequestStatus, UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.models import (
    AcademicStudent,
    Absence,
    CompensationRequest,
    Module,
    PlanningSession,
    Salle,
    Session,
    Teacher,
    session_students,
    session_groups,
)
from app.models.planning_session import planning_session_teachers
from app.models.student import Student
from app.schemas.compensation_request import (
    AvailableCompensationSession,
    AvailableCompensationSessionsResponse,
    CompensationRequestApproveOut,
    CompensationRequestCreate,
    CompensationRequestListResponse,
    CompensationRequestOut,
    CompensationRequestRejectBody,
    CompensationRequestRejectOut,
    StudentModule,
)
from app.services.notification_service import create_and_push

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Compensation Requests"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _format_time(session: Session) -> str:
    st = session.start_time.strftime("%H:%M") if session.start_time else ""
    et = session.end_time.strftime("%H:%M") if session.end_time else ""
    return f"{st} - {et}"


async def _enrich_request(
    req: CompensationRequest,
    db: AsyncSession,
) -> CompensationRequestOut:
    """Build the enriched CompensationRequestOut from the ORM object."""
    session: Session = req.session
    academic_student: AcademicStudent = req.student

    # Gather all groups for the session
    extra_groups = (
        await db.execute(
            select(session_groups.c.group_name).where(
                session_groups.c.session_id == session.id
            )
        )
    ).scalars().all()
    all_groups: list[str] = ([session.group] if session.group else []) + list(extra_groups)
    target_group = ", ".join(all_groups) if all_groups else None

    room_code: Optional[str] = session.room.code if session.room else None
    subject: Optional[str] = session.module.nom if session.module else None

    return CompensationRequestOut(
        id=req.id,
        status=req.status.value if hasattr(req.status, "value") else req.status,
        session_id=req.session_id,
        student_matricule=req.student_matricule,
        studentName=f"{academic_student.nom} {academic_student.prenom}",
        studentGroup=academic_student.groupe,
        targetGroup=target_group,
        room=room_code,
        date=session.date,
        time=_format_time(session),
        subject=subject,
        reason=req.reason,
        rejection_reason=req.rejection_reason,
        created_at=req.created_at,
        approved_at=req.approved_at,
        rejected_at=req.rejected_at,
    )


async def _get_request_or_404(db: AsyncSession, request_id: UUID) -> CompensationRequest:
    req = (
        await db.execute(
            select(CompensationRequest)
            .options(
                selectinload(CompensationRequest.session).options(
                    selectinload(Session.module),
                    selectinload(Session.room),
                ),
                selectinload(CompensationRequest.student),
            )
            .where(CompensationRequest.id == request_id)
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compensation request not found.")
    return req


async def _notify_student(
    db: AsyncSession,
    student_matricule: str,
    *,
    type: str,
    title: str,
    body: str,
    compensation_request_id: UUID,
) -> None:
    """Send an in-app notification to the student identified by their matricule."""
    student_user = (
        await db.execute(
            select(Student).where(Student.student_id == student_matricule)
        )
    ).scalar_one_or_none()
    if student_user is None:
        return
    await create_and_push(
        db,
        recipient_id=student_user.id,
        recipient_role="student",
        type=type,
        title=title,
        body=body,
        justification_id=compensation_request_id,
    )


# ── GET /compensation-requests/my-modules ─────────────────────────────────────

@router.get(
    "/compensation-requests/my-modules",
    response_model=list[StudentModule],
    summary="List the student's modules (for compensation module picker)",
    description="""
Returns all modules that have planning sessions for the student's group.
Use the `id` from this list as `module_id` in the **available-sessions** endpoint.

**Auth:** Student only (JWT).
""",
)
async def list_my_modules(
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    student_matricule: str = current_user.student_id

    academic_student = (
        await db.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == student_matricule)
        )
    ).scalar_one_or_none()
    if academic_student is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your academic profile was not found in the system.",
        )

    student_group = academic_student.groupe.strip()  # e.g. "G4"
    student_year = academic_student.niveau.strip()   # e.g. "1CS"

    # ── Query PlanningSession templates (always populated after CSV import) ──
    # Concrete Session rows are only created lazily when a teacher opens the
    # app, so querying Session here would yield nothing before that happens.
    planning_sessions = (
        await db.execute(
            select(PlanningSession.subject).distinct()
            .where(
                and_(
                    func.lower(func.trim(PlanningSession.year)) == student_year.lower(),
                    func.lower(func.trim(PlanningSession.group)) == student_group.lower(),
                )
            )
        )
    ).scalars().all()

    if not planning_sessions:
        return []

    # For each unique subject found in the planning, find-or-create a Module row
    # so we always have a stable UUID to hand back to the frontend.
    result: list[StudentModule] = []
    seen_subjects: set[str] = set()
    for subject in planning_sessions:
        if subject in seen_subjects:
            continue
        seen_subjects.add(subject)

        module = (
            await db.execute(select(Module).where(Module.code == subject))
        ).scalar_one_or_none()
        if module is None:
            module = Module(code=subject, nom=subject)
            db.add(module)
            await db.flush()

        result.append(StudentModule(id=module.id, code=module.code, nom=module.nom))

    result.sort(key=lambda m: m.nom)
    return result


# ── GET /compensation-requests/available-sessions ─────────────────────────────

@router.get(
    "/compensation-requests/available-sessions",
    response_model=AvailableCompensationSessionsResponse,
    summary="List sessions available for compensation this week",
    description="""
Returns all sessions for the selected module in the **current week (Mon–Sun)**,
taught by the same teacher who teaches the student's group for that module.

Call **my-modules** first to get the correct `module_id`.

**Query params:**
- `module_id` (required) — the module UUID from `/my-modules`

**Auth:** Student only (JWT).
""",
)
async def list_available_sessions_for_compensation(
    module_id: UUID = Query(...),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    student_matricule: str = current_user.student_id

    academic_student = (
        await db.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == student_matricule)
        )
    ).scalar_one_or_none()
    if academic_student is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your academic profile was not found in the system.",
        )

    student_group = academic_student.groupe.strip()
    student_group_lower = student_group.lower()

    # ── Find teacher(s) for this module + student group ──────────────────────
    # First try concrete Session rows (fast path for recurring sessions).
    teacher_ids: list = (
        await db.execute(
            select(Session.teacher_id).distinct()
            .outerjoin(session_groups, session_groups.c.session_id == Session.id)
            .where(
                and_(
                    Session.module_id == module_id,
                    or_(
                        func.lower(func.trim(Session.group)) == student_group_lower,
                        func.lower(func.trim(session_groups.c.group_name)) == student_group_lower,
                    ),
                )
            )
        )
    ).scalars().all()

    if not teacher_ids:
        # Fall back to PlanningSession templates — these always exist after the
        # admin imports the CSV, even before any teacher opens the app for the day.
        module = (
            await db.execute(select(Module).where(Module.id == module_id))
        ).scalar_one_or_none()
        if module is None:
            return AvailableCompensationSessionsResponse(data=[], total=0)

        teacher_ids = (
            await db.execute(
                select(planning_session_teachers.c.teacher_id).distinct()
                .join(
                    PlanningSession,
                    planning_session_teachers.c.planning_session_id == PlanningSession.id,
                )
                .where(
                    and_(
                        func.lower(func.trim(PlanningSession.subject)) == module.code.lower(),
                        func.lower(func.trim(PlanningSession.group)) == student_group_lower,
                    )
                )
            )
        ).scalars().all()

    if not teacher_ids:
        return AvailableCompensationSessionsResponse(data=[], total=0)

    # Current week: Monday to Sunday
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    # Materialize sessions for all involved teachers for the entire week
    # so they are available to be selected for compensation.
    from app.services.session_service import materialise_sessions_for_teacher
    teachers = (
        await db.execute(select(Teacher).where(Teacher.id.in_(teacher_ids)))
    ).scalars().all()
    
    # We do this in a nested transaction just in case, though it should be safe
    async with db.begin_nested():
        for t in teachers:
            for i in range(7):
                current_day = monday + timedelta(days=i)
                await materialise_sessions_for_teacher(db, t, current_day)

    sessions = (
        await db.execute(
            select(Session)
            .options(
                selectinload(Session.room),
                selectinload(Session.module),
                selectinload(Session.teacher),
            )
            .where(
                and_(
                    Session.module_id == module_id,
                    Session.teacher_id.in_(teacher_ids),
                    Session.date >= monday,
                    Session.date <= sunday,
                )
            )
            .order_by(Session.date, Session.start_time)
        )
    ).scalars().all()

    result = [
        AvailableCompensationSession(
            session_id=s.id,
            date=s.date,
            start_time=s.start_time.strftime("%H:%M") if s.start_time else "",
            end_time=s.end_time.strftime("%H:%M") if s.end_time else "",
            room=s.room.code if s.room else None,
            teacher_name=f"{s.teacher.first_name} {s.teacher.last_name}" if s.teacher else None,
            module_name=s.module.nom if s.module else None,
        )
        for s in sessions
    ]
    return AvailableCompensationSessionsResponse(data=result, total=len(result))


# ── POST /compensation-requests ────────────────────────────────────────────────

@router.post(
    "/compensation-requests",
    response_model=CompensationRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Student submits a compensation request for a session",
    description="""
Student requests to attend a session belonging to another group to make up
for a missed session.

**Validations:**
- The session must exist.
- The student must not already have a PENDING request for the same session.
- The student must either not belong to the session's group, or must have
  been marked absent in that session.

On success, the teacher who owns the session receives an in-app notification.
""",
)
async def create_compensation_request(
    data: CompensationRequestCreate,
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    # current_user is a Student (student_users); their matricule is student_id
    student_matricule: str = current_user.student_id

    # Load target session
    session = (
        await db.execute(
            select(Session)
            .options(
                selectinload(Session.module),
                selectinload(Session.room),
                selectinload(Session.teacher),
            )
            .where(Session.id == data.session_id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    # Load AcademicStudent record
    academic_student = (
        await db.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == student_matricule)
        )
    ).scalar_one_or_none()
    if academic_student is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your academic profile (matricule) was not found in the system.",
        )

    # Gather all session groups
    extra_groups = (
        await db.execute(
            select(session_groups.c.group_name).where(
                session_groups.c.session_id == session.id
            )
        )
    ).scalars().all()
    all_groups = ([session.group] if session.group else []) + list(extra_groups)
    normalized_groups = [g.lower().strip() for g in all_groups if g]

    student_in_group = academic_student.groupe.lower().strip() in normalized_groups

    if student_in_group:
        # Allow only if the student was actually marked absent
        absence = (
            await db.execute(
                select(Absence).where(
                    Absence.session_id == session.id,
                    Absence.student_matricule == student_matricule,
                    Absence.is_absent.is_(True),
                )
            )
        ).scalar_one_or_none()
        if absence is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "You already belong to this session's group. "
                    "Compensation requests are only allowed if you were marked absent."
                ),
            )

    # Validate original_session_id has a real absence for this student
    if data.original_session_id is not None:
        original_absence = (
            await db.execute(
                select(Absence).where(
                    Absence.session_id == data.original_session_id,
                    Absence.student_matricule == student_matricule,
                    Absence.is_absent.is_(True),
                )
            )
        ).scalar_one_or_none()
        if original_absence is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No confirmed absence found for the specified original session.",
            )

    # No duplicate pending request for the same session
    existing = (
        await db.execute(
            select(CompensationRequest).where(
                CompensationRequest.session_id == session.id,
                CompensationRequest.student_matricule == student_matricule,
                CompensationRequest.status == CompensationRequestStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending compensation request for this session.",
        )

    req = CompensationRequest(
        session_id=session.id,
        original_session_id=data.original_session_id,
        student_matricule=student_matricule,
        reason=data.reason,
        status=CompensationRequestStatus.PENDING,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)

    # Notify the session's teacher
    teacher: Teacher = session.teacher
    if teacher:
        subject_name = session.module.nom if session.module else "Unknown subject"
        await create_and_push(
            db,
            recipient_id=teacher.id,
            recipient_role="teacher",
            type="compensation_request_submitted",
            title="New Compensation Request",
            body=(
                f"{academic_student.nom} {academic_student.prenom} "
                f"(Group {academic_student.groupe}) is requesting to attend your "
                f"{subject_name} session on {session.date}."
            ),
            justification_id=req.id,
        )

    # Re-load with relationships for the response
    req = await _get_request_or_404(db, req.id)
    return await _enrich_request(req, db)


# ── GET /compensation-requests ─────────────────────────────────────────────────

@router.get(
    "/compensation-requests",
    response_model=CompensationRequestListResponse,
    summary="Teacher fetches compensation requests for their sessions",
    description="""
Returns all compensation requests targeting sessions owned by the calling teacher.

**Query params (all optional):**
- `status` — PENDING | APPROVED | REJECTED
- `session_id` — filter to a specific session
- `page` / `page_size` — pagination

**Auth:** Teacher only (JWT).
""",
)
async def list_compensation_requests(
    req_status: Optional[str] = Query(default=None, alias="status"),
    session_id: Optional[UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(CompensationRequest)
        .join(Session, CompensationRequest.session_id == Session.id)
        .options(
            selectinload(CompensationRequest.session).options(
                selectinload(Session.module),
                selectinload(Session.room),
            ),
            selectinload(CompensationRequest.student),
        )
        .where(Session.teacher_id == current_user.id)
        .order_by(CompensationRequest.created_at.desc())
    )

    if req_status:
        try:
            status_enum = CompensationRequestStatus(req_status.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status '{req_status}'. Use PENDING, APPROVED, or REJECTED.",
            )
        stmt = stmt.where(CompensationRequest.status == status_enum)

    if session_id:
        stmt = stmt.where(CompensationRequest.session_id == session_id)

    total: int = (
        await db.execute(
            select(func.count())
            .select_from(CompensationRequest)
            .join(Session, CompensationRequest.session_id == Session.id)
            .where(Session.teacher_id == current_user.id)
            .where(
                *([CompensationRequest.status == CompensationRequestStatus(req_status.upper())]
                  if req_status else [])
            )
            .where(
                *([CompensationRequest.session_id == session_id] if session_id else [])
            )
        )
    ).scalar() or 0

    requests = (
        await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    enriched = [await _enrich_request(r, db) for r in requests]
    return CompensationRequestListResponse(data=enriched, total=total)


# ── PATCH /compensation-requests/{id}/approve ─────────────────────────────────

@router.patch(
    "/compensation-requests/{request_id}/approve",
    response_model=CompensationRequestApproveOut,
    summary="Teacher approves a compensation request",
    description="""
Approves the request and automatically adds the student to the session roster
(inserts into `session_students`) so they appear in attendance with the option
to mark them present.

**Auth:** Teacher only. The teacher must own the session.
""",
)
async def approve_compensation_request(
    request_id: UUID,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    req = await _get_request_or_404(db, request_id)

    if req.session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the teacher of this session.",
        )

    if req.status != CompensationRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is already {req.status.value}.",
        )

    now = datetime.now(timezone.utc)
    req.status = CompensationRequestStatus.APPROVED
    req.approved_at = now
    db.add(req)

    # Add student to session roster if not already there
    already_linked = (
        await db.execute(
            select(session_students).where(
                session_students.c.session_id == req.session_id,
                session_students.c.student_matricule == req.student_matricule,
            )
        )
    ).first()
    if already_linked is None:
        await db.execute(
            session_students.insert().values(
                session_id=req.session_id,
                student_matricule=req.student_matricule,
            )
        )

    await db.flush()

    # Notify student
    session = req.session
    subject_name = session.module.nom if session.module else "Unknown subject"
    await _notify_student(
        db,
        req.student_matricule,
        type="compensation_request_approved",
        title="Compensation Request Approved",
        body=(
            f"Your request to attend the {subject_name} session "
            f"on {session.date} has been approved. "
            f"You are now on the attendance roster."
        ),
        compensation_request_id=req.id,
    )

    return CompensationRequestApproveOut(
        id=req.id,
        status=req.status.value,
        approved_at=req.approved_at,
    )


# ── PATCH /compensation-requests/{id}/reject ──────────────────────────────────

@router.patch(
    "/compensation-requests/{request_id}/reject",
    response_model=CompensationRequestRejectOut,
    summary="Teacher rejects a compensation request",
)
async def reject_compensation_request(
    request_id: UUID,
    body: Optional[CompensationRequestRejectBody] = None,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    req = await _get_request_or_404(db, request_id)

    if req.session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the teacher of this session.",
        )

    if req.status != CompensationRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is already {req.status.value}.",
        )

    now = datetime.now(timezone.utc)
    req.status = CompensationRequestStatus.REJECTED
    req.rejected_at = now
    if body and body.reason:
        req.rejection_reason = body.reason
    db.add(req)
    await db.flush()

    # Notify student
    session = req.session
    subject_name = session.module.nom if session.module else "Unknown subject"
    rejection_note = f" Reason: {req.rejection_reason}" if req.rejection_reason else ""
    await _notify_student(
        db,
        req.student_matricule,
        type="compensation_request_rejected",
        title="Compensation Request Rejected",
        body=(
            f"Your request to attend the {subject_name} session "
            f"on {session.date} has been rejected.{rejection_note}"
        ),
        compensation_request_id=req.id,
    )

    return CompensationRequestRejectOut(
        id=req.id,
        status=req.status.value,
        rejection_reason=req.rejection_reason,
        rejected_at=req.rejected_at,
    )


# ── GET /compensation-requests/me ─────────────────────────────────────────────

@router.get(
    "/compensation-requests/me",
    response_model=CompensationRequestListResponse,
    summary="Student views their own compensation requests",
)
async def my_compensation_requests(
    req_status: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    student_matricule: str = current_user.student_id

    stmt = (
        select(CompensationRequest)
        .options(
            selectinload(CompensationRequest.session).options(
                selectinload(Session.module),
                selectinload(Session.room),
            ),
            selectinload(CompensationRequest.student),
        )
        .where(CompensationRequest.student_matricule == student_matricule)
        .order_by(CompensationRequest.created_at.desc())
    )

    if req_status:
        try:
            status_enum = CompensationRequestStatus(req_status.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status '{req_status}'. Use PENDING, APPROVED, or REJECTED.",
            )
        stmt = stmt.where(CompensationRequest.status == status_enum)

    total_stmt = (
        select(func.count())
        .select_from(CompensationRequest)
        .where(CompensationRequest.student_matricule == student_matricule)
    )
    if req_status:
        total_stmt = total_stmt.where(
            CompensationRequest.status == CompensationRequestStatus(req_status.upper())
        )
    total: int = (await db.execute(total_stmt)).scalar() or 0

    requests = (
        await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    enriched = [await _enrich_request(r, db) for r in requests]
    return CompensationRequestListResponse(data=enriched, total=total)
