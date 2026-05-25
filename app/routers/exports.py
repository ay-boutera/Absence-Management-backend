from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, cast, exists, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.enums import JustificationScopeType, JustificationStatus
from app.db import get_db
from app.helpers.permissions import require_admin_or_teacher_bearer, require_role
from app.helpers.role_users import user_role
from app.models import (
    Absence,
    ImportExportLog,
    Session,
    UserRole,
)
from app.models.justification import Justification
from app.models.module import Module
from app.models.planning_session import PlanningSession
from app.models.salle import Salle
from app.models.student import AcademicStudent
from app.models.teacher import Teacher
from app.utils.export_utils import build_csv_response, build_pdf_response

router = APIRouter(tags=["exports"])


def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _format_time_range(start_time, end_time) -> str:
    return f"{start_time} - {end_time}"


def _teacher_name(first_name: Optional[str], last_name: Optional[str]) -> str:
    return f"{first_name or ''} {last_name or ''}".strip()


@router.get("/export/absences/csv", summary="Export absences as CSV")
async def export_absences_csv(
    year: Optional[str] = Query(default=None),
    semester: Optional[str] = Query(default=None),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    week: Optional[int] = Query(default=None, ge=1, le=53),
    day: Optional[date] = Query(default=None),
    module: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    student_id: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    filters_applied = {
        "year": year,
        "semester": semester,
        "month": month,
        "week": week,
        "day": day,
        "module": module,
        "group": group,
        "teacher_id": teacher_id,
        "student_id": student_id,
    }
    if all(value in (None, "") for value in filters_applied.values()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one filter is required to export absences",
        )

    stmt = (
        select(
            Absence.id,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.groupe,
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Absence.is_absent,
            func.bool_or(
                and_(
                    Justification.status == JustificationStatus.APPROVED,
                    or_(
                        and_(
                            Justification.scope_type == JustificationScopeType.ABSENCE,
                            Justification.absence_id == Absence.id,
                        ),
                        and_(
                            Justification.scope_type == JustificationScopeType.SESSION,
                            Justification.session_id == Absence.session_id,
                            Justification.student_id == AcademicStudent.id,
                        ),
                        and_(
                            Justification.scope_type == JustificationScopeType.RANGE,
                            Justification.student_id == AcademicStudent.id,
                            Justification.start_date <= Session.date,
                            Justification.end_date >= Session.date,
                        ),
                    ),
                )
            ).label("is_justified"),
        )
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .join(Teacher, Teacher.id == Session.teacher_id)
        .outerjoin(Justification, Justification.student_id == AcademicStudent.id)
        .group_by(
            Absence.id,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.groupe,
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Absence.is_absent,
        )
    )

    filters = []
    if year:
        filters.append(Session.year == year)
    if semester:
        filters.append(Session.semester == semester)
    if month is not None:
        filters.append(func.extract("month", Session.date) == month)
    if week is not None:
        filters.append(func.extract("week", Session.date) == week)
    if day is not None:
        filters.append(Session.date == day)
    if module:
        like = f"%{module}%"
        filters.append(or_(Module.nom.ilike(like), Module.code.ilike(like), cast(Module.id, String) == module))
    if group:
        filters.append(Session.group == group)
    if teacher_id:
        filters.append(
            or_(
                cast(Teacher.id, String) == teacher_id,
                Teacher.employee_id == teacher_id,
            )
        )
    if student_id:
        filters.append(
            or_(
                cast(AcademicStudent.id, String) == student_id,
                AcademicStudent.matricule == student_id,
            )
        )

    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Session.date.desc(), Session.start_time.desc()))).all()

    headers = [
        "Student Full Name",
        "Student Email",
        "Group",
        "Year",
        "Semester",
        "Module",
        "Session Type",
        "Session Date",
        "Session Time",
        "Teacher(s)",
        "Absence Status",
        "Justified",
    ]

    data_rows = [
        [
            f"{row[1]} {row[2]}",
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9].isoformat() if row[9] else "",
            _format_time_range(row[10], row[11]),
            _teacher_name(row[12], row[13]),
            "Absent" if row[14] else "Present",
            "Yes" if row[15] else "No",
        ]
        for row in rows
    ]

    return build_csv_response(headers, data_rows, f"absences_{_today_stamp()}")


@router.get("/export/absences/pdf", summary="Export absences as PDF")
async def export_absences_pdf(
    year: Optional[str] = Query(default=None),
    semester: Optional[str] = Query(default=None),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    week: Optional[int] = Query(default=None, ge=1, le=53),
    day: Optional[date] = Query(default=None),
    module: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    student_id: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    csv_response = await export_absences_csv(
        year=year,
        semester=semester,
        month=month,
        week=week,
        day=day,
        module=module,
        group=group,
        teacher_id=teacher_id,
        student_id=student_id,
        current_user=current_user,
        db=db,
    )

    # Re-run query to keep same source data but produce PDF rows
    # (avoid trying to decode StreamingResponse internals)
    filters_applied = {
        "Year": year,
        "Semester": semester,
        "Month": month,
        "Week": week,
        "Day": day.isoformat() if day else None,
        "Module": module,
        "Group": group,
        "Teacher": teacher_id,
        "Student": student_id,
    }

    stmt = (
        select(
            Absence.id,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.groupe,
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Absence.is_absent,
            func.bool_or(
                and_(
                    Justification.status == JustificationStatus.APPROVED,
                    or_(
                        and_(
                            Justification.scope_type == JustificationScopeType.ABSENCE,
                            Justification.absence_id == Absence.id,
                        ),
                        and_(
                            Justification.scope_type == JustificationScopeType.SESSION,
                            Justification.session_id == Absence.session_id,
                            Justification.student_id == AcademicStudent.id,
                        ),
                        and_(
                            Justification.scope_type == JustificationScopeType.RANGE,
                            Justification.student_id == AcademicStudent.id,
                            Justification.start_date <= Session.date,
                            Justification.end_date >= Session.date,
                        ),
                    ),
                )
            ).label("is_justified"),
        )
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .join(Teacher, Teacher.id == Session.teacher_id)
        .outerjoin(Justification, Justification.student_id == AcademicStudent.id)
        .group_by(
            Absence.id,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.groupe,
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Absence.is_absent,
        )
    )

    filters = []
    if year:
        filters.append(Session.year == year)
    if semester:
        filters.append(Session.semester == semester)
    if month is not None:
        filters.append(func.extract("month", Session.date) == month)
    if week is not None:
        filters.append(func.extract("week", Session.date) == week)
    if day is not None:
        filters.append(Session.date == day)
    if module:
        like = f"%{module}%"
        filters.append(or_(Module.nom.ilike(like), Module.code.ilike(like), cast(Module.id, String) == module))
    if group:
        filters.append(Session.group == group)
    if teacher_id:
        filters.append(or_(cast(Teacher.id, String) == teacher_id, Teacher.employee_id == teacher_id))
    if student_id:
        filters.append(or_(cast(AcademicStudent.id, String) == student_id, AcademicStudent.matricule == student_id))
    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Session.date.desc(), Session.start_time.desc()))).all()

    headers = [
        "Student Full Name",
        "Student Email",
        "Group",
        "Year",
        "Semester",
        "Module",
        "Session Type",
        "Session Date",
        "Session Time",
        "Teacher(s)",
        "Absence Status",
        "Justified",
    ]
    data_rows = [
        [
            f"{row[1]} {row[2]}",
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9].isoformat() if row[9] else "",
            _format_time_range(row[10], row[11]),
            _teacher_name(row[12], row[13]),
            "Absent" if row[14] else "Present",
            "Yes" if row[15] else "No",
        ]
        for row in rows
    ]

    filters_text = " | ".join([f"{k}: {v}" for k, v in filters_applied.items() if v not in (None, "")])

    return build_pdf_response(
        title="Absence Report — ESI-SBA",
        headers=headers,
        rows=data_rows,
        filename=f"absences_{_today_stamp()}",
        metadata={"Filters applied": filters_text or "None"},
    )


@router.get("/export/students/csv", summary="Export students as CSV")
async def export_students_csv(
    year: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    absences_count_sq = (
        select(Absence.student_matricule, func.count(Absence.id).label("total_absences"))
        .group_by(Absence.student_matricule)
        .subquery()
    )
    approved_justified_sq = (
        select(AcademicStudent.matricule.label("matricule"), func.count(Justification.id).label("justified_absences"))
        .join(Justification, Justification.student_id == AcademicStudent.id)
        .where(
            Justification.status == JustificationStatus.APPROVED,
            Justification.scope_type == JustificationScopeType.ABSENCE,
            Justification.absence_id.is_not(None),
        )
        .group_by(AcademicStudent.matricule)
        .subquery()
    )

    stmt = (
        select(
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.matricule,
            AcademicStudent.niveau,
            AcademicStudent.groupe,
            AcademicStudent.status,
            func.coalesce(absences_count_sq.c.total_absences, 0),
            func.coalesce(approved_justified_sq.c.justified_absences, 0),
        )
        .outerjoin(absences_count_sq, absences_count_sq.c.student_matricule == AcademicStudent.matricule)
        .outerjoin(approved_justified_sq, approved_justified_sq.c.matricule == AcademicStudent.matricule)
    )

    filters = []
    if year:
        filters.append(AcademicStudent.niveau == year)
    if group:
        filters.append(AcademicStudent.groupe == group)
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                AcademicStudent.nom.ilike(like),
                AcademicStudent.prenom.ilike(like),
                AcademicStudent.email.ilike(like),
            )
        )
    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(AcademicStudent.nom.asc(), AcademicStudent.prenom.asc()))).all()

    headers = [
        "Full Name",
        "Email",
        "Student ID",
        "Year",
        "Group",
        "Status",
        "Total Absences",
        "Justified Absences",
    ]
    data_rows = [
        [
            f"{row[0]} {row[1]}",
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
        ]
        for row in rows
    ]

    return build_csv_response(headers, data_rows, f"students_{_today_stamp()}")


@router.get("/export/students/pdf", summary="Export students as PDF")
async def export_students_pdf(
    year: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    absences_count_sq = (
        select(Absence.student_matricule, func.count(Absence.id).label("total_absences"))
        .group_by(Absence.student_matricule)
        .subquery()
    )
    approved_justified_sq = (
        select(AcademicStudent.matricule.label("matricule"), func.count(Justification.id).label("justified_absences"))
        .join(Justification, Justification.student_id == AcademicStudent.id)
        .where(
            Justification.status == JustificationStatus.APPROVED,
            Justification.scope_type == JustificationScopeType.ABSENCE,
            Justification.absence_id.is_not(None),
        )
        .group_by(AcademicStudent.matricule)
        .subquery()
    )

    stmt = (
        select(
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.email,
            AcademicStudent.matricule,
            AcademicStudent.niveau,
            AcademicStudent.groupe,
            AcademicStudent.status,
            func.coalesce(absences_count_sq.c.total_absences, 0),
            func.coalesce(approved_justified_sq.c.justified_absences, 0),
        )
        .outerjoin(absences_count_sq, absences_count_sq.c.student_matricule == AcademicStudent.matricule)
        .outerjoin(approved_justified_sq, approved_justified_sq.c.matricule == AcademicStudent.matricule)
    )

    filters = []
    if year:
        filters.append(AcademicStudent.niveau == year)
    if group:
        filters.append(AcademicStudent.groupe == group)
    if search:
        like = f"%{search}%"
        filters.append(or_(AcademicStudent.nom.ilike(like), AcademicStudent.prenom.ilike(like), AcademicStudent.email.ilike(like)))
    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(AcademicStudent.nom.asc(), AcademicStudent.prenom.asc()))).all()

    headers = [
        "Full Name",
        "Email",
        "Student ID",
        "Year",
        "Group",
        "Status",
        "Total Absences",
        "Justified Absences",
    ]
    data_rows = [[f"{r[0]} {r[1]}", r[2], r[3], r[4], r[5], r[6], r[7], r[8]] for r in rows]

    return build_pdf_response(
        title="Students Report — ESI-SBA",
        headers=headers,
        rows=data_rows,
        filename=f"students_{_today_stamp()}",
        metadata={
            "Filters": " | ".join(
                [
                    f"Year={year}" if year else "",
                    f"Group={group}" if group else "",
                    f"Search={search}" if search else "",
                ]
            ).strip(" |")
            or "None"
        },
    )


@router.get("/export/teachers/csv", summary="Export teachers as CSV")
async def export_teachers_csv(
    year: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    modules_sq = (
        select(
            Session.teacher_id.label("teacher_id"),
            func.string_agg(func.distinct(Module.nom), ", ").label("modules_taught"),
            func.count(Session.id).label("total_sessions"),
        )
        .join(Module, Module.id == Session.module_id)
        .group_by(Session.teacher_id)
        .subquery()
    )

    stmt = (
        select(
            Teacher.first_name,
            Teacher.last_name,
            Teacher.email,
            Teacher.employee_id,
            Teacher.specialization,
            func.coalesce(modules_sq.c.modules_taught, ""),
            func.coalesce(modules_sq.c.total_sessions, 0),
        )
        .outerjoin(modules_sq, modules_sq.c.teacher_id == Teacher.id)
    )

    filters = []
    if search:
        like = f"%{search}%"
        filters.append(or_(Teacher.first_name.ilike(like), Teacher.last_name.ilike(like), Teacher.email.ilike(like)))
    if year:
        filters.append(
            exists(
                select(1).where(Session.teacher_id == Teacher.id, Session.year == year)
            )
        )
    if module:
        like_m = f"%{module}%"
        filters.append(
            exists(
                select(1)
                .select_from(Session)
                .join(Module, Module.id == Session.module_id)
                .where(
                    Session.teacher_id == Teacher.id,
                    or_(Module.nom.ilike(like_m), Module.code.ilike(like_m), cast(Module.id, String) == module),
                )
            )
        )
    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Teacher.last_name.asc(), Teacher.first_name.asc()))).all()

    headers = ["Full Name", "Email", "Employee ID", "Grade/Rank", "Modules Taught", "Total Sessions"]
    data_rows = [[f"{r[0]} {r[1]}", r[2], r[3] or "", r[4] or "", r[5] or "", r[6]] for r in rows]
    return build_csv_response(headers, data_rows, f"teachers_{_today_stamp()}")


@router.get("/export/teachers/pdf", summary="Export teachers as PDF")
async def export_teachers_pdf(
    year: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    modules_sq = (
        select(
            Session.teacher_id.label("teacher_id"),
            func.string_agg(func.distinct(Module.nom), ", ").label("modules_taught"),
            func.count(Session.id).label("total_sessions"),
        )
        .join(Module, Module.id == Session.module_id)
        .group_by(Session.teacher_id)
        .subquery()
    )

    stmt = (
        select(
            Teacher.first_name,
            Teacher.last_name,
            Teacher.email,
            Teacher.employee_id,
            Teacher.specialization,
            func.coalesce(modules_sq.c.modules_taught, ""),
            func.coalesce(modules_sq.c.total_sessions, 0),
        )
        .outerjoin(modules_sq, modules_sq.c.teacher_id == Teacher.id)
    )

    filters = []
    if search:
        like = f"%{search}%"
        filters.append(or_(Teacher.first_name.ilike(like), Teacher.last_name.ilike(like), Teacher.email.ilike(like)))
    if year:
        filters.append(exists(select(1).where(Session.teacher_id == Teacher.id, Session.year == year)))
    if module:
        like_m = f"%{module}%"
        filters.append(
            exists(
                select(1)
                .select_from(Session)
                .join(Module, Module.id == Session.module_id)
                .where(
                    Session.teacher_id == Teacher.id,
                    or_(Module.nom.ilike(like_m), Module.code.ilike(like_m), cast(Module.id, String) == module),
                )
            )
        )
    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Teacher.last_name.asc(), Teacher.first_name.asc()))).all()

    headers = ["Full Name", "Email", "Employee ID", "Grade/Rank", "Modules Taught", "Total Sessions"]
    data_rows = [[f"{r[0]} {r[1]}", r[2], r[3] or "", r[4] or "", r[5] or "", r[6]] for r in rows]
    return build_pdf_response(
        title="Teachers Report — ESI-SBA",
        headers=headers,
        rows=data_rows,
        filename=f"teachers_{_today_stamp()}",
    )


@router.get("/export/planning/csv", summary="Export planning as CSV")
async def export_planning_csv(
    year: Optional[str] = Query(default=None),
    semester: Optional[str] = Query(default=None),
    week: Optional[int] = Query(default=None, ge=1, le=53),
    day: Optional[date] = Query(default=None),
    group: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    group_counts_sq = (
        select(
            AcademicStudent.niveau.label("year"),
            AcademicStudent.groupe.label("group"),
            func.count(AcademicStudent.id).label("total_students"),
        )
        .group_by(AcademicStudent.niveau, AcademicStudent.groupe)
        .subquery()
    )

    stmt = (
        select(
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.group,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Salle.code,
            func.coalesce(group_counts_sq.c.total_students, 0),
        )
        .join(Module, Module.id == Session.module_id)
        .join(Teacher, Teacher.id == Session.teacher_id)
        .outerjoin(Salle, Salle.id == Session.room_id)
        .outerjoin(
            group_counts_sq,
            and_(group_counts_sq.c.year == Session.year, group_counts_sq.c.group == Session.group),
        )
    )

    filters = []
    if year:
        filters.append(Session.year == year)
    if semester:
        filters.append(Session.semester == semester)
    if week is not None:
        filters.append(func.extract("week", Session.date) == week)
    if day is not None:
        filters.append(Session.date == day)
    if group:
        filters.append(Session.group == group)
    if teacher_id:
        filters.append(or_(cast(Teacher.id, String) == teacher_id, Teacher.employee_id == teacher_id))
    if module:
        like = f"%{module}%"
        filters.append(or_(Module.nom.ilike(like), Module.code.ilike(like), cast(Module.id, String) == module))

    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Session.date.asc(), Session.start_time.asc()))).all()

    headers = [
        "Year",
        "Semester",
        "Module",
        "Session Type",
        "Group",
        "Date",
        "Start Time",
        "End Time",
        "Teacher(s)",
        "Room",
        "Total Students",
    ]
    data_rows = [
        [
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5].isoformat() if r[5] else "",
            str(r[6]),
            str(r[7]),
            _teacher_name(r[8], r[9]),
            r[10] or "",
            r[11],
        ]
        for r in rows
    ]

    return build_csv_response(headers, data_rows, f"planning_{_today_stamp()}")


@router.get("/export/planning/pdf", summary="Export planning as PDF")
async def export_planning_pdf(
    year: Optional[str] = Query(default=None),
    semester: Optional[str] = Query(default=None),
    week: Optional[int] = Query(default=None, ge=1, le=53),
    day: Optional[date] = Query(default=None),
    group: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    group_counts_sq = (
        select(
            AcademicStudent.niveau.label("year"),
            AcademicStudent.groupe.label("group"),
            func.count(AcademicStudent.id).label("total_students"),
        )
        .group_by(AcademicStudent.niveau, AcademicStudent.groupe)
        .subquery()
    )

    stmt = (
        select(
            Session.year,
            Session.semester,
            Module.nom,
            Session.type,
            Session.group,
            Session.date,
            Session.start_time,
            Session.end_time,
            Teacher.first_name,
            Teacher.last_name,
            Salle.code,
            func.coalesce(group_counts_sq.c.total_students, 0),
        )
        .join(Module, Module.id == Session.module_id)
        .join(Teacher, Teacher.id == Session.teacher_id)
        .outerjoin(Salle, Salle.id == Session.room_id)
        .outerjoin(
            group_counts_sq,
            and_(group_counts_sq.c.year == Session.year, group_counts_sq.c.group == Session.group),
        )
    )

    filters = []
    if year:
        filters.append(Session.year == year)
    if semester:
        filters.append(Session.semester == semester)
    if week is not None:
        filters.append(func.extract("week", Session.date) == week)
    if day is not None:
        filters.append(Session.date == day)
    if group:
        filters.append(Session.group == group)
    if teacher_id:
        filters.append(or_(cast(Teacher.id, String) == teacher_id, Teacher.employee_id == teacher_id))
    if module:
        like = f"%{module}%"
        filters.append(or_(Module.nom.ilike(like), Module.code.ilike(like), cast(Module.id, String) == module))

    if filters:
        stmt = stmt.where(and_(*filters))

    rows = (await db.execute(stmt.order_by(Session.date.asc(), Session.start_time.asc()))).all()

    headers = [
        "Year",
        "Semester",
        "Module",
        "Session Type",
        "Group",
        "Date",
        "Start Time",
        "End Time",
        "Teacher(s)",
        "Room",
        "Total Students",
    ]
    data_rows = [
        [
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5].isoformat() if r[5] else "",
            str(r[6]),
            str(r[7]),
            _teacher_name(r[8], r[9]),
            r[10] or "",
            r[11],
        ]
        for r in rows
    ]

    return build_pdf_response(
        title="Planning Report — ESI-SBA",
        headers=headers,
        rows=data_rows,
        filename=f"planning_{_today_stamp()}",
    )


@router.get(
    "/import-export/history",
    summary="Get import/export audit history",
)
async def import_export_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_admin_or_teacher_bearer),
    db: AsyncSession = Depends(get_db),
):
    query = select(ImportExportLog).order_by(ImportExportLog.created_at.desc())

    if user_role(current_user) == UserRole.TEACHER:
        query = query.where(ImportExportLog.performed_by_id == current_user.id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(item.id),
                "performed_by_id": str(item.performed_by_id) if item.performed_by_id is not None else None,
                "action": item.action.value,
                "file_type": item.file_type.value,
                "file_name": item.file_name,
                "data_type": item.data_type.value,
                "row_count": item.row_count,
                "success_count": item.success_count,
                "error_count": item.error_count,
                "error_details": item.error_details,
                "created_at": item.created_at.isoformat() if item.created_at is not None else None,
            }
            for item in rows
        ],
    }
