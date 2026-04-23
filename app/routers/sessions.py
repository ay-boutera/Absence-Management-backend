"""
routers/sessions.py — Session Endpoints
=========================================

GET  /api/v1/sessions/today              Teacher's sessions for today (US-18).
GET  /api/v1/sessions/my-sessions        All sessions for the teacher, with filters (Feature 1.3).
GET  /api/v1/sessions/{id}/attendance    Student list with attendance state (Feature 1.1).
PUT  /api/v1/sessions/{id}/attendance    Bulk submit / update attendance (Feature 1.2).
GET  /api/v1/sessions/{id}/students      Students in a session (US-26).
GET  /api/v1/sessions/{id}/summary       Live attendance counts (US-24).
POST /api/v1/sessions/{id}/groups        Add a group to a session (Feature 2.1).
POST /api/v1/sessions/{id}/students      Add a student directly to a session (Feature 2.2).
"""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import AbsenceSourceEnum, SessionStatusEnum
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
    session_groups,
    session_students,
)
from app.models.student import AcademicStudent
from app.schemas.session import (
    AddGroupToSessionRequest,
    AddGroupToSessionResponse,
    AddStudentToSessionRequest,
    AddStudentToSessionResponse,
    AttendanceListResponse,
    AttendanceSubmit,
    AttendanceSubmitResult,
    AttendanceSummaryOut,
    MySessionListResponse,
    MySessionOut,
    SessionListResponse,
    SessionOut,
    StudentAttendanceOut,
    StudentAttendanceRecord,
    StudentListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Sessions"])

_TODAY_DAY_NAMES = {
    0: "Lundi",
    1: "Mardi",
    2: "Mercredi",
    3: "Jeudi",
    4: "Vendredi",
    5: "Samedi",
    6: "Dimanche",
}


# ── Internal helpers ───────────────────────────────────────────────────────────

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
    today: date_type,
) -> list[Session]:
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


async def _get_session_or_404(db: AsyncSession, session_id: UUID) -> Session:
    s = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")
    return s


def _assert_owns_session(session: Session, current_user) -> None:
    if session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You are not assigned to this session.",
        )


# ── GET /sessions/today ────────────────────────────────────────────────────────
@router.get(
    "/sessions/today",
    response_model=SessionListResponse,
    summary="Teacher's sessions for today (US-18)",
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


# ── GET /sessions/my-sessions ─────────────────────────────────────────────────
@router.get(
    "/sessions/my-sessions",
    response_model=MySessionListResponse,
    summary="All sessions for the authenticated teacher, with filters (Feature 1.3)",
    description="""
Returns all sessions assigned to the authenticated teacher, ordered by date
descending (most recent first).

**Filters (all optional):**
- `date` — exact date (YYYY-MM-DD)
- `lesson_name` — partial search on module name
- `group` — filter by group name (e.g. "G1")
- `status` — session status (SCHEDULED | IN_PROGRESS | COMPLETED | CANCELLED)

Each result includes `has_attendance: bool` — whether at least one absence
record exists for that session.

**Auth:** Teacher only (JWT).
""",
)
async def get_my_sessions(
    date: Optional[str] = Query(default=None, description="Filter by date (YYYY-MM-DD)"),
    lesson_name: Optional[str] = Query(default=None, description="Partial search on module name"),
    group: Optional[str] = Query(default=None, description="Filter by group name"),
    session_status: Optional[str] = Query(default=None, alias="status", description="Filter by session status"),
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Session)
        .options(
            selectinload(Session.module),
            selectinload(Session.room),
        )
        .where(Session.teacher_id == current_user.id)
    )

    if date:
        try:
            parsed_date = date_type.fromisoformat(date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )
        stmt = stmt.where(Session.date == parsed_date)

    if session_status:
        stmt = stmt.where(Session.status == session_status)

    if group:
        stmt = stmt.where(Session.group == group)

    if lesson_name:
        stmt = stmt.join(Module, Session.module_id == Module.id).where(
            Module.nom.ilike(f"%{lesson_name}%")
        )

    stmt = stmt.order_by(Session.date.desc(), Session.start_time)

    sessions = list((await db.execute(stmt)).scalars().all())

    if not sessions:
        return MySessionListResponse(total=0, sessions=[])

    session_ids = [s.id for s in sessions]

    # Which sessions already have at least one absence record
    attended_ids: set[UUID] = set(
        (
            await db.execute(
                select(Absence.session_id)
                .where(Absence.session_id.in_(session_ids))
                .distinct()
            )
        ).scalars().all()
    )

    # Extra groups added via session_groups table
    extra_groups_rows = (
        await db.execute(
            select(session_groups.c.session_id, session_groups.c.group_name).where(
                session_groups.c.session_id.in_(session_ids)
            )
        )
    ).all()
    extra_groups_by_session: dict[UUID, list[str]] = {}
    for row in extra_groups_rows:
        extra_groups_by_session.setdefault(row.session_id, []).append(row.group_name)

    result: list[MySessionOut] = []
    for s in sessions:
        groups: list[str] = [s.group] if s.group else []
        groups += extra_groups_by_session.get(s.id, [])

        result.append(
            MySessionOut(
                id=s.id,
                date=s.date,
                start_time=s.start_time,
                end_time=s.end_time,
                type=s.type,
                status=s.status,
                lesson_name=s.module.nom if s.module else "",
                room=s.room.code if s.room else None,
                groups=groups,
                has_attendance=s.id in attended_ids,
            )
        )

    return MySessionListResponse(total=len(result), sessions=result)


# ── GET /sessions/{id}/attendance ─────────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/attendance",
    response_model=AttendanceListResponse,
    summary="Get student list with attendance state for a session (Feature 1.1)",
    description="""
Returns the full list of students belonging to the groups assigned to this
session, along with each student's current attendance state.

Fields per student:
- `is_present` — False by default (no record yet); reflects the stored value once marked
- `participation` — null if not recorded (e.g. "A+", "B-")
- `total_absences` — cumulative absences for this student across **all** sessions

**Auth:** Teacher only. The teacher must be assigned to (own) this session.
""",
)
async def get_session_attendance(
    session_id: UUID,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session_or_404(db, session_id)
    _assert_owns_session(session, current_user)

    # Collect all group names for this session
    all_groups: list[str] = [session.group] if session.group else []
    extra_group_rows = (
        await db.execute(
            select(session_groups.c.group_name).where(
                session_groups.c.session_id == session_id
            )
        )
    ).all()
    all_groups += [row[0] for row in extra_group_rows]
    all_groups = list(dict.fromkeys(all_groups))  # deduplicate, preserve order

    # Students from group membership
    group_students: list[AcademicStudent] = []
    if all_groups:
        filters = [AcademicStudent.groupe.in_(all_groups)]
        if session.year:
            filters.append(AcademicStudent.niveau == session.year)
        group_students = list(
            (
                await db.execute(select(AcademicStudent).where(and_(*filters)))
            ).scalars().all()
        )

    # Students added directly via session_students
    direct_matricule_rows = (
        await db.execute(
            select(session_students.c.student_matricule).where(
                session_students.c.session_id == session_id
            )
        )
    ).all()
    direct_matricules = {row[0] for row in direct_matricule_rows}

    direct_students: list[AcademicStudent] = []
    if direct_matricules:
        direct_students = list(
            (
                await db.execute(
                    select(AcademicStudent).where(
                        AcademicStudent.matricule.in_(direct_matricules)
                    )
                )
            ).scalars().all()
        )

    # Merge and deduplicate
    seen: set[str] = set()
    all_students: list[AcademicStudent] = []
    for s in group_students + direct_students:
        if s.matricule not in seen:
            seen.add(s.matricule)
            all_students.append(s)
    all_students.sort(key=lambda s: (s.nom, s.prenom))

    # Existing absence records for this session
    absences = list(
        (
            await db.execute(select(Absence).where(Absence.session_id == session_id))
        ).scalars().all()
    )
    absence_by_matricule: dict[str, Absence] = {a.student_matricule: a for a in absences}

    # Total absences per student (one aggregated query)
    total_absence_counts: dict[str, int] = {}
    if seen:
        rows = (
            await db.execute(
                select(Absence.student_matricule, func.count().label("cnt"))
                .where(Absence.is_absent.is_(True))
                .where(Absence.student_matricule.in_(seen))
                .group_by(Absence.student_matricule)
            )
        ).all()
        total_absence_counts = {row.student_matricule: row.cnt for row in rows}

    records: list[StudentAttendanceRecord] = []
    for student in all_students:
        absence = absence_by_matricule.get(student.matricule)
        records.append(
            StudentAttendanceRecord(
                student_id=student.id,
                matricule=student.matricule,
                nom=student.nom,
                prenom=student.prenom,
                email=student.email,
                avatar_url=None,
                is_present=(not absence.is_absent) if absence else False,
                participation=absence.participation if absence else None,
                total_absences=total_absence_counts.get(student.matricule, 0),
            )
        )

    return AttendanceListResponse(
        session_id=session_id,
        total=len(records),
        records=records,
    )


# ── PUT /sessions/{id}/attendance ─────────────────────────────────────────────
@router.put(
    "/sessions/{session_id}/attendance",
    response_model=AttendanceSubmitResult,
    summary="Bulk submit or update attendance for a session (Feature 1.2)",
    description="""
Idempotent bulk attendance submission. Can be called multiple times to update
records — useful when a teacher returns to a completed session to make changes.

- If a record exists for `(session_id, student_matricule)` → **UPDATE**.
- Otherwise → **CREATE**.

Returns `{ "created": N, "updated": N }`.

**Auth:** Teacher only. The teacher must own this session.
""",
)
async def submit_session_attendance(
    session_id: UUID,
    data: AttendanceSubmit,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session_or_404(db, session_id)
    _assert_owns_session(session, current_user)

    created_count = 0
    updated_count = 0

    for record in data.records:
        existing = (
            await db.execute(
                select(Absence).where(
                    Absence.session_id == session_id,
                    Absence.student_matricule == record.student_matricule,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(
                Absence(
                    session_id=session_id,
                    student_matricule=record.student_matricule,
                    recorded_by=current_user.id,
                    is_absent=not record.is_present,
                    participation=record.participation,
                    source=AbsenceSourceEnum.PWA,
                    synced_at=datetime.now(timezone.utc),
                )
            )
            created_count += 1
        else:
            existing.is_absent = not record.is_present
            existing.participation = record.participation
            existing.recorded_by = current_user.id
            existing.synced_at = datetime.now(timezone.utc)
            db.add(existing)
            updated_count += 1

    await db.flush()
    return AttendanceSubmitResult(updated=updated_count, created=created_count)


# ── GET /sessions/{id}/students ────────────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/students",
    response_model=StudentListResponse,
    summary="Students in a session with absence status (US-26)",
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

    student_filters = []
    if session.year:
        student_filters.append(AcademicStudent.niveau == session.year)
    if session.group:
        student_filters.append(AcademicStudent.groupe == session.group)

    if q:
        like_pattern = f"%{q}%"
        student_filters.append(
            or_(
                AcademicStudent.nom.ilike(like_pattern),
                AcademicStudent.prenom.ilike(like_pattern),
                AcademicStudent.matricule.ilike(like_pattern),
            )
        )

    students_q = select(AcademicStudent)
    if student_filters:
        students_q = students_q.where(and_(*student_filters))
    students_q = students_q.order_by(AcademicStudent.nom, AcademicStudent.prenom)

    students = list((await db.execute(students_q)).scalars().all())

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
)
async def get_session_summary(
    session_id: UUID,
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    session = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")

    student_filters = []
    if session.year:
        student_filters.append(AcademicStudent.niveau == session.year)
    if session.group:
        student_filters.append(AcademicStudent.groupe == session.group)

    total_result = await db.execute(
        select(func.count()).select_from(AcademicStudent).where(
            and_(*student_filters) if student_filters else True
        )
    )
    total_students = total_result.scalar_one() or 0

    absences = list(
        (await db.execute(select(Absence).where(Absence.session_id == session_id))).scalars().all()
    )
    absent_count = sum(1 for a in absences if a.is_absent)
    present_count = sum(1 for a in absences if not a.is_absent)
    pending_count = total_students - len(absences)

    summary = (
        await db.execute(
            select(SessionAttendanceSummary).where(
                SessionAttendanceSummary.session_id == session_id
            )
        )
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


# ── POST /sessions/{id}/groups ────────────────────────────────────────────────
@router.post(
    "/sessions/{session_id}/groups",
    response_model=AddGroupToSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a group to a session (Feature 2.1)",
    description="""
Links a group to a session so its students appear in the attendance list.

Returns **409 Conflict** if the group is already assigned (either as the
session's primary group or via a previous call to this endpoint).

**Auth:** Teacher only. The teacher must own this session.
""",
)
async def add_group_to_session(
    session_id: UUID,
    data: AddGroupToSessionRequest,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session_or_404(db, session_id)
    _assert_owns_session(session, current_user)

    if session.group == data.group_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.group_name}' is already the primary group of this session.",
        )

    existing = (
        await db.execute(
            select(session_groups).where(
                session_groups.c.session_id == session_id,
                session_groups.c.group_name == data.group_name,
            )
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.group_name}' is already linked to this session.",
        )

    await db.execute(
        session_groups.insert().values(
            session_id=session_id,
            group_name=data.group_name,
        )
    )
    await db.flush()

    return AddGroupToSessionResponse(session_id=session_id, group_name=data.group_name)


# ── POST /sessions/{id}/students ──────────────────────────────────────────────
@router.post(
    "/sessions/{session_id}/students",
    response_model=AddStudentToSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a student directly to a session (Feature 2.2)",
    description="""
Links an individual student to a session — for edge cases where a student
needs to attend a session that is not part of their regular group.

Returns **404** if the student matricule does not exist.
Returns **409 Conflict** if the student is already directly linked.

**Auth:** Teacher only. The teacher must own this session.
""",
)
async def add_student_to_session(
    session_id: UUID,
    data: AddStudentToSessionRequest,
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session_or_404(db, session_id)
    _assert_owns_session(session, current_user)

    student = (
        await db.execute(
            select(AcademicStudent).where(
                AcademicStudent.matricule == data.student_matricule
            )
        )
    ).scalar_one_or_none()
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with matricule '{data.student_matricule}' not found.",
        )

    existing = (
        await db.execute(
            select(session_students).where(
                session_students.c.session_id == session_id,
                session_students.c.student_matricule == data.student_matricule,
            )
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student '{data.student_matricule}' is already linked to this session.",
        )

    await db.execute(
        session_students.insert().values(
            session_id=session_id,
            student_matricule=data.student_matricule,
        )
    )
    await db.flush()

    return AddStudentToSessionResponse(
        session_id=session_id,
        student_matricule=data.student_matricule,
    )
