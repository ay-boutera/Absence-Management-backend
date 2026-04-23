"""
routers/sessions.py — Session Endpoints
=========================================

GET  /api/v1/sessions/today          Teacher's sessions for today (US-18).
                                     Sessions are materialised on-demand from
                                     PlanningSession if they don't exist yet.

GET  /api/v1/sessions/{id}/students  Students in a session with their current
                                     absence status (US-26).

GET  /api/v1/sessions/{id}/summary   Live attendance counts (US-24).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import SessionStatusEnum
from app.db import get_db
from app.helpers.permissions import get_current_user_bearer, require_role
from app.models import (
    Absence,
    Module,
    PlanningSession,
    Salle,
    Session,
    SessionAttendanceSummary,
    Teacher,
    UserRole,
)
from app.models.student import AcademicStudent
from app.schemas.session import (
    AttendanceSummaryOut,
    SessionListResponse,
    SessionOut,
    StudentAttendanceOut,
    StudentListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Sessions"])

# French day names matching PlanningSession.day values
_TODAY_DAY_NAMES = {
    0: "Lundi",
    1: "Mardi",
    2: "Mercredi",
    3: "Jeudi",
    4: "Vendredi",
    5: "Samedi",
    6: "Dimanche",
}


async def _get_or_create_module(db: AsyncSession, subject: str) -> Module:
    result = await db.execute(select(Module).where(Module.code == subject))
    module = result.scalar_one_or_none()
    if module is None:
        module = Module(code=subject, nom=subject)
        db.add(module)
        await db.flush()
    return module


async def _get_or_create_salle(db: AsyncSession, room_code: str) -> Salle:
    result = await db.execute(select(Salle).where(Salle.code == room_code))
    salle = result.scalar_one_or_none()
    if salle is None:
        salle = Salle(code=room_code)
        db.add(salle)
        await db.flush()
    return salle


async def _materialise_sessions_for_teacher(
    db: AsyncSession,
    teacher: Teacher,
    today: date,
) -> list[Session]:
    """
    Find PlanningSession entries for today's day, then find-or-create Session
    rows for today's date.  Returns the list of Session objects.
    """
    today_day_name = _TODAY_DAY_NAMES.get(today.weekday())
    if today_day_name is None:
        return []

    planning_q = (
        select(PlanningSession)
        .options(selectinload(PlanningSession.teachers))
        .where(
            and_(
                PlanningSession.teachers.any(Teacher.id == teacher.id),
                PlanningSession.day == today_day_name,
            )
        )
    )
    planning_sessions = list((await db.execute(planning_q)).scalars().all())

    result_sessions: list[Session] = []

    for ps in planning_sessions:
        # Find or create Module and Salle
        module = await _get_or_create_module(db, ps.subject)
        salle = await _get_or_create_salle(db, ps.room) if ps.room else None

        existing = (
            await db.execute(
                select(Session)
                .options(
                    selectinload(Session.module),
                    selectinload(Session.teacher),
                    selectinload(Session.room),
                )
                .where(
                    and_(
                        Session.planning_session_id == ps.id,
                        Session.teacher_id == teacher.id,
                        Session.date == today,
                    )
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = Session(
                planning_session_id=ps.id,
                module_id=module.id,
                teacher_id=teacher.id,
                room_id=salle.id if salle else None,
                group=ps.group,
                year=ps.year,
                section=ps.section,
                speciality=ps.speciality,
                semester=ps.semester,
                date=today,
                start_time=ps.time_start,
                end_time=ps.time_end,
                type=ps.type,
                status=SessionStatusEnum.SCHEDULED,
                is_makeup=False,
            )
            db.add(existing)
            await db.flush()
            await db.refresh(existing, ["module", "teacher", "room"])

        result_sessions.append(existing)

    return result_sessions


# ── GET /sessions/today ────────────────────────────────────────────────────────
@router.get(
    "/sessions/today",
    response_model=SessionListResponse,
    summary="Teacher's sessions for today (US-18)",
    description="""
Returns all sessions scheduled for the authenticated teacher **today**.

Sessions are created on-demand from the weekly planning template if they
don't exist yet for today's date, so no pre-generation step is required.

**Auth:** Teacher only (JWT).
""",
)
async def get_today_sessions(
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    teacher = (await db.execute(select(Teacher).where(Teacher.id == current_user.id))).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil enseignant introuvable.")

    today = datetime.now(timezone.utc).date()

    async with db.begin_nested():
        sessions = await _materialise_sessions_for_teacher(db, teacher, today)

    sessions_ordered = sorted(sessions, key=lambda s: s.start_time)

    out = []
    for s in sessions_ordered:
        if s.module is None:
            await db.refresh(s, ["module", "teacher", "room"])
        out.append(
            SessionOut(
                id=s.id,
                date=s.date,
                start_time=s.start_time,
                end_time=s.end_time,
                type=s.type,
                status=s.status,
                is_makeup=s.is_makeup,
                group=s.group,
                year=s.year,
                section=s.section,
                speciality=s.speciality,
                semester=s.semester,
                module=s.module,
                teacher=s.teacher,
                room=s.room,
            )
        )

    return SessionListResponse(total=len(out), sessions=out)


# ── GET /sessions/{id}/students ────────────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/students",
    response_model=StudentListResponse,
    summary="Students in a session with attendance status (US-26)",
    description="""
Returns the list of students in the session's group along with their current
absence status (is_absent = true/false/null if not yet marked).

Supports a `q` query param for full-text search on first_name, last_name,
and matricule (server-side filtering for large groups).

**Auth:** Teacher or Admin.
""",
)
async def get_session_students(
    session_id: UUID,
    q: Optional[str] = Query(default=None, description="Search by name or matricule"),
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    session = (
        await db.execute(
            select(Session)
            .options(selectinload(Session.absences))
            .where(Session.id == session_id)
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")

    # Build student query for the session's group
    student_filters = []
    if session.year:
        student_filters.append(AcademicStudent.niveau == session.year)
    if session.group:
        student_filters.append(AcademicStudent.groupe == session.group)

    if q:
        like_pattern = f"%{q}%"
        from sqlalchemy import or_
        student_filters.append(
            or_(
                AcademicStudent.nom.ilike(like_pattern),
                AcademicStudent.prenom.ilike(like_pattern),
                AcademicStudent.matricule.ilike(like_pattern),
            )
        )

    students_q = select(AcademicStudent)
    if student_filters:
        from sqlalchemy import and_
        students_q = students_q.where(and_(*student_filters))
    students_q = students_q.order_by(AcademicStudent.nom, AcademicStudent.prenom)

    students = list((await db.execute(students_q)).scalars().all())

    # Index existing absences by matricule
    absence_by_matricule: dict[str, Absence] = {
        a.student_matricule: a for a in session.absences
    }

    out = []
    for student in students:
        absence = absence_by_matricule.get(student.matricule)
        out.append(
            StudentAttendanceOut(
                matricule=student.matricule,
                nom=student.nom,
                prenom=student.prenom,
                groupe=student.groupe,
                is_absent=absence.is_absent if absence else None,
                absence_id=absence.id if absence else None,
            )
        )

    return StudentListResponse(total=len(out), students=out)


# ── GET /sessions/{id}/summary ─────────────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/summary",
    response_model=AttendanceSummaryOut,
    summary="Live attendance count for a session (US-24)",
    description="""
Returns the real-time attendance counts: total students, present, absent, and pending.

Counts are computed from the absences table on each request.
The frontend polls this endpoint every 5 s (useAttendanceSummary hook).

**Auth:** Teacher or Admin.
""",
)
async def get_session_summary(
    session_id: UUID,
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    session = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")

    # Count students in the group
    student_filters = []
    if session.year:
        student_filters.append(AcademicStudent.niveau == session.year)
    if session.group:
        student_filters.append(AcademicStudent.groupe == session.group)

    from sqlalchemy import func, and_ as sa_and
    total_result = await db.execute(
        select(func.count()).select_from(AcademicStudent).where(sa_and(*student_filters) if student_filters else True)
    )
    total_students = total_result.scalar_one() or 0

    absences = list(
        (await db.execute(select(Absence).where(Absence.session_id == session_id))).scalars().all()
    )
    absent_count = sum(1 for a in absences if a.is_absent)
    present_count = sum(1 for a in absences if not a.is_absent)
    pending_count = total_students - len(absences)

    # Keep summary table in sync
    summary = (
        await db.execute(select(SessionAttendanceSummary).where(SessionAttendanceSummary.session_id == session_id))
    ).scalar_one_or_none()

    if summary is None:
        summary = SessionAttendanceSummary(
            session_id=session_id,
            total_students=total_students,
            present_count=present_count,
            absent_count=absent_count,
            pending_count=pending_count,
        )
        db.add(summary)
    else:
        summary.total_students = total_students
        summary.present_count = present_count
        summary.absent_count = absent_count
        summary.pending_count = pending_count
        db.add(summary)

    await db.flush()

    return AttendanceSummaryOut(
        session_id=session_id,
        total_students=total_students,
        present_count=present_count,
        absent_count=absent_count,
        pending_count=pending_count,
    )
