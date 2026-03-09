from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db import get_db
from app.models.user import User
from app.schemas.user import UserResponse, UserCreate
from app.core.permissions import require_active_user, require_role
from app.config import UserRole
from sqlalchemy import select
from app.services.auth_service import AuthService

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (Public for testing)."""
    service = AuthService(db)
    user = await service.register(
        data=data,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent"),
    )
    return user


@router.get("/", response_model=List[UserResponse])
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all users (Admin only)."""
    result = await db.execute(select(User))
    return result.scalars().all()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(require_active_user)):
    """Get current user profile."""
    return current_user
