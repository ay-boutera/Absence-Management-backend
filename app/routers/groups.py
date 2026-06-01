"""
routers/groups.py — Teacher Group Endpoints
=============================================

GET  /api/v1/teachers/my-groups                              Teacher's groups with stats.
GET  /api/v1/teachers/my-groups/{group_name}/students        Students + cumulative attendance.
GET  /api/v1/teachers/my-groups/{group_name}/students/{matricule}/history
                                                              Full absence timeline for a student.
"""

from __future__ import annotations

import logging
from datetime import date as date_type, time as time_type
from typing import Optional
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import UserRole
from app.db import get_db
from app.helpers.absence import UNEXCUSED_ABSENCE
from app.helpers.permissions import require_role, require_active_user
from app.models import (
    Absence,
    Module,
    PlanningSession,
    Session,
    Teacher,
)
from app.models.student import AcademicStudent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Teacher Groups"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class GroupSummary(BaseModel):
    group_name: str
    year: str | None = None
    section: str | None = None
    speciality: str | None = None
    subjects: list[str]
    student_count: int
    total_sessions: int
    total_absences: int

    class Config:
        from_attributes = True


class GroupListResponse(BaseModel):
    total: int
    groups: list[GroupSummary]


class StudentGroupAttendance(BaseModel):
    student_id: UUID
    matricule: str
    nom: str
    prenom: str
    email: str
    total_sessions: int
    sessions_present: int
    sessions_absent: int
    attendance_rate: float  # 0.0 – 100.0


class GroupStudentsResponse(BaseModel):
    group_name: str
    year: str | None = None
    total_students: int
    total_sessions: int
    students: list[StudentGroupAttendance]


# ── Absence History Schemas ────────────────────────────────────────────────────

class SessionAbsenceRecord(BaseModel):
    """One row in the absence timeline."""
    session_id: UUID
    date: date_type
    start_time: str  # HH:MM
    end_time: str    # HH:MM
    subject: str
    session_type: str
    was_absent: bool
    was_present: bool
    not_recorded: bool  # True if teacher hasn't submitted attendance yet


class StudentAbsenceHistoryResponse(BaseModel):
    matricule: str
    nom: str
    prenom: str
    email: str
    group_name: str
    year: str | None = None
    total_sessions: int
    sessions_present: int
    sessions_absent: int
    sessions_not_recorded: int
    attendance_rate: float
    timeline: list[SessionAbsenceRecord]


# ── GET /teachers/my-groups ───────────────────────────────────────────────────

@router.get(
    "/teachers/my-groups",
    response_model=GroupListResponse,
    summary="Get all groups assigned to the current teacher",
    description="""
Returns a list of unique groups that the logged-in teacher is assigned to,
derived from their planning sessions. Each group includes:
- The academic year / section / speciality
- List of subjects taught in that group
- Number of students in the group
- Number of completed sessions
- Total absences recorded across all sessions

**Auth:** Teacher only (JWT).
""",
)
async def get_my_groups(
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    # 1. Get all planning sessions for this teacher
    planning_q = (
        select(PlanningSession)
        .options(selectinload(PlanningSession.teachers))
        .where(PlanningSession.teachers.any(Teacher.id == current_user.id))
    )
    planning_sessions = list((await db.execute(planning_q)).scalars().all())

    if not planning_sessions:
        return GroupListResponse(total=0, groups=[])

    # 2. Build unique groups: key = (group, year)
    group_map: dict[tuple[str, str | None], dict] = {}

    for ps in planning_sessions:
        if not ps.group:
            continue

        key = (ps.group, ps.year)
        if key not in group_map:
            group_map[key] = {
                "group_name": ps.group,
                "year": ps.year,
                "section": ps.section,
                "speciality": ps.speciality,
                "subjects": set(),
            }
        group_map[key]["subjects"].add(ps.subject)

    if not group_map:
        return GroupListResponse(total=0, groups=[])

    # 3. For each group, count students + sessions + absences
    result_groups: list[GroupSummary] = []

    for (group_name, year), info in group_map.items():
        # Count students
        student_filters = [func.lower(AcademicStudent.groupe) == group_name.lower()]
        if year:
            student_filters.append(func.lower(AcademicStudent.niveau) == year.lower())

        student_count_result = await db.execute(
            select(func.count()).select_from(AcademicStudent).where(and_(*student_filters))
        )
        student_count = student_count_result.scalar() or 0

        # Count sessions for this teacher + group
        session_filters = [
            Session.teacher_id == current_user.id,
            Session.group == group_name,
        ]
        if year:
            session_filters.append(Session.year == year)

        session_count_result = await db.execute(
            select(func.count()).select_from(Session).where(and_(*session_filters))
        )
        total_sessions = session_count_result.scalar() or 0

        # Count unexcused absences across all sessions for this group
        absence_count = 0
        if total_sessions > 0:
            session_ids_q = select(Session.id).where(and_(*session_filters))
            absence_result = await db.execute(
                select(func.count())
                .select_from(Absence)
                .where(
                    and_(
                        Absence.session_id.in_(session_ids_q),
                        UNEXCUSED_ABSENCE,
                    )
                )
            )
            absence_count = absence_result.scalar() or 0

        result_groups.append(
            GroupSummary(
                group_name=group_name,
                year=year,
                section=info["section"],
                speciality=info["speciality"],
                subjects=sorted(info["subjects"]),
                student_count=student_count,
                total_sessions=total_sessions,
                total_absences=absence_count,
            )
        )

    result_groups.sort(key=lambda g: (g.year or "", g.group_name))

    return GroupListResponse(total=len(result_groups), groups=result_groups)


# ── GET /teachers/my-groups/{group_name}/students ─────────────────────────────

@router.get(
    "/teachers/my-groups/{group_name}/students",
    response_model=GroupStudentsResponse,
    summary="Get all students in a group with cumulative attendance",
    description="""
Returns all students belonging to a specific group, along with their
**cumulative attendance** across **all** sessions the teacher has held
for this group.

Each student record includes:
- Total sessions held for this group
- Number of sessions present / absent
- Attendance rate (percentage)

Use `year` query param to filter by academic year (e.g. `1CS`).

**Auth:** Teacher only (JWT).
""",
)
async def get_group_students(
    group_name: str,
    year: Optional[str] = Query(default=None, description="Academic year filter, e.g. 1CS"),
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    # 1. Get all expected base sessions for this group
    session_filters = [
        Session.teacher_id == current_user.id,
        Session.group == group_name,
    ]
    if year:
        session_filters.append(Session.year == year)

    base_sessions_result = await db.execute(
        select(Session.id, Session.module_id).where(and_(*session_filters))
    )
    base_sessions = base_sessions_result.all()
    base_session_ids = [row.id for row in base_sessions]
    base_module_ids = list({row.module_id for row in base_sessions})
    total_sessions = len(base_session_ids)

    # 2. Get all students in this group
    student_filters = [func.lower(AcademicStudent.groupe) == group_name.lower()]
    if year:
        student_filters.append(func.lower(AcademicStudent.niveau) == year.lower())

    students_result = await db.execute(
        select(AcademicStudent).where(and_(*student_filters)).order_by(
            AcademicStudent.nom, AcademicStudent.prenom
        )
    )
    students = list(students_result.scalars().all())

    if not students:
        return GroupStudentsResponse(
            group_name=group_name,
            year=year,
            total_students=0,
            total_sessions=total_sessions,
            students=[],
        )

    # 3. Find ALL eligible sessions (from any group) for this teacher + modules
    # A student is considered "Present" if they attended ANY session for that module by this teacher.
    any_session_result = await db.execute(
        select(Session.id).where(
            and_(
                Session.teacher_id == current_user.id,
                Session.module_id.in_(base_module_ids) if base_module_ids else False
            )
        )
    )
    all_eligible_session_ids = list(any_session_result.scalars().all())

    # 4. Get total PRESENCE counts for these students across ALL eligible sessions
    presence_counts: dict[str, int] = {}
    if all_eligible_session_ids:
        presence_rows = (
            await db.execute(
                select(
                    Absence.student_matricule,
                    func.count().label("cnt"),
                )
                .where(
                    and_(
                        Absence.session_id.in_(all_eligible_session_ids),
                        Absence.is_absent.is_(False),
                    )
                )
                .group_by(Absence.student_matricule)
            )
        ).all()
        presence_counts = {row.student_matricule: row.cnt for row in presence_rows}

    # 5. Build response
    records: list[StudentGroupAttendance] = []
    for student in students:
        # Limit presence to the max number of expected sessions
        present = min(presence_counts.get(student.matricule, 0), total_sessions)
        absent = max(total_sessions - present, 0)
        rate = (present / total_sessions * 100.0) if total_sessions > 0 else 0.0

        records.append(
            StudentGroupAttendance(
                student_id=student.id,
                matricule=student.matricule,
                nom=student.nom,
                prenom=student.prenom,
                email=student.email,
                total_sessions=total_sessions,
                sessions_present=present,
                sessions_absent=absent,
                attendance_rate=round(rate, 1),
            )
        )

    return GroupStudentsResponse(
        group_name=group_name,
        year=year,
        total_students=len(records),
        total_sessions=total_sessions,
        students=records,
    )


# ── GET /teachers/my-groups/{group_name}/students/{matricule}/history ─────────

@router.get(
    "/teachers/my-groups/{group_name}/students/{matricule}/history",
    response_model=StudentAbsenceHistoryResponse,
    summary="Get full absence history for a student in a group",
    description="""
Returns the full timeline of absences for a specific student in a specific group.
Each record in the timeline represents a session and indicates if the student
was marked present, absent, or if attendance was not recorded yet.

**Auth:** Teacher only (JWT).
""",
)
async def get_student_absence_history(
    group_name: str,
    matricule: str,
    year: Optional[str] = Query(default=None, description="Academic year filter, e.g. 1CS"),
    current_user=Depends(require_role(UserRole.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    # 1. Verify student exists in this group
    student_filters = [
        AcademicStudent.matricule == matricule,
        func.lower(AcademicStudent.groupe) == group_name.lower(),
    ]
    if year:
        student_filters.append(func.lower(AcademicStudent.niveau) == year.lower())

    student = (
        await db.execute(select(AcademicStudent).where(and_(*student_filters)))
    ).scalar_one_or_none()

    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Étudiant '{matricule}' introuvable dans le groupe '{group_name}'.",
        )

    # 2. Get all sessions for this teacher + group
    session_filters = [
        Session.teacher_id == current_user.id,
        Session.group == group_name,
    ]
    if year:
        session_filters.append(Session.year == year)

    sessions_result = await db.execute(
        select(Session)
        .options(selectinload(Session.module))
        .where(and_(*session_filters))
        .order_by(Session.date.desc(), Session.start_time.desc())
    )
    base_sessions = list(sessions_result.scalars().all())
    base_session_ids = [s.id for s in base_sessions]
    base_module_ids = list({s.module_id for s in base_sessions})
    total_sessions = len(base_sessions)

    # 3. Get all absence records for this student in these base sessions
    absences_by_session: dict[UUID, Absence] = {}
    if base_session_ids:
        absences_result = await db.execute(
            select(Absence).where(
                and_(
                    Absence.student_matricule == matricule,
                    Absence.session_id.in_(base_session_ids),
                )
            )
        )
        for absence in absences_result.scalars().all():
            absences_by_session[absence.session_id] = absence

    # 4. Get compensatory presences (sessions from other groups with SAME modulo by SAME teacher where student was marked present)
    compensatory_absences_result = await db.execute(
        select(Absence).join(Session, Absence.session_id == Session.id)
        .options(selectinload(Absence.session).selectinload(Session.module))
        .where(
            and_(
                Absence.student_matricule == matricule,
                Absence.session_id.notin_(base_session_ids) if base_session_ids else True,
                Session.teacher_id == current_user.id,
                Session.module_id.in_(base_module_ids) if base_module_ids else False,
                Absence.is_absent == False
            )
        )
        .order_by(Session.date.desc(), Session.start_time.desc())
    )
    compensatory_absences = list(compensatory_absences_result.scalars().all())

    # 5. Build timeline using base_sessions PLUS compensatory sessions
    timeline: list[SessionAbsenceRecord] = []
    sessions_present = 0
    sessions_not_recorded = 0

    for s in base_sessions:
        absence = absences_by_session.get(s.id)
        
        not_recorded = absence is None
        was_absent = False
        was_present = False

        if not_recorded:
            sessions_not_recorded += 1
        elif absence.is_absent:
            was_absent = True
        else:
            was_present = True
            sessions_present += 1

        timeline.append(
            SessionAbsenceRecord(
                session_id=s.id,
                date=s.date,
                start_time=s.start_time.strftime("%H:%M"),
                end_time=s.end_time.strftime("%H:%M"),
                subject=s.module.nom if s.module else "Inconnu",
                session_type=str(s.type.value) if hasattr(s.type, "value") else str(s.type),
                was_absent=was_absent,
                was_present=was_present,
                not_recorded=not_recorded,
            )
        )

    # ADD compensatory sessions to timeline
    for comp_absence in compensatory_absences:
        comp_s = comp_absence.session
        sessions_present += 1
        timeline.append(
            SessionAbsenceRecord(
                session_id=comp_s.id,
                date=comp_s.date,
                start_time=comp_s.start_time.strftime("%H:%M"),
                end_time=comp_s.end_time.strftime("%H:%M"),
                subject=comp_s.module.nom if comp_s.module else "Inconnu",
                session_type=f"{comp_s.type.value if hasattr(comp_s.type, 'value') else comp_s.type} (avec {comp_s.group})",
                was_absent=False,
                was_present=True,
                not_recorded=False,
            )
        )
    
    # Sort the full timeline purely by date/time
    timeline.sort(key=lambda r: (r.date, r.start_time), reverse=True)

    # Calculate bounded totals
    sessions_present_bounded = min(sessions_present, total_sessions)
    sessions_absent_bounded = max(total_sessions - sessions_present_bounded, 0)
    rate = (sessions_present_bounded / total_sessions * 100.0) if total_sessions > 0 else 0.0

    return StudentAbsenceHistoryResponse(
        matricule=student.matricule,
        nom=student.nom,
        prenom=student.prenom,
        email=student.email,
        group_name=group_name,
        year=year,
        total_sessions=total_sessions,
        sessions_present=sessions_present_bounded,
        sessions_absent=sessions_absent_bounded,
        sessions_not_recorded=sessions_not_recorded,
        attendance_rate=round(rate, 1),
        timeline=timeline,
    )


# ── GET /groups ─────────────────────────────────────────────────────────────

class GroupGlobalItem(BaseModel):
    group_id: str
    year: str | None = None
    section: str | None = None
    group_name: str
    speciality: str | None = None
    student_count: int
    absence_rate: float

class GroupGlobalResponse(BaseModel):
    total: int
    items: list[GroupGlobalItem]

@router.get(
    "/groups",
    response_model=GroupGlobalResponse,
    summary="Get all groups optionally filtered by criteria",
    description="""
Returns all groups mapped globally, not just for a single teacher.
Filter by year, section, speciality.

**Auth:** Active User (JWT).
""",
)
async def get_all_groups(
    year: Optional[str] = Query(None, description="Filter by year (e.g. 1CP)"),
    section: Optional[str] = Query(None, description="Filter by section (e.g. A)"),
    speciality: Optional[str] = Query(None, description="Filter by speciality"),
    current_user=Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Get unique groups
    planning_q = select(
        PlanningSession.year,
        PlanningSession.section,
        PlanningSession.speciality,
        PlanningSession.group
    ).where(PlanningSession.group.isnot(None))

    if year:
        planning_q = planning_q.where(PlanningSession.year == year)
    if section:
        planning_q = planning_q.where(PlanningSession.section == section)
    if speciality:
        planning_q = planning_q.where(PlanningSession.speciality == speciality)

    planning_q = planning_q.distinct()
    planning_rows = await db.execute(planning_q)

    # 2. Extract into a list of tuples
    unique_groups = []
    for row in planning_rows.all():
        row_year = row.year.value if hasattr(row.year, "value") else str(row.year) if row.year else None
        row_section = row.section.value if hasattr(row.section, "value") else str(row.section) if row.section else None
        row_spec = row.speciality.value if hasattr(row.speciality, "value") else str(row.speciality) if row.speciality else None
        
        unique_groups.append({
            "year": row_year,
            "section": row_section,
            "speciality": row_spec,
            "group_name": row.group
        })

    # Optional: If section was not provided and we want groups from AcademicStudent that might not have sessions yet
    if not section and not unique_groups:
        student_q = select(
            AcademicStudent.niveau,
            AcademicStudent.filiere,
            AcademicStudent.groupe
        ).where(AcademicStudent.groupe.isnot(None))
        if year:
            student_q = student_q.where(AcademicStudent.niveau == year)
        if speciality:
            student_q = student_q.where(AcademicStudent.filiere == speciality)
        student_q = student_q.distinct()
        student_rows = await db.execute(student_q)
        for row in student_rows.all():
            unique_groups.append({
                "year": row.niveau,
                "section": None,
                "speciality": row.filiere,
                "group_name": row.groupe
            })

    # Sort to be nice
    unique_groups.sort(key=lambda x: (x["year"] or "", x["section"] or "", x["group_name"] or ""))

    items = []
    for info in unique_groups:
        student_filters = []
        if info["group_name"]:
            student_filters.append(func.lower(AcademicStudent.groupe) == info["group_name"].lower())
        else:
            student_filters.append(AcademicStudent.groupe.is_(None))
        if info["year"]:
            student_filters.append(func.lower(AcademicStudent.niveau) == info["year"].lower())
        if info["speciality"]:
            student_filters.append(func.lower(AcademicStudent.filiere) == info["speciality"].lower())
            
        student_count = (await db.execute(select(func.count()).select_from(AcademicStudent).where(and_(*student_filters)))).scalar() or 0
        
        session_filters = [Session.group == info["group_name"]]
        if info["year"]:
            session_filters.append(Session.year == info["year"])
        if info["section"]:
            session_filters.append(Session.section == info["section"])
            
        sessions_ids = (await db.execute(select(Session.id).where(and_(*session_filters)))).scalars().all()
        
        absence_rate = 0.0
        if sessions_ids and student_count > 0:
            total_absences = (await db.execute(
                select(func.count()).select_from(Absence)
                .where(and_(Absence.session_id.in_(sessions_ids), UNEXCUSED_ABSENCE))
            )).scalar() or 0
            
            rate = (total_absences / (student_count * len(sessions_ids))) * 100.0
            absence_rate = round(rate, 2)

        group_id_str = f"group-{info['year']}-{info['section']}-{info['group_name']}"
        group_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, group_id_str))

        items.append(
            GroupGlobalItem(
                group_id=group_uuid,
                year=info["year"],
                section=info["section"],
                group_name=info["group_name"],
                speciality=info["speciality"],
                student_count=student_count,
                absence_rate=absence_rate
            )
        )

    return GroupGlobalResponse(
        total=len(items),
        items=items
    )
