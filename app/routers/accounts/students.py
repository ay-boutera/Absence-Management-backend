from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.helpers.role_users import list_users_by_role
from app.schemas import (
    StudentAccountCreate,
    StudentAccountResponse,
    StudentAccountUpdate,
)
from app.schemas.students import StudentAttendanceListOut
from app.services.auth_service import AuthService
from sqlalchemy import select, func, and_, or_
from app.models import Absence
from app.models.student import Student
from typing import Any

router = APIRouter(prefix="/accounts", tags=["Student Accounts"])


@router.post(
    "/students",
    response_model=StudentAccountResponse,
    status_code=201,
    summary="Create Student Account",
)
async def create_student_account(
    data: StudentAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.register_student(
        data=data,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/students", response_model=List[StudentAttendanceListOut], summary="Get Students with absences")
async def get_students(
    search: Optional[str] = None,
    year: Optional[str] = None,
    group: Optional[str] = None,
    status_query: Optional[str] = None,
    sort_by: Optional[str] = "nom",
    sort_order: Optional[str] = "asc",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    absence_count_sq = (
        select(
            Absence.student_matricule,
            func.count(Absence.id).label("total_absences"),
        )
        .where(Absence.is_absent.is_(True))
        .group_by(Absence.student_matricule)
        .subquery()
    )

    base_q = (
        select(
            Student,
            func.coalesce(absence_count_sq.c.total_absences, 0).label("total_absences"),
        )
        .outerjoin(
            absence_count_sq,
            Student.student_id == absence_count_sq.c.student_matricule,
        )
    )

    filters = []
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                Student.last_name.ilike(like),
                Student.first_name.ilike(like),
                Student.student_id.ilike(like),
                Student.email.ilike(like),
            )
        )
    if year:
        filters.append(Student.level == year)
    if group:
        filters.append(func.lower(Student.group) == group.lower())
    if status_query:
        # We simulate status from is_active since Student model lacks a dedicated status string field
        if status_query == "normal":
            filters.append(Student.is_active.is_(True))
        elif status_query == "bloque":
            filters.append(Student.is_active.is_(False))

    if filters:
        base_q = base_q.where(and_(*filters))

    sort_columns: dict[str, Any] = {
        "nom": Student.last_name,
        "matricule": Student.student_id,
        "total_absences": func.coalesce(absence_count_sq.c.total_absences, 0),
    }
    sort_key = sort_by if sort_by in sort_columns else "nom"
    sort_col = sort_columns[sort_key]
    if sort_order == "desc":
        sort_col = sort_col.desc()
    else:
        sort_col = sort_col.asc()

    base_q = base_q.order_by(sort_col, Student.first_name)

    rows = (await db.execute(base_q)).all()

    items = [
        StudentAttendanceListOut(
            student_id=student.student_id,
            full_name=f"{student.last_name} {student.first_name}",
            email=student.email,
            avatar_url=student.avatar_url,
            level=student.level,
            group=student.group or "",
            total_absences=total_absences,
            status="normal" if student.is_active else "bloque",
            is_active=student.is_active,
        )
        for student, total_absences in rows
    ]

    return items


@router.patch(
    "/students/{account_id}",
    response_model=StudentAccountResponse,
    summary="Update Student Account",
)
async def update_student_account(
    account_id: UUID,
    data: StudentAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.update_student_account(account_id, data)
