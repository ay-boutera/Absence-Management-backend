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
    PaginatedStudentAttendanceList,
    StudentAttendanceListOut,
    StudentProfileOut,
    StudentStatusUpdate,
)

router = APIRouter(tags=["Students"])


# ── GET /students ──────────────────────────────────────────────────────────────
@router.get(
    "/students",
    response_model=PaginatedStudentAttendanceList,
    summary="List all students with absence counts (Admin attendance list)",
    description="""
Returns a paginated list of **all** academic students together with each
student's total number of absences (`absences_count`).

**Query parameters (all optional):**
- `q` — search by name, matricule, or email (case-insensitive partial match)
- `niveau` — filter by year / level (e.g. "CP1", "CS1")
- `groupe` — filter by group (e.g. "A1", "B2")
- `status` — filter by student status ("normal", "exclu", "bloque", "abandonné")
- `sort_by` — field to sort on: `nom`, `matricule`, `absences_count` (default: `nom`)
- `sort_order` — `asc` or `desc` (default: `asc`)
- `page` — page number (default: 1)
- `page_size` — items per page (default: 20, max: 100)

**Auth:** Admin only.
""",
)
async def list_students_with_absences(
    q: Optional[str] = Query(default=None, description="Search by name, matricule, or email"),
    niveau: Optional[str] = Query(default=None, description="Filter by year/level"),
    groupe: Optional[str] = Query(default=None, description="Filter by group"),
    student_status: Optional[str] = Query(default=None, alias="status", description="Filter by student status"),
    sort_by: Optional[str] = Query(default="nom", description="Sort field: nom, matricule, absences_count"),
    sort_order: Optional[str] = Query(default="asc", description="Sort order: asc or desc"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    # Sub-query: count absences per student
    absence_count_sq = (
        select(
            Absence.student_matricule,
            func.count(Absence.id).label("absences_count"),
        )
        .where(Absence.is_absent.is_(True))
        .group_by(Absence.student_matricule)
        .subquery()
    )

    # Main query: join students with absence counts
    base_q = (
        select(
            AcademicStudent,
            func.coalesce(absence_count_sq.c.absences_count, 0).label("absences_count"),
        )
        .outerjoin(
            absence_count_sq,
            AcademicStudent.matricule == absence_count_sq.c.student_matricule,
        )
    )

    # ── Filters ────────────────────────────────────────────────────────────────
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                AcademicStudent.nom.ilike(like),
                AcademicStudent.prenom.ilike(like),
                AcademicStudent.matricule.ilike(like),
                AcademicStudent.email.ilike(like),
            )
        )
    if niveau:
        filters.append(AcademicStudent.niveau == niveau)
    if groupe:
        filters.append(AcademicStudent.groupe == groupe)
    if student_status:
        filters.append(AcademicStudent.status == student_status)

    if filters:
        base_q = base_q.where(and_(*filters))

    # ── Total count ────────────────────────────────────────────────────────────
    count_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # ── Sorting ────────────────────────────────────────────────────────────────
    sort_columns = {
        "nom": AcademicStudent.nom,
        "matricule": AcademicStudent.matricule,
        "absences_count": func.coalesce(absence_count_sq.c.absences_count, 0),
    }
    sort_col = sort_columns.get(sort_by, AcademicStudent.nom)
    if sort_order == "desc":
        sort_col = sort_col.desc()
    else:
        sort_col = sort_col.asc()

    base_q = base_q.order_by(sort_col, AcademicStudent.prenom)

    # ── Pagination ─────────────────────────────────────────────────────────────
    base_q = base_q.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(base_q)).all()

    items = [
        StudentAttendanceListOut(
            id=student.id,
            matricule=student.matricule,
            nom=student.nom,
            prenom=student.prenom,
            email=student.email,
            filiere=student.filiere,
            niveau=student.niveau,
            groupe=student.groupe,
            status=student.status,
            absences_count=absences_count,
        )
        for student, absences_count in rows
    ]

    return PaginatedStudentAttendanceList(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


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
- Full absence history: per-session records with date, module, teacher, justification status

**Auth:** Admin only.
""",
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
            )
        )

    # ── 6. Compute attendance rate (own group sessions only) ───────────────────
    if total_sessions > 0:
        attendance_rate = round((1 - total_absences / total_sessions) * 100, 1)
    else:
        attendance_rate = 100.0

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
