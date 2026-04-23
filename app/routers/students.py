"""
routers/students.py — Academic Student Endpoints
=================================================

PATCH /api/v1/students/{student_id}/status   Admin: update a student's academic status (Feature 3).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.models.student import AcademicStudent
from app.schemas.students import AcademicStudentStatusOut, StudentStatusUpdate

router = APIRouter(tags=["Students"])


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
