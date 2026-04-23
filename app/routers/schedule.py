"""
routers/schedule.py — Weekly Timetable Endpoint
================================================

GET /api/v1/planning/my-schedule   Returns the weekly schedule for the authenticated user (teacher, student, admin).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.helpers.permissions import get_current_user_bearer
from app.models import PlanningSession, Teacher, UserRole
from app.models.student import Student as StudentUser
from app.schemas.planning import PlanningSessionOut, ScheduleResponse, TeacherInfo

router = APIRouter(tags=["Schedule"])

YEARS_WITH_SPECIALITY = {"2CS", "3CS"}


def _fmt_time(t) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


@router.get(
    "/planning/my-schedule",
    response_model=ScheduleResponse,
    summary="Get my weekly schedule",
    description="""
Returns the planning (weekly template) sessions for the authenticated user.

- **Teacher**: sessions where the teacher is assigned.
- **Student**: sessions matching year, section, speciality, and group.
- **Admin**: all sessions.

**Optional filters:** `semester` (S1|S2), `day`
""",
)
async def my_schedule(
    semester: Optional[str] = Query(default=None, pattern="^(S1|S2)$"),
    day: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.role

    base_q = select(PlanningSession).options(selectinload(PlanningSession.teachers))

    if role == UserRole.TEACHER:
        teacher = (await db.execute(select(Teacher).where(Teacher.id == current_user.id))).scalar_one_or_none()
        if teacher is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil enseignant introuvable.")
        base_q = base_q.where(PlanningSession.teachers.any(Teacher.id == teacher.id))

    elif role == UserRole.STUDENT:
        student = (await db.execute(select(StudentUser).where(StudentUser.id == current_user.id))).scalar_one_or_none()
        if student is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil étudiant introuvable.")

        student_year = student.level
        student_group = student.group
        student_speciality: Optional[str] = None
        student_section: Optional[str] = None

        if student_year in YEARS_WITH_SPECIALITY:
            student_speciality = student.program or None
        else:
            student_section = student.program if student.program else None

        filters = [PlanningSession.year == student_year]

        if student_section is not None:
            filters.append(or_(PlanningSession.section == student_section, PlanningSession.section.is_(None)))
        if student_speciality is not None:
            filters.append(or_(PlanningSession.speciality == student_speciality, PlanningSession.speciality.is_(None)))

        if student_group:
            filters.append(or_(PlanningSession.group == student_group, PlanningSession.group.is_(None)))
        else:
            filters.append(PlanningSession.group.is_(None))

        base_q = base_q.where(and_(*filters))

    if semester:
        base_q = base_q.where(PlanningSession.semester == semester)
    if day:
        base_q = base_q.where(PlanningSession.day == day)

    base_q = base_q.order_by(PlanningSession.semester, PlanningSession.time_start)

    sessions = list((await db.execute(base_q)).scalars().all())

    def _serialize(s: PlanningSession) -> PlanningSessionOut:
        return PlanningSessionOut(
            id=s.id,
            day=s.day,
            time_start=_fmt_time(s.time_start),
            time_end=_fmt_time(s.time_end),
            type=s.type,
            subject=s.subject,
            room=s.room,
            group=s.group,
            year=s.year,
            section=s.section,
            speciality=s.speciality,
            semester=s.semester,
            teachers=[
                TeacherInfo(id=t.id, employee_id=t.employee_id, first_name=t.first_name, last_name=t.last_name)
                for t in s.teachers
            ],
        )

    return ScheduleResponse(total=len(sessions), sessions=[_serialize(s) for s in sessions])
