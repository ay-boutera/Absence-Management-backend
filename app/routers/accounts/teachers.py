from typing import List, Optional, cast
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
    description="""
Returns a complete teacher profile identified by **employee_id** (matricule).

Includes:
- Personal/account info: name, email, phone, specialization, avatar, active status
- Global KPIs: `total_subjects`, `total_groups`, `total_sessions`, `total_absences`, `overall_attendance_rate`
- Group attendance details: `attendance_by_group[]` with:
  - `niveau`, `subject`, `group`
  - `total_sessions` handled by this teacher for that group
  - `total_absences` accumulated from attendance summaries
  - `attendance_rate` = `((total_students - total_absences) / total_students) * 100`
- Subject coverage: `subjects[]` with grouped list of groups per subject/niveau

Input:
- Path parameter `matricule` (teacher employee_id), example: `ENS002`

Auth: Admin only.
""",
    responses={
        200: {
            "description": "Teacher profile fetched successfully",
            "content": {
                "application/json": {
                    "example": {
                        "employee_id": "ENS002",
                        "last_name": "NOUR ELFOUAD",
                        "first_name": "TRARI",
                        "email": "nf.trari@esi-sba.dz",
                        "specialization": "math",
                        "role": "TEACHER",
                        "avatar_url": None,
                        "phone": None,
                        "is_active": True,
                        "total_subjects": 1,
                        "total_groups": 3,
                        "total_sessions": 21,
                        "total_absences": 3,
                        "overall_attendance_rate": 85.7,
                        "attendance_by_group": [
                            {
                                "niveau": "1CS",
                                "subject": "Archi",
                                "group": "G6",
                                "total_sessions": 7,
                                "total_absences": 1,
                                "attendance_rate": 85.7,
                            },
                            {
                                "niveau": "1CS",
                                "subject": "Archi",
                                "group": "G2",
                                "total_sessions": 7,
                                "total_absences": 1,
                                "attendance_rate": 85.7,
                            },
                            {
                                "niveau": "1CS",
                                "subject": "Archi",
                                "group": "G3",
                                "total_sessions": 7,
                                "total_absences": 1,
                                "attendance_rate": 85.7,
                            },
                        ],
                        "subjects": [
                            {
                                "subject_name": "Archi",
                                "niveau": "1CS",
                                "groups": ["G2", "G3", "G6"],
                            }
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Teacher not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Teacher with matricule 'ENS002' not found."}
                }
            },
        },
    },
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
                    group_stats[key] = {
                        "present": 0,
                        "total_students": 0,
                        "total_absences": 0,
                        "total_sessions": 0,
                    }

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
        key = (niveau_str, subject_name, session.group)
        if key not in group_stats:
            group_stats[key] = {
                "present": 0,
                "total_students": 0,
                "total_absences": 0,
                "total_sessions": 0,
            }
        group_stats[key]["total_sessions"] += 1

        if summary and summary.total_students > 0:
            key = (niveau_str, subject_name, session.group)
            group_stats[key]["present"] += summary.present_count
            group_stats[key]["total_students"] += summary.total_students
            group_stats[key]["total_absences"] += summary.absent_count

    # 3. Build attendance_by_group and subjects list
    attendance_by_group = []
    total_present = 0
    total_students_all = 0
    total_sessions_all = 0
    total_absences_all = 0

    for (niveau, subject, group), stats in group_stats.items():
        rate = (
            round((stats["present"] / stats["total_students"]) * 100, 1)
            if stats["total_students"] > 0
            else 0.0
        )
        attendance_by_group.append(
            AttendanceGroupStats(
                niveau=niveau,
                subject=subject,
                group=group,
                total_sessions=stats["total_sessions"],
                total_absences=stats["total_absences"],
                attendance_rate=rate
            )
        )
        total_present += stats["present"]
        total_students_all += stats["total_students"]
        total_sessions_all += stats["total_sessions"]
        total_absences_all += stats["total_absences"]

    overall_attendance_rate = (
        round((total_present / total_students_all) * 100, 1)
        if total_students_all > 0
        else 0.0
    )

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

    employee_id_val = cast(Optional[str], teacher.employee_id)

    return TeacherProfileResponse(
        employee_id=employee_id_val if employee_id_val else matricule,
        last_name=cast(str, teacher.last_name),
        first_name=cast(str, teacher.first_name),
        email=cast(str, teacher.email),
        specialization=cast(Optional[str], teacher.specialization),
        role="TEACHER",
        avatar_url=cast(Optional[str], teacher.avatar_url),
        phone=cast(Optional[str], teacher.phone),
        is_active=cast(bool, teacher.is_active),
        total_subjects=len(distinct_subjects),
        total_groups=len(distinct_groups),
        total_sessions=total_sessions_all,
        total_absences=total_absences_all,
        overall_attendance_rate=overall_attendance_rate,
        attendance_by_group=attendance_by_group,
        subjects=subjects_list
    )
