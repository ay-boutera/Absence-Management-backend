"""
routers/students.py — Academic Student Endpoints
=================================================

GET   /api/v1/students                       Admin: paginated student list with absence counts.
GET   /api/v1/students/{matricule}           Admin: full student profile with absence history.
PATCH /api/v1/students/{student_id}/status   Admin: update a student's academic status (Feature 3).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.models import Absence, Session, Module, Teacher
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


# ── GET /students ──────────────────────────────────────────────────────────────
@router.get(
    "/students",
    response_model=list[StudentAttendanceListOut],
    summary="List all students with absence counts (Admin attendance list)",
    description="""
Returns a list of **all** academic students together with each
student's total number of absences (`total_absences`).

**Query parameters (all optional):**
- `search` — search by name, matricule, or email
- `year` — filter by year / level (e.g. "CP1", "CS1")
- `group` — filter by group (e.g. "A1", "B2")
- `status` — filter by student status ("normal", "exclu", "bloque", "abandonné")
- `sort_by` — field to sort on: `nom`, `matricule`, `total_absences` (default: `nom`)
- `sort_order` — `asc` or `desc` (default: `asc`)

**Auth:** Admin only.
""",
)
async def list_students_with_absences(
    search: Optional[str] = Query(default=None, description="Search by name, matricule, or email"),
    year: Optional[str] = Query(default=None, description="Filter by year/level"),
    group: Optional[str] = Query(default=None, description="Filter by group"),
    status_query: Optional[str] = Query(default=None, alias="status", description="Filter by student status"),
    sort_by: Optional[str] = Query(default="nom", description="Sort field: nom, matricule, total_absences"),
    sort_order: Optional[str] = Query(default="asc", description="Sort order: asc or desc"),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    # Sub-query: count absences per student
    absence_count_sq = (
        select(
            Absence.student_matricule,
            func.count(Absence.id).label("total_absences"),
        )
        .where(Absence.is_absent.is_(True))
        .group_by(Absence.student_matricule)
        .subquery()
    )

    # Main query: join students with absence counts and auth user info
    base_q = (
        select(
            AcademicStudent,
            func.coalesce(absence_count_sq.c.total_absences, 0).label("total_absences"),
            Student.avatar_url,
            Student.is_active,
        )
        .outerjoin(
            absence_count_sq,
            AcademicStudent.matricule == absence_count_sq.c.student_matricule,
        )
        .outerjoin(
            Student,
            AcademicStudent.email == Student.email,
        )
    )

    # ── Filters ────────────────────────────────────────────────────────────────
    filters = []
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                AcademicStudent.nom.ilike(like),
                AcademicStudent.prenom.ilike(like),
                AcademicStudent.matricule.ilike(like),
                AcademicStudent.email.ilike(like),
            )
        )
    if year:
        filters.append(AcademicStudent.niveau == year)
    if group:
        filters.append(AcademicStudent.groupe == group)
    if status_query:
        filters.append(AcademicStudent.status == status_query)

    if filters:
        base_q = base_q.where(and_(*filters))

    # ── Sorting ────────────────────────────────────────────────────────────────
    sort_columns = {
        "nom": AcademicStudent.nom,
        "matricule": AcademicStudent.matricule,
        "total_absences": func.coalesce(absence_count_sq.c.total_absences, 0),
    }
    sort_col = sort_columns.get(sort_by, AcademicStudent.nom)
    if sort_order == "desc":
        sort_col = sort_col.desc()
    else:
        sort_col = sort_col.asc()

    base_q = base_q.order_by(sort_col, AcademicStudent.prenom)

    rows = (await db.execute(base_q)).all()

    items = [
        StudentAttendanceListOut(
            student_id=student.matricule,
            full_name=f"{student.nom} {student.prenom}",
            email=student.email,
            avatar_url=avatar_url,
            level=student.niveau,
            group=student.groupe,
            total_absences=total_absences,
            status=student.status,
            is_active=bool(is_active),
        )
        for student, total_absences, avatar_url, is_active in rows
    ]

    return items

# ── GET /students/{matricule} ──────────────────────────────────────────────────
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
        200: {
            "description": "Student profile fetched successfully",
            "content": {
                "application/json": {
                    "example": {
                        "id": "e4c5f24b-1bfa-45fd-9b9c-630ba72e54aa",
                        "matricule": "232332029109",
                        "nom": "ABADELIA",
                        "prenom": "Mohammed imad eddine",
                        "email": "mie.abadelia@esi-sba.dz",
                        "filiere": "CS",
                        "niveau": "1CS",
                        "groupe": "G3",
                        "status": "normal",
                        "avatar_url": None,
                        "phone": None,
                        "is_active": True,
                        "created_at": "2026-05-25T13:24:36.016766Z",
                        "last_activity": None,
                        "total_absences": 0,
                        "total_sessions": 1,
                        "attendance_rate": 100.0,
                        "cross_session_count": 0,
                        "module_attendance": [
                            {
                                "module_name": "Archi",
                                "total_sessions": 1,
                                "absences": 0,
                                "attendance_rate": 100.0,
                            },
                            {
                                "module_name": "ACSI",
                                "total_sessions": 0,
                                "absences": 0,
                                "attendance_rate": None,
                            },
                        ],
                        "absence_history": [
                            {
                                "absence_id": "b8091a68-ad52-4f3e-871e-8077cdc993aa",
                                "session_id": "a05509e4-290d-416f-9311-100ad3fc11cc",
                                "date": "2026-05-20T00:00:00",
                                "start_time": "08:00:00",
                                "end_time": "15:00:00",
                                "module_name": "Archi",
                                "teacher_name": "TRARI NOUR ELFOUAD",
                                "is_absent": False,
                                "justification_status": None,
                                "session_group": "G3",
                                "is_own_group": True,
                                "is_cross_session": False,
                            }
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Student not found",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Student with matricule '232332029109' not found."
                    }
                }
            },
        },
    },
)
async def get_student_profile(
    matricule: str,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
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
                justification_status=absence.statut_justificatif,
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

    # ── 7. Compute per-module attendance (own group sessions only) ────────────
    module_rows = (await db.execute(select(Module).order_by(Module.nom.asc()))).scalars().all()
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
                module_name=module.nom,
                total_sessions=module_total_sessions,
                absences=module_absences,
                attendance_rate=module_rate,
            )
        )

    return StudentProfileOut(
        id=academic.id,
        matricule=academic.matricule,
        nom=academic.nom,
        prenom=academic.prenom,
        email=academic.email,
        filiere=academic.filiere,
        niveau=academic.niveau,
        groupe=academic.groupe,
        status=academic.status,
        # From auth account (if linked)
        avatar_url=auth_account.avatar_url if auth_account else None,
        phone=auth_account.phone if auth_account else None,
        is_active=auth_account.is_active if auth_account else None,
        created_at=auth_account.created_at if auth_account else academic.created_at,
        last_activity=auth_account.last_activity if auth_account else None,
        # Attendance (own group only)
        total_absences=total_absences,
        total_sessions=total_sessions,
        attendance_rate=attendance_rate,
        cross_session_count=cross_session_count,
        module_attendance=module_attendance,
        absence_history=history,
    )


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

    student.status = data.status
    db.add(student)
    await db.flush()
    await db.refresh(student)
    return student
