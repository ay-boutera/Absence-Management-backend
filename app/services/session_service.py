from datetime import date as date_type
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import SessionStatusEnum
from app.models import Module, PlanningSession, Salle, Session, Teacher

_TODAY_DAY_NAMES = {
    0: "Lundi",
    1: "Mardi",
    2: "Mercredi",
    3: "Jeudi",
    4: "Vendredi",
    5: "Samedi",
    6: "Dimanche",
}

async def get_or_create_module(db: AsyncSession, subject: str) -> Module:
    result = await db.execute(select(Module).where(Module.code == subject))
    module = result.scalar_one_or_none()
    if module is None:
        module = Module(code=subject, nom=subject)
        db.add(module)
        await db.flush()
    return module

async def get_or_create_salle(db: AsyncSession, room_code: str) -> Salle:
    result = await db.execute(select(Salle).where(Salle.code == room_code))
    salle = result.scalar_one_or_none()
    if salle is None:
        salle = Salle(code=room_code)
        db.add(salle)
        await db.flush()
    return salle

async def materialise_sessions_for_teacher(
    db: AsyncSession,
    teacher: Teacher,
    today: date_type,
) -> list[Session]:
    today_day_name = _TODAY_DAY_NAMES.get(today.weekday())
    if today_day_name is None:
        return []

    planning_q = (
        select(PlanningSession)
        .options(selectinload(PlanningSession.teachers))
        .where(
            and_(
                PlanningSession.teachers.any(Teacher.id == teacher.id),
                PlanningSession.day == today_day_name,
            )
        )
    )
    planning_sessions = list((await db.execute(planning_q)).scalars().all())

    result_sessions: list[Session] = []

    for ps in planning_sessions:
        module = await get_or_create_module(db, ps.subject)
        salle = await get_or_create_salle(db, ps.room) if ps.room else None

        existing = (
            await db.execute(
                select(Session)
                .options(
                    selectinload(Session.module),
                    selectinload(Session.teacher),
                    selectinload(Session.room),
                )
                .where(
                    and_(
                        Session.planning_session_id == ps.id,
                        Session.teacher_id == teacher.id,
                        Session.date == today,
                    )
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = Session(
                planning_session_id=ps.id,
                module_id=module.id,
                teacher_id=teacher.id,
                room_id=salle.id if salle else None,
                group=ps.group,
                year=ps.year,
                section=ps.section,
                speciality=ps.speciality,
                semester=ps.semester,
                date=today,
                start_time=ps.time_start,
                end_time=ps.time_end,
                type=ps.type,
                status=SessionStatusEnum.SCHEDULED,
                is_makeup=False,
            )
            db.add(existing)
            await db.flush()
            await db.refresh(existing, ["module", "teacher", "room"])

        result_sessions.append(existing)

    return result_sessions
