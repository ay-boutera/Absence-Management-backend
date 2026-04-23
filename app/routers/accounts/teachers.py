from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.helpers.role_users import list_users_by_role
from app.schemas import (
    TeacherAccountCreate,
    TeacherAccountResponse,
    TeacherAccountUpdate,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/accounts", tags=["Teacher Accounts"])


@router.post(
    "/teachers",
    response_model=TeacherAccountResponse,
    status_code=201,
    summary="Create Teacher Account",
)
async def create_teacher_account(
    data: TeacherAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.register_teacher(
        data=data,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/teachers", response_model=List[TeacherAccountResponse], summary="Get Teachers")
async def get_teachers(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    return await list_users_by_role(db, UserRole.TEACHER)


@router.patch(
    "/teachers/{account_id}",
    response_model=TeacherAccountResponse,
    summary="Update Teacher Account",
)
async def update_teacher_account(
    account_id: UUID,
    data: TeacherAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.update_teacher_account(account_id, data)
