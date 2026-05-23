from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Request, HTTPException, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.teacher import Teacher
from app.models.session import Session, SessionAttendanceSummary
from app.models.module import Module

from app.models.planning_session import PlanningSession, planning_session_teachers

from app.config import UserRole
from app.db import get_db
from app.helpers.permissions import require_role
from app.helpers.role_users import list_users_by_role
from app.schemas import (
    TeacherAccountCreate,
    TeacherAccountResponse,
    TeacherAccountUpdate,
    TeacherProfileResponse,
    AttendanceGroupStats,
    SubjectGroupStats,
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
    teachers = await list_users_by_role(db, UserRole.TEACHER)

    # Pre-fetch planning sessions to attach subjects and groups
    planning_q = select(
        planning_session_teachers.c.teacher_id,
        PlanningSession.subject,
        PlanningSession.group
    ).select_from(
        planning_session_teachers.join(
            PlanningSession, 
            PlanningSession.id == planning_session_teachers.c.planning_session_id
        )
    )
    result = await db.execute(planning_q)

    # Map teacher_id -> { "subjects": set(), "groups": set() }
    teacher_data = {}
    for row in result.all():
        t_id, subject, group = row
        if t_id not in teacher_data:
            teacher_data[t_id] = {"subjects": set(), "groups": set()}
        if subject:
            teacher_data[t_id]["subjects"].add(subject)
        if group:
            teacher_data[t_id]["groups"].add(group)

    # Attach to teacher objects
    responses = []
    for t in teachers:
        data = teacher_data.get(t.id, {"subjects": set(), "groups": set()})
        # Validate ORM object then inject manual arrays
        resp = TeacherAccountResponse.model_validate(t)
        resp.subjects = sorted(list(data["subjects"]))
        resp.groups = sorted(list(data["groups"]))
        responses.append(resp)

    return responses


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


@router.get(
    "/teachers/{matricule}",
    response_model=TeacherProfileResponse,
    summary="Get Teacher Profile and Attendance Stats",
)
async def get_teacher_profile(
    matricule: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(UserRole.ADMIN)),
):
    # Fetch Teacher
    teacher = (
        await db.execute(select(Teacher).where(Teacher.employee_id == matricule))
    ).scalar_one_or_none()

    if not teacher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Teacher with matricule '{matricule}' not found."
        )

    # 1. Fetch Planned Sessions to get all assigned subjects/groups
    planning_q = select(
        PlanningSession.subject,
        PlanningSession.year,
        PlanningSession.group
    ).select_from(
        planning_session_teachers.join(
            PlanningSession, 
            PlanningSession.id == planning_session_teachers.c.planning_session_id
        )
    ).where(planning_session_teachers.c.teacher_id == teacher.id)
    
    planned_rows = (await db.execute(planning_q)).all()
    
    subject_map: dict[tuple[str, str], set[str]] = {}
    distinct_subjects: set[str] = set()
    
    # Aggregate stats
    group_stats: dict[tuple[str, str, str], dict[str, int]] = {}
    
    for row in planned_rows:
        subj, niveau, grp = row
        if subj:
            distinct_subjects.add(subj)
            niveau_str = niveau.value if hasattr(niveau, 'value') else str(niveau)
            sub_key = (subj, niveau_str)
            if sub_key not in subject_map:
                subject_map[sub_key] = set()
            if grp:
                subject_map[sub_key].add(grp)
                # Ensure it appears in attendance_by_group even with 0 sessions
                key = (niveau_str, subj, grp)
                if key not in group_stats:
                    group_stats[key] = {"present": 0, "total": 0}

    # 2. Fetch actual sessions for this teacher with Module and AttendanceSummary
    stmt = (
        select(Session, Module.nom.label("subject_name"), SessionAttendanceSummary)
        .join(Module, Session.module_id == Module.id)
        .outerjoin(SessionAttendanceSummary, Session.id == SessionAttendanceSummary.session_id)
        .where(Session.teacher_id == teacher.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    for row in rows:
        session, subject_name, summary = row
        distinct_subjects.add(subject_name)
        niveau_str = session.year.value if hasattr(session.year, 'value') else str(session.year)
        
        sub_key = (subject_name, niveau_str)
        if sub_key not in subject_map:
            subject_map[sub_key] = set()
        if session.group:
            subject_map[sub_key].add(session.group)
        
        # Only consider sessions that have an attendance summary
        if summary and summary.total_students > 0:
            key = (niveau_str, subject_name, session.group)
            if key not in group_stats:
                group_stats[key] = {"present": 0, "total": 0}
            
            group_stats[key]["present"] += summary.present_count
            group_stats[key]["total"] += summary.total_students

    # 3. Build attendance_by_group and subjects list
    attendance_by_group = []
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

    overall_attendance_rate = round(total_rate_sum / len(attendance_by_group)) if attendance_by_group else 0.0

    # Build subjects array, sorting groups: "Cours" first, then alphabetically
    subjects_list = []
    distinct_groups = set()
    for (subject, niveau), groups_set in subject_map.items():
        distinct_groups.update(groups_set)
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

    return TeacherProfileResponse(
        employee_id=teacher.employee_id or matricule,
        last_name=teacher.last_name,
        first_name=teacher.first_name,
        email=teacher.email,
        specialization=teacher.specialization,
        role="TEACHER",
        avatar_url=teacher.avatar_url,
        is_active=teacher.is_active,
        total_subjects=len(distinct_subjects),
        total_groups=len(distinct_groups),
        overall_attendance_rate=overall_attendance_rate,
        attendance_by_group=attendance_by_group,
        subjects=subjects_list
    )

