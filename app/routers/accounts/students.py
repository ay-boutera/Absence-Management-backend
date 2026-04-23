from typing import List
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
from app.services.auth_service import AuthService

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


@router.get("/students", response_model=List[StudentAccountResponse], summary="Get Students")
async def get_students(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    return await list_users_by_role(db, UserRole.STUDENT)


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
