# Create Pydantic schemas

from .auth import *
from .account_admins import *
from .account_students import *
from .account_teachers import *
from .import_export import *

UserResponse = AdminAccountResponse | TeacherAccountResponse | StudentAccountResponse

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "TokenRefreshResponse",
    "PasswordResetRequest",
    "PasswordResetConfirm",
    "ChangePasswordRequest",
    "MessageResponse",
    "OAuthStateResponse",
    "OAuthLoginResponse",
    "UserResponse",
    "StudentAccountCreate",
    "TeacherAccountCreate",
    "AdminAccountCreate",
    "StudentAccountResponse",
    "TeacherAccountResponse",
    "AdminAccountResponse",
    "StudentAccountUpdate",
    "TeacherAccountUpdate",
    "AdminAccountUpdate",
    "UserStatusUpdate",
    "ImportErrorItem",
    "ImportResponse",
]
