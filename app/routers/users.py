from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db import get_db
from app.models.user import Account, Admin
from app.schemas.user import (
    AccountCreate,
    AccountResponse,
    AccountStatusUpdate,
    AdminAccountUpdate,
    AdminAccountCreate,
    StudentAccountUpdate,
    StudentAccountCreate,
    TeacherAccountUpdate,
    TeacherAccountCreate,
)
from app.helpers.permissions import require_active_user, require_role, require_super_admin
from app.config import UserRole
from sqlalchemy import select
from app.services.auth_service import AuthService

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post(
    "/super-admins",
    response_model=AccountResponse,
    status_code=201,
    summary="Create initial Super Admin (Bootstrap)",
)
async def create_super_admin_account(
    data: AdminAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Bootstrap endpoint to create the first super admin.
    Public by design, but only allowed while no super admin exists.
    """
    existing_super_admin = await db.execute(
        select(Admin.id).where(Admin.admin_level == "super").limit(1)
    )
    if existing_super_admin.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin already exists.",
        )

    service = AuthService(db)
    account = await service.register(
        data=AccountCreate(
            role=UserRole.ADMIN,
            admin_level="super",
            **data.model_dump(exclude={"admin_level"}),
        ),
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
        allow_full_firstname_email=True,
    )
    return account


@router.post(
    "/students",
    response_model=AccountResponse,
    status_code=201,
    summary="Create Student Account",
)
async def create_student_account(
    data: StudentAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Create a student account (Admin only)."""
    service = AuthService(db)
    account = await service.register(
        data=AccountCreate(role=UserRole.STUDENT, **data.model_dump()),
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )
    return account


@router.post(
    "/teachers",
    response_model=AccountResponse,
    status_code=201,
    summary="Create Teacher Account",
)
async def create_teacher_account(
    data: TeacherAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Create a teacher account (Admin only)."""
    service = AuthService(db)
    account = await service.register(
        data=AccountCreate(role=UserRole.TEACHER, **data.model_dump()),
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )
    return account


@router.post(
    "/admins",
    response_model=AccountResponse,
    status_code=201,
    summary="Create Admin Account",
)
async def create_admin_account(
    data: AdminAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_super_admin),
):
    """Create a regular admin account (Super Admin only)."""
    service = AuthService(db)
    account = await service.register(
        data=AccountCreate(
            role=UserRole.ADMIN,
            admin_level="regular",
            **data.model_dump(exclude={"admin_level"}),
        ),
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )
    return account


@router.get("/", response_model=List[AccountResponse], summary="Get Accounts")
async def get_accounts(
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """List all accounts (Admin only)."""
    result = await db.execute(select(Account))
    return result.scalars().all()


@router.get("/students", response_model=List[AccountResponse], summary="Get Students")
async def get_students(
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """
    List all students (Admin only).

    Returns every account whose role is ``UserRole.STUDENT``.
    """
    result = await db.execute(
        select(Account).where(Account.role == UserRole.STUDENT)
    )
    return result.scalars().all()


@router.get("/teachers", response_model=List[AccountResponse], summary="Get Teachers")
async def get_teachers(
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """
    List all teachers (Admin only).

    Returns every account whose role is ``UserRole.TEACHER``.
    """
    result = await db.execute(
        select(Account).where(Account.role == UserRole.TEACHER)
    )
    return result.scalars().all()


@router.get("/admins", response_model=List[AccountResponse], summary="Get Admins")
async def get_admins(
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """
    List all admins (Admin only).

    Returns every account whose role is ``UserRole.ADMIN``.
    """
    result = await db.execute(select(Account).where(Account.role == UserRole.ADMIN))
    return result.scalars().all()


@router.get("/me", response_model=AccountResponse, summary="Get Me")
async def get_me(current_user: Account = Depends(require_active_user)):
    """Get current account profile."""
    return current_user


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Get Account By ID",
)
async def get_account_by_id(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Fetch one account by UUID (Admin only)."""
    service = AuthService(db)
    return await service.get_account_by_id(account_id)


@router.patch(
    "/students/{account_id}",
    response_model=AccountResponse,
    summary="Update Student Account",
)
async def update_student_account(
    account_id: UUID,
    data: StudentAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Update a student account and student profile fields (Admin only)."""
    service = AuthService(db)
    return await service.update_student_account(account_id, data)


@router.patch(
    "/teachers/{account_id}",
    response_model=AccountResponse,
    summary="Update Teacher Account",
)
async def update_teacher_account(
    account_id: UUID,
    data: TeacherAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Update a teacher account and teacher profile fields (Admin only)."""
    service = AuthService(db)
    return await service.update_teacher_account(account_id, data)


@router.patch(
    "/admins/{account_id}",
    response_model=AccountResponse,
    summary="Update Admin Account",
)
async def update_admin_account(
    account_id: UUID,
    data: AdminAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Update an admin account and admin profile fields (Admin only)."""
    service = AuthService(db)
    return await service.update_admin_account(account_id, data)


@router.patch(
    "/{account_id}/status",
    response_model=AccountResponse,
    summary="Activate / Deactivate Account",
)
async def update_account_status(
    account_id: UUID,
    data: AccountStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_role(UserRole.ADMIN)),
):
    """Enable or disable an account (Admin only)."""
    if str(current_user.id) == str(account_id) and not data.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    service = AuthService(db)
    return await service.set_account_active_state(account_id, data.is_active)
