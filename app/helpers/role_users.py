from __future__ import annotations

from typing import Optional, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.models.admin import Admin
from app.models.teacher import Teacher
from app.models.student import Student

RoleUser = Union[Admin, Teacher, Student]


def user_role(user: RoleUser) -> UserRole:
    if isinstance(user, Admin):
        return UserRole.ADMIN
    if isinstance(user, Teacher):
        return UserRole.TEACHER
    return UserRole.STUDENT


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> Optional[RoleUser]:
    for model in (Admin, Teacher, Student):
        result = await db.execute(select(model).where(model.id == user_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
    return None


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[RoleUser]:
    normalized = email.strip().lower()
    for model in (Admin, Teacher, Student):
        result = await db.execute(select(model).where(func.lower(model.email) == normalized))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
    return None


async def get_user_by_google_id(db: AsyncSession, google_id: str) -> Optional[RoleUser]:
    for model in (Admin, Teacher, Student):
        result = await db.execute(select(model).where(model.google_id == google_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
    return None


async def get_student_by_student_id(db: AsyncSession, student_id: str) -> Optional[Student]:
    result = await db.execute(select(Student).where(Student.student_id == student_id))
    return result.scalar_one_or_none()


async def email_exists_for_other(
    db: AsyncSession,
    email: str,
    current_user_id: UUID,
) -> bool:
    normalized = email.strip().lower()
    for model in (Admin, Teacher, Student):
        result = await db.execute(
            select(model.id).where(func.lower(model.email) == normalized, model.id != current_user_id)
        )
        if result.scalar_one_or_none() is not None:
            return True
    return False


async def list_users_by_role(db: AsyncSession, role: Optional[UserRole] = None) -> list[RoleUser]:
    if role == UserRole.ADMIN:
        return list((await db.execute(select(Admin))).scalars().all())
    if role == UserRole.TEACHER:
        return list((await db.execute(select(Teacher))).scalars().all())
    if role == UserRole.STUDENT:
        return list((await db.execute(select(Student))).scalars().all())

    users: list[RoleUser] = []
    users.extend((await db.execute(select(Admin))).scalars().all())
    users.extend((await db.execute(select(Teacher))).scalars().all())
    users.extend((await db.execute(select(Student))).scalars().all())
    users.sort(key=lambda u: (u.created_at or 0, str(u.id)), reverse=True)
    return users
