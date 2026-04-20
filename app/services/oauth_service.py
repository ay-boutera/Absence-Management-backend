import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.helpers.email import validate_esi_email
from app.helpers.role_users import (
    RoleUser,
    get_user_by_email,
    get_user_by_google_id,
    user_role,
)
from app.helpers.security import create_access_token, create_refresh_token
from app.models.audit_log import ActionType, AuditLog

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES = "openid email profile"


class OAuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _make_client(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
            scope=GOOGLE_SCOPES,
        )

    async def _log(
        self,
        action: ActionType,
        user_id=None,
        resource_id: str | None = None,
        details: str | None = None,
        ip: str | None = None,
    ):
        self.db.add(
            AuditLog(
                user_id=user_id,
                action=action,
                resource_type="oauth",
                resource_id=resource_id,
                details=details,
                ip_address=ip,
            )
        )

    async def get_authorization_url(self, state: str) -> str:
        async with self._make_client() as client:
            url, _ = client.create_authorization_url(
                GOOGLE_AUTH_URL,
                state=state,
                prompt="select_account",
            )
        return url

    async def handle_callback(self, code: str, ip_address: str) -> tuple[RoleUser, str, str, bool]:
        async with self._make_client() as client:
            try:
                token_data = await client.fetch_token(GOOGLE_TOKEN_URL, code=code)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to exchange authorization code: {exc}",
                ) from exc

        async with httpx.AsyncClient() as http:
            resp = await http.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not retrieve profile from Google. Please try again.",
            )

        google_profile = resp.json()
        google_id = google_profile.get("sub")
        google_email = (google_profile.get("email") or "").lower()
        is_verified = bool(google_profile.get("email_verified", False))
        avatar_url = google_profile.get("picture")

        if not is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Google account email is not verified.",
            )

        validate_esi_email(google_email)

        user = await get_user_by_google_id(self.db, google_id)
        if user is None:
            user = await get_user_by_email(self.db, google_email)
            if user is None:
                await self._log(
                    ActionType.LOGIN_FAILED,
                    details=f"OAuth login attempt for non-existent email: {google_email}",
                    ip=ip_address,
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No account found with this institutional email. Please contact the administrator.",
                )
            user.google_id = google_id
            user.avatar_url = avatar_url
            self.db.add(user)

        if not user.is_active:
            await self._log(
                ActionType.LOGIN_FAILED,
                user_id=user.id,
                resource_id=str(user.id),
                details="OAuth login attempt on deactivated account",
                ip=ip_address,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated. Contact the administration.",
            )

        from datetime import datetime, timezone

        user.last_activity = datetime.now(timezone.utc)
        self.db.add(user)

        token_payload = {"sub": str(user.id), "role": user_role(user).value}
        access_token = create_access_token(token_payload)
        refresh_token = create_refresh_token(token_payload)

        await self._log(
            ActionType.LOGIN_SUCCESS,
            user_id=user.id,
            resource_id=str(user.id),
            details="Google OAuth login",
            ip=ip_address,
        )

        return user, access_token, refresh_token, False
