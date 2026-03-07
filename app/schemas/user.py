from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional
from uuid import UUID
from app.config import UserRole


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    role: UserRole
    is_active: bool
    avatar_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
