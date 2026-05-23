import asyncio
import sys
from sqlalchemy import select, func
from app.db import engine
from app.models.teacher import Teacher
from app.models.session import Session, SessionAttendanceSummary
from app.models.module import Module
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

async def test():
    async with AsyncSession(engine) as db:
        # Check Teacher with sessions
        stmt = (
            select(Teacher)
            .where(Teacher.employee_id == "ENS002") # User mentioned TRARI NOUR ELFOUAD earlier, let's use his or another
        )
        t = (await db.execute(stmt)).scalar()
        if not t:
            t = (await db.execute(select(Teacher))).scalars().first()

        if t:
            print(f"Teacher found: {t.employee_id} {t.first_name} {t.last_name}")
            
        # Join Session, Module, AttendanceSummary
        stmt = (
            select(Session, Module.nom.label("subject_name"), SessionAttendanceSummary)
            .join(Module, Session.module_id == Module.id)
            .outerjoin(SessionAttendanceSummary, Session.id == SessionAttendanceSummary.session_id)
            .where(Session.teacher_id == t.id)
        )
        res = await db.execute(stmt)
        for row in res.all():
            session, subject_name, summary = row
            print(f"Session: {session.year} {subject_name} {session.group} {session.type.value} -> summary: {summary.present_count if summary else 0}/{summary.total_students if summary else 0}")

asyncio.run(test())
