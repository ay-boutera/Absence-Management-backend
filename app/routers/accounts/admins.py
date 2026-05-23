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
    AdminProfileResponse,
    AttendanceGroupStats,
    SubjectGroupStats,
)
from app.models.teacher import Teacher
from app.models.session import Session, SessionAttendanceSummary
from app.models.module import Module
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
            detail={"error": "Super admin already exists."},
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


@router.get("/", response_model=List[UserResponse], summary="Get All Accounts")
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


@router.get("/me", response_model=UserResponse, summary="Get Current User")
async def get_me(current_user=Depends(require_active_user)):
    return current_user


@router.get("/{account_id}", response_model=UserResponse, summary="Get Account By ID")
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


@router.get(
    "/administrators/{account_id}",
    response_model=AdminProfileResponse,
    summary="Get Admin Profile and optional Teaching Stats",
)
async def get_admin_profile(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    # 1. Fetch admin
    admin = (
        await db.execute(select(Admin).where(Admin.id == account_id))
    ).scalar_one_or_none()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Admin with id '{account_id}' not found."
        )

    # 2. Check if admin is also a teacher (link by email)
    teacher = (
        await db.execute(select(Teacher).where(Teacher.email == admin.email))
    ).scalar_one_or_none()

    attendance_by_group = []
    subjects_list = []
    total_subjects = 0
    total_groups = 0
    overall_attendance_rate = 0.0

    if teacher:
        stmt = (
            select(Session, Module.nom.label("subject_name"), SessionAttendanceSummary)
            .join(Module, Session.module_id == Module.id)
            .outerjoin(SessionAttendanceSummary, Session.id == SessionAttendanceSummary.session_id)
            .where(Session.teacher_id == teacher.id)
        )
        result = await db.execute(stmt)
        rows = result.all()

        group_stats: dict[tuple[str, str, str], dict[str, int]] = {}
        distinct_subjects: set[str] = set()

        for row in rows:
            session, subject_name, summary = row
            distinct_subjects.add(str(session.module_id))
            
            if summary and summary.total_students > 0:
                key = (session.year, subject_name, session.group)
                if key not in group_stats:
                    group_stats[key] = {"present": 0, "total": 0}
                
                group_stats[key]["present"] += summary.present_count
                group_stats[key]["total"] += summary.total_students

        subject_map: dict[tuple[str, str], set[str]] = {}
        total_rate_sum = 0.0

        for (niveau, subject, group), stats in group_stats.items():
            rate = round((stats["present"] / stats["total"]) * 100) if stats["total"] > 0 else 0
            attendance_by_group.append(
                AttendanceGroupStats(
                    niveau=niveau,
                    subject=subject,
                    group=group,
                    attendance_rate=rate
                )
            )
            total_rate_sum += rate

            sub_key = (subject, niveau)
            if sub_key not in subject_map:
                subject_map[sub_key] = set()
            subject_map[sub_key].add(group)

        overall_attendance_rate = round(total_rate_sum / len(attendance_by_group)) if attendance_by_group else 0.0

        for (subject, niveau), groups_set in subject_map.items():
            sorted_groups = sorted(
                list(groups_set),
                key=lambda g: (0, g) if g and g.lower() == "cours" else (1, g)
            )
            subjects_list.append(
                SubjectGroupStats(
                    subject_name=subject,
                    niveau=niveau,
                    groups=sorted_groups
                )
            )

        total_subjects = len(distinct_subjects)
        total_groups = len({g for (_, _, g) in group_stats.keys()})

    matricule_val = str(admin.id)
    # If the user also wants to show employee_id if it's a teacher, we can do it:
    if teacher and teacher.employee_id:
        matricule_val = teacher.employee_id

    return AdminProfileResponse(
        matricule=matricule_val,
        nom=admin.last_name,
        prenom=admin.first_name,
        email=admin.email,
        departement=admin.department,
        role="ADMIN",
        avatar_url=admin.avatar_url,
        is_active=admin.is_active,
        total_subjects=total_subjects,
        total_groups=total_groups,
        overall_attendance_rate=overall_attendance_rate,
        attendance_by_group=attendance_by_group,
        subjects=subjects_list,
    )
