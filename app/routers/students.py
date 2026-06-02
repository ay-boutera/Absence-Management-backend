from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.models import Absence, Session, Module, Teacher
from app.models.justification import Justification
from app.models.student import AcademicStudent, Student
from app.schemas.students import (
    AbsenceHistoryItem,
    AcademicStudentStatusOut,
    ModuleAttendanceItem,
    StudentAttendanceListOut,
    StudentProfileOut,
    StudentStatusUpdate,
)

router = APIRouter(tags=["Students"])


async def _justification_status_maps(
    db: AsyncSession,
    absence_rows: Sequence[Any],
) -> tuple[dict, dict]:
    """Return (by_absence_id, by_session_id) dicts mapping UUID → status string.

    Covers pending/rejected justifications that never write back to
    absence.statut_justificatif (which only reflects approved ones).
    """
    if not absence_rows:
        return {}, {}

    absence_ids = [absence.id for absence, *_ in absence_rows]
    session_ids = [session.id for _, session, *_ in absence_rows]

    rows = (
        await db.execute(
            select(Justification.absence_id, Justification.session_id, Justification.status)
            .where(
                or_(
                    Justification.absence_id.in_(absence_ids),
                    Justification.session_id.in_(session_ids),
                )
            )
        )
    ).all()

    by_absence: dict = {}
    by_session: dict = {}
    for j_absence_id, j_session_id, j_status in rows:
        val = j_status.value if hasattr(j_status, "value") else str(j_status)
        if j_absence_id:
            by_absence[j_absence_id] = val
        if j_session_id:
            by_session[j_session_id] = val

    return by_absence, by_session


# ── GET /students ──────────────────────────────────────────────────────────────

# ── GET /students/{matricule} ──────────────────────────────────────────────────
async def _get_student_profile_logic(
    matricule: str,
    db: AsyncSession,
) -> StudentProfileOut:
    # ── 1. Fetch academic record ───────────────────────────────────────────────
    academic = (
        await db.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == matricule)
        )
    ).scalar_one_or_none()

    if academic is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with matricule '{matricule}' not found.",
        )

    # ── 2. Try to find linked auth account (student_users) by email ───────────
    auth_account: Student | None = (
        await db.execute(
            select(Student).where(Student.email == academic.email)
        )
    ).scalar_one_or_none()

    # ── 3. Fetch all absence records with session + module + teacher info ──────
    absence_rows = (
        await db.execute(
            select(Absence, Session, Module, Teacher)
            .join(Session, Absence.session_id == Session.id)
            .join(Module, Session.module_id == Module.id)
            .join(Teacher, Session.teacher_id == Teacher.id)
            .where(Absence.student_matricule == matricule)
            .order_by(Session.date.desc())
        )
    ).all()

    # ── 4. Total sessions the student was supposed to attend ──────────────────
    # (sessions where the student's group / year matches)
    total_sessions_result = await db.execute(
        select(func.count(Session.id)).where(
            and_(
                Session.group == academic.groupe,
                Session.year == academic.niveau,
            )
        )
    )
    total_sessions = total_sessions_result.scalar_one() or 0

    # ── 5. Build absence history ───────────────────────────────────────────────
    just_by_absence, just_by_session = await _justification_status_maps(db, absence_rows)

    history: list[AbsenceHistoryItem] = []
    total_absences = 0
    cross_session_count = 0

    for absence, session, module, teacher in absence_rows:
        # Determine if this session belongs to the student's own group
        is_own_group = (
            session.group == academic.groupe
            and session.year == academic.niveau
        )

        if is_own_group and absence.is_absent:
            total_absences += 1
        if not is_own_group:
            cross_session_count += 1

        # Prefer live justification status (catches pending/rejected);
        # fall back to statut_justificatif for approved records.
        justification_status = (
            just_by_absence.get(absence.id)
            or just_by_session.get(session.id)
            or absence.statut_justificatif
        )

        history.append(
            AbsenceHistoryItem(
                absence_id=absence.id,
                session_id=session.id,
                date=session.date,
                start_time=str(session.start_time),
                end_time=str(session.end_time),
                module_name=module.nom if module else None,
                teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
                is_absent=absence.is_absent,
                justification_status=justification_status,
                session_group=session.group,
                is_own_group=is_own_group,
                is_cross_session=not is_own_group,
            )
        )

    # ── 6. Compute attendance rate (own group sessions only) ───────────────────
    if total_sessions > 0:
        attendance_rate = round((1 - total_absences / total_sessions) * 100, 1)
    else:
        attendance_rate = 100.0

    # ── 7. Compute per-module attendance ──────────────────────────────────────
    # Module scope:
    # - include all modules taught in the student's level/year for the same semester(s)
    # - semester(s) are inferred from the student's own group sessions
    # - if no own-group sessions exist yet, fall back to all semesters in the level
    own_group_semesters_rows = (
        await db.execute(
            select(Session.semester)
            .where(
                and_(
                    Session.group == academic.groupe,
                    Session.year == academic.niveau,
                    Session.semester.is_not(None),
                )
            )
            .distinct()
        )
    ).all()
    own_group_semesters = [row[0] for row in own_group_semesters_rows if row[0]]

    module_scope_q = (
        select(Module)
        .join(Session, Session.module_id == Module.id)
        .where(Session.year == academic.niveau)
    )
    if own_group_semesters:
        module_scope_q = module_scope_q.where(Session.semester.in_(own_group_semesters))

    module_rows = (await db.execute(module_scope_q.distinct().order_by(Module.nom.asc()))).scalars().all()

    semester_scope_q = (
        select(Session.module_id, Session.semester)
        .join(Module, Session.module_id == Module.id)
        .where(and_(Session.year == academic.niveau, Session.semester.is_not(None)))
    )
    if own_group_semesters:
        semester_scope_q = semester_scope_q.where(Session.semester.in_(own_group_semesters))
    module_semester_map = {mid: sem for mid, sem in (await db.execute(semester_scope_q.distinct())).all()}

    sessions_per_module_rows = (
        await db.execute(
            select(Session.module_id, func.count(Session.id))
            .where(
                and_(
                    Session.group == academic.groupe,
                    Session.year == academic.niveau,
                )
            )
            .group_by(Session.module_id)
        )
    ).all()
    absences_per_module_rows = (
        await db.execute(
            select(Session.module_id, func.count(Absence.id))
            .join(Session, Absence.session_id == Session.id)
            .where(
                and_(
                    Absence.student_matricule == matricule,
                    Absence.is_absent.is_(True),
                    Session.group == academic.groupe,
                    Session.year == academic.niveau,
                )
            )
            .group_by(Session.module_id)
        )
    ).all()

    sessions_per_module = {module_id: count for module_id, count in sessions_per_module_rows}
    absences_per_module = {module_id: count for module_id, count in absences_per_module_rows}
    module_attendance: list[ModuleAttendanceItem] = []
    for module in module_rows:
        module_total_sessions = int(sessions_per_module.get(module.id, 0) or 0)
        module_absences = int(absences_per_module.get(module.id, 0) or 0)
        module_rate = None
        if module_total_sessions > 0:
            module_rate = round((1 - module_absences / module_total_sessions) * 100, 1)

        module_attendance.append(
            ModuleAttendanceItem(
                module_name=cast(str, module.nom),
                semester=module_semester_map.get(module.id),
                total_sessions=module_total_sessions,
                absences=module_absences,
                attendance_rate=module_rate,
            )
        )

    return StudentProfileOut(
        id=cast(UUID, academic.id),
        matricule=cast(str, academic.matricule),
        nom=cast(str, academic.nom),
        prenom=cast(str, academic.prenom),
        email=cast(str, academic.email),
        filiere=cast(str, academic.filiere),
        niveau=cast(str, academic.niveau),
        groupe=cast(str, academic.groupe),
        status=cast(str, academic.status),
        # From auth account (if linked)
        avatar_url=cast(Optional[str], auth_account.avatar_url) if auth_account else None,
        phone=cast(Optional[str], auth_account.phone) if auth_account else None,
        is_active=cast(Optional[bool], auth_account.is_active) if auth_account else None,
        created_at=cast(Optional[datetime], auth_account.created_at) if auth_account else cast(Optional[datetime], academic.created_at),
        last_activity=cast(Optional[datetime], auth_account.last_activity) if auth_account else None,
        # Attendance (own group only)
        total_absences=total_absences,
        total_sessions=total_sessions,
        attendance_rate=attendance_rate,
        cross_session_count=cross_session_count,
        module_attendance=module_attendance,
        absence_history=history,
    )


async def _get_student_profile_from_account(
    student: Student,
    db: AsyncSession,
) -> StudentProfileOut:
    student_matricule = str(student.student_id).strip()
    group_value = student.group or ""

    absence_rows = (
        await db.execute(
            select(Absence, Session, Module, Teacher)
            .join(Session, Absence.session_id == Session.id)
            .join(Module, Session.module_id == Module.id)
            .join(Teacher, Session.teacher_id == Teacher.id)
            .where(Absence.student_matricule == student_matricule)
            .order_by(Session.date.desc())
        )
    ).all()

    total_sessions_result = await db.execute(
        select(func.count(Session.id)).where(
            and_(Session.group == group_value, Session.year == student.level)
        )
    )
    total_sessions = total_sessions_result.scalar_one() or 0

    just_by_absence, just_by_session = await _justification_status_maps(db, absence_rows)

    history: list[AbsenceHistoryItem] = []
    total_absences = 0
    cross_session_count = 0

    for absence, session, module, teacher in absence_rows:
        is_own_group = session.group == group_value and session.year == student.level

        if is_own_group and absence.is_absent:
            total_absences += 1
        if not is_own_group:
            cross_session_count += 1

        justification_status = (
            just_by_absence.get(absence.id)
            or just_by_session.get(session.id)
            or absence.statut_justificatif
        )

        history.append(
            AbsenceHistoryItem(
                absence_id=absence.id,
                session_id=session.id,
                date=session.date,
                start_time=str(session.start_time),
                end_time=str(session.end_time),
                module_name=module.nom if module else None,
                teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
                is_absent=absence.is_absent,
                justification_status=justification_status,
                session_group=session.group,
                is_own_group=is_own_group,
                is_cross_session=not is_own_group,
            )
        )

    if total_sessions > 0:
        attendance_rate = round((1 - total_absences / total_sessions) * 100, 1)
    else:
        attendance_rate = 100.0

    own_group_semesters_rows = (
        await db.execute(
            select(Session.semester)
            .where(
                and_(
                    Session.group == group_value,
                    Session.year == student.level,
                    Session.semester.is_not(None),
                )
            )
            .distinct()
        )
    ).all()
    own_group_semesters = [row[0] for row in own_group_semesters_rows if row[0]]

    module_scope_q = (
        select(Module)
        .join(Session, Session.module_id == Module.id)
        .where(Session.year == student.level)
    )
    if own_group_semesters:
        module_scope_q = module_scope_q.where(Session.semester.in_(own_group_semesters))

    module_rows = (
        await db.execute(module_scope_q.distinct().order_by(Module.nom.asc()))
    ).scalars().all()

    semester_scope_q = (
        select(Session.module_id, Session.semester)
        .join(Module, Session.module_id == Module.id)
        .where(and_(Session.year == student.level, Session.semester.is_not(None)))
    )
    if own_group_semesters:
        semester_scope_q = semester_scope_q.where(Session.semester.in_(own_group_semesters))
    module_semester_map = {mid: sem for mid, sem in (await db.execute(semester_scope_q.distinct())).all()}

    sessions_per_module_rows = (
        await db.execute(
            select(Session.module_id, func.count(Session.id))
            .where(
                and_(Session.group == group_value, Session.year == student.level)
            )
            .group_by(Session.module_id)
        )
    ).all()
    absences_per_module_rows = (
        await db.execute(
            select(Session.module_id, func.count(Absence.id))
            .join(Session, Absence.session_id == Session.id)
            .where(
                and_(
                    Absence.student_matricule == student_matricule,
                    Absence.is_absent.is_(True),
                    Session.group == group_value,
                    Session.year == student.level,
                )
            )
            .group_by(Session.module_id)
        )
    ).all()

    sessions_per_module = {module_id: count for module_id, count in sessions_per_module_rows}
    absences_per_module = {module_id: count for module_id, count in absences_per_module_rows}
    module_attendance: list[ModuleAttendanceItem] = []
    for module in module_rows:
        module_total_sessions = int(sessions_per_module.get(module.id, 0) or 0)
        module_absences = int(absences_per_module.get(module.id, 0) or 0)
        module_rate = None
        if module_total_sessions > 0:
            module_rate = round((1 - module_absences / module_total_sessions) * 100, 1)

        module_attendance.append(
            ModuleAttendanceItem(
                module_name=cast(str, module.nom),
                semester=module_semester_map.get(module.id),
                total_sessions=module_total_sessions,
                absences=module_absences,
                attendance_rate=module_rate,
            )
        )

    return StudentProfileOut(
        id=cast(UUID, student.id),
        matricule=student_matricule,
        nom=cast(str, student.last_name),
        prenom=cast(str, student.first_name),
        email=cast(str, student.email),
        filiere=cast(str, student.program),
        niveau=cast(str, student.level),
        groupe=cast(str, group_value),
        status="normal",
        avatar_url=cast(Optional[str], student.avatar_url),
        phone=cast(Optional[str], student.phone),
        is_active=cast(Optional[bool], student.is_active),
        created_at=cast(Optional[datetime], student.created_at),
        last_activity=cast(Optional[datetime], student.last_activity),
        total_absences=total_absences,
        total_sessions=total_sessions,
        attendance_rate=attendance_rate,
        cross_session_count=cross_session_count,
        module_attendance=module_attendance,
        absence_history=history,
    )



@router.get(
    "/students/me",
    response_model=StudentProfileOut,
    summary="Get my student profile and absence summary",
    description="""
Returns a complete profile for the authenticated student.

**Includes:**
- Personal and academic info.
- Attendance summary: total_absences, attendance_rate.
- Per-module attendance summary: module_attendance[].
- Full absence history.

**Auth:** Student only.
""",
    responses={
        200: {"description": "Student profile fetched successfully"},
        404: {"description": "Student not found"},
    },
)
async def get_my_profile(
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    normalized_email = (current_user.email or "").strip().lower()
    academic = (
        await db.execute(
            select(AcademicStudent).where(
                func.lower(AcademicStudent.email) == normalized_email
            )
        )
    ).scalar_one_or_none()
    if academic is not None:
        return await _get_student_profile_logic(cast(str, academic.matricule), db)

    if current_user.student_id:
        return await _get_student_profile_from_account(current_user, db)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            "Student profile not found for account "
            f"email '{current_user.email}' and student_id '{current_user.student_id}'."
        ),
    )


@router.get(
    "/students/{matricule}",
    response_model=StudentProfileOut,
    summary="Full student profile with absence history (Admin)",
    description="""
Returns a complete profile for a single student identified by their **matricule**.

**Includes:**
- Personal info (name, email, phone, avatar)
- Academic info (year, group, filière, status)
- Account info (avatar, creation date, last login) — merged from `student_users` if a linked account exists
- Attendance summary: `total_absences`, `total_sessions`, `attendance_rate` (%)
- Per-module attendance summary: `module_attendance[]` with `absences` for each module
- Full absence history: per-session records with date, module, teacher, justification status

**Input:**
- Path parameter `matricule` (string), example: `232332029109`

**Output:**
- Full student profile object including:
  - student identity and academic data
  - global attendance KPIs
  - `module_attendance` with per-module absence counters
  - `absence_history` with `is_own_group` and `is_cross_session`

**Auth:** Admin only.
""",
    responses={
        200: {"description": "Student profile fetched successfully"},
        404: {"description": "Student not found"},
    },
)
async def get_student_profile(
    matricule: str,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await _get_student_profile_logic(matricule, db)


# ── PATCH /students/{id}/status ────────────────────────────────────────────────

@router.patch(
    "/students/{student_id}/status",
    response_model=AcademicStudentStatusOut,
    summary="Update academic student status (Admin only) — Feature 3",
    description="""
Updates the academic status of a student record.

**Allowed values:** `normal`, `exclu`, `bloque`, `abandonné`

Returns **404** if the student does not exist.

**Auth:** Admin only.
""",
)
async def update_student_status(
    student_id: UUID,
    data: StudentStatusUpdate,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    student = (
        await db.execute(
            select(AcademicStudent).where(AcademicStudent.id == student_id)
        )
    ).scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id '{student_id}' not found.",
        )

    student.status = data.status  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]
    db.add(student)
    await db.flush()
    await db.refresh(student)
    return student
