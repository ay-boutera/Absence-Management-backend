"""
services/oauth_service.py — Google OAuth2 Flow
================================================
Handles the complete Google OAuth2 authorization code flow.

Full sequence:
    Step 1 — GET /auth/google
    Server generates an authorization URL with a random 'state' parameter
    (CSRF protection for the OAuth flow itself) and returns it to the frontend.
    Frontend redirects the browser to Google's consent screen.

    Step 2 — Google → GET /auth/google/callback?code=...&state=...
    Google redirects back with an authorization 'code'.
    Server:
        a. Validates 'state' matches what we stored in Redis (CSRF check) in production no redis available for now ?
        b. Exchanges 'code' for tokens at Google's token endpoint
        c. Fetches the user's profile (email, name, picture) from Google
        d. Validates email matches @esi-sba.dz pattern
        e. Finds existing user OR creates a new one (first-time login)
        f. Issues our own JWT tokens and sets HttpOnly cookies
        g. Redirects browser to the React frontend dashboard

Why store 'state' in Redis?
    The state parameter prevents CSRF attacks on the OAuth callback itself.
    We generate a random string, store it in Redis with a 10-minute TTL,
    and verify it when Google calls us back. No state match = reject.

Why link by email AND google_id?
    - First login: find by email → link google_id to existing account
    (Admin may have pre-created the account before the user ever logged in)
    - Subsequent logins: find directly by google_id (faster, more stable)
"""

import secrets
from typing import Optional

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.models.user import Account, UserRole, Student
from app.models.audit_log import AuditLog, ActionType
from app.helpers.email import validate_esi_email, extract_name_hint_from_email
from app.helpers.security import create_access_token, create_refresh_token
from app.services.redis_service import RedisService
from app.config import settings

# ── Google OAuth2 endpoints ────────────────────────────────────────────────────
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Scopes we request from Google:
#   openid    — gives us the 'sub' (google_id) and id_token
#   email     — gives us the verified email address
#   profile   — gives us name and picture URL
GOOGLE_SCOPES = "openid email profile"

# Redis key prefix for OAuth state storage
_STATE_PREFIX = "oauth_state:"
_STATE_TTL = 600  # 10 minutes


class OAuthService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.redis = RedisService()

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _make_client(self) -> AsyncOAuth2Client:
        """Create a configured Authlib OAuth2 client."""
        return AsyncOAuth2Client(
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
            scope=GOOGLE_SCOPES,
        )

    async def _log(
        self,
        action: ActionType,
        user_id: Optional[any] = None,
        resource_id: Optional[str] = None,
        details: Optional[str] = None,
        ip: Optional[str] = None,
    ):
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type="oauth",
            resource_id=resource_id,
            details=details,
            ip_address=ip,
        )
        self.db.add(log)

    # ── Step 1: Generate authorization URL ───────────────────────────────────


    async def get_authorization_url(self, state: str) -> str:
        async with self._make_client() as client:
            url, _ = client.create_authorization_url(
                GOOGLE_AUTH_URL,
                state=state,
                prompt="select_account",
            )
        return url

    # ── Step 2: Handle callback ───────────────────────────────────────────────
    async def handle_callback(self, code: str, ip_address: str) -> tuple:
        """
        Process the OAuth callback from Google.

        Returns: (user, access_token, refresh_token, is_new_user)
        Raises:  HTTPException on any failure
        """
        # ── a. State validation is handled in the router via sessions ─────────

        # ── b. Exchange code for tokens ───────────────────────────────────────
        async with self._make_client() as client:
            try:
                token_data = await client.fetch_token(
                    GOOGLE_TOKEN_URL,
                    code=code,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to exchange authorization code: {exc}",
                )

        # ── c. Fetch user profile from Google ─────────────────────────────────
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
        # Google userinfo fields: sub, email, email_verified, name,
        #                         given_name, family_name, picture
        google_id = google_profile.get("sub")
        google_email = google_profile.get("email", "").lower()
        is_verified = google_profile.get("email_verified", False)
        given_name = google_profile.get("given_name", "")
        family_name = google_profile.get("family_name", "")
        avatar_url = google_profile.get("picture")

        # ── d. Validate email is verified and matches ESI-SBA format ──────────
        if not is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Google account email is not verified.",
            )

        validate_esi_email(google_email)  # raises 403 if not x.name@esi-sba.dz

        # ── e. Find or create the user ────────────────────────────────────────
        is_new_user = False

        # First: look up by google_id (subsequent logins — fastest path)
        result = await self.db.execute(
            select(Account).where(Account.google_id == google_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            # Second: look up by email (first OAuth login for an existing account)
            # The admin may have pre-created the account before the user logged in.
            result = await self.db.execute(
                select(Account).where(Account.email == google_email)
            )
            user = result.scalar_one_or_none()

            if user:
                # Link the Google identity to the existing account
                user.google_id = google_id
                user.avatar_url = avatar_url
                self.db.add(user)
            else:
                # Brand new user — create the account automatically
                # Name fallback: use email hints if Google didn't return names
                if not given_name:
                    hint = extract_name_hint_from_email(google_email)
                    given_name = hint["first_initial"]
                    family_name = hint["last_name"]

                user = Account(
                    email=google_email,
                    google_id=google_id,
                    first_name=given_name,
                    last_name=family_name,
                    avatar_url=avatar_url,
                    role=UserRole.STUDENT,  # default — admin promotes later
                    is_active=True,
                    # hashed_password stays NULL — no credential login for this user
                )
                self.db.add(user)
                await self.db.flush()  # get user.id without committing

                self.db.add(
                    Student(
                        user_id=user.id,
                        student_id=f"oauth-{google_id[:12]}",
                        program="N/A",
                        level="N/A",
                        group=None,
                    )
                )
                await self.db.flush()
                is_new_user = True

                await self._log(
                    ActionType.ACCOUNT_CREATED,
                    user_id=user.id,
                    resource_id=str(user.id),
                    details=f"Auto-created via Google OAuth: {google_email}",
                    ip=ip_address,
                )

        # ── Check account is active ───────────────────────────────────────────
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

        # ── f. Issue OUR JWT tokens ───────────────────────────────────────────
        # We do NOT use Google's tokens after this point.
        # We issue our own short-lived access + long-lived refresh tokens.
        from datetime import datetime, timezone

        user.last_activity = datetime.now(timezone.utc)
        self.db.add(user)

        token_payload = {"sub": str(user.id), "role": user.role.value}
        access_token = create_access_token(token_payload)
        refresh_token = create_refresh_token(token_payload)

        await self._log(
            ActionType.LOGIN_SUCCESS,
            user_id=user.id,
            resource_id=str(user.id),
            details="Google OAuth login",
            ip=ip_address,
        )

        return user, access_token, refresh_token, is_new_user
