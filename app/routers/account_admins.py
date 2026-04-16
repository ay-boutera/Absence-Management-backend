from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_active_user, require_role, require_super_admin
from app.helpers.role_users import list_users_by_role
from app.models import Admin
from app.schemas import (
    AdminAccountCreate,
    AdminAccountResponse,
    AdminAccountUpdate,
    UserResponse,
    UserStatusUpdate,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/accounts", tags=["Admin Accounts"])


@router.post(
    "/super-admins",
    response_model=AdminAccountResponse,
    status_code=201,
    summary="Create initial Super Admin (Bootstrap)",
)
async def create_super_admin_account(
    data: AdminAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    existing_super_admin = await db.execute(
        select(Admin.id).where(Admin.admin_level == "super").limit(1)
    )
    if existing_super_admin.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Super admin already exists.",
            },
        )

    service = AuthService(db)
    user = await service.register_admin(
        data=data,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
        allow_full_firstname_email=True,
        forced_admin_level="super",
    )
    return user


@router.post(
    "/admins",
    response_model=AdminAccountResponse,
    status_code=201,
    summary="Create Admin Account",
)
async def create_admin_account(
    data: AdminAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    service = AuthService(db)
    return await service.register_admin(
        data=data,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
        forced_admin_level="regular",
    )


@router.get("/", response_model=List[UserResponse], summary="Get Accounts")
async def get_accounts(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    return await list_users_by_role(db)


@router.get("/admins", response_model=List[AdminAccountResponse], summary="Get Admins")
async def get_admins(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    return await list_users_by_role(db, UserRole.ADMIN)


@router.get("/me", response_model=UserResponse, summary="Get Me")
async def get_me(current_user=Depends(require_active_user)):
    return current_user


@router.get(
    "/{account_id}",
    response_model=UserResponse,
    summary="Get Account By ID",
)
async def get_account_by_id(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.get_account_by_id(account_id)


@router.patch(
    "/admins/{account_id}",
    response_model=AdminAccountResponse,
    summary="Update Admin Account",
)
async def update_admin_account(
    account_id: UUID,
    data: AdminAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    service = AuthService(db)
    return await service.update_admin_account(account_id, data)


@router.patch(
    "/{account_id}/status",
    response_model=UserResponse,
    summary="Activate / Deactivate Account",
)
async def update_account_status(
    account_id: UUID,
    data: UserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    if str(current_user.id) == str(account_id) and not data.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    service = AuthService(db)
    return await service.set_account_active_state(account_id, data.is_active)
