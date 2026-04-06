"""
routers/auth.py — Authentication HTTP Endpoints
================================================
Both login methods share the same cookie infrastructure:
    - On success → set_auth_cookies() sets the same HttpOnly cookies
    - On logout  → clear_auth_cookies() removes them
    - /auth/me   → works regardless of how the user logged in

Credential endpoints  (existing):
    POST  /api/v1/auth/login                    FR-01
    POST  /api/v1/auth/logout                   FR-01
    POST  /api/v1/auth/refresh                  FR-01
    POST  /api/v1/auth/reset-password           FR-04
    POST  /api/v1/auth/reset-password/confirm   FR-04
    POST  /api/v1/auth/change-password
    GET   /api/v1/auth/me

Google OAuth endpoints  (new):
    GET   /api/v1/auth/google                   → returns Google consent URL
    GET   /api/v1/auth/google/callback          → Google redirects here after login

Frontend flow for OAuth:
    1. User clicks "Sign in with Google"
    2. Frontend calls GET /auth/google → gets { authorization_url }
    3. Frontend does: window.location.href = authorization_url
    4. Google shows consent screen, user approves
    5. Google redirects to GET /auth/google/callback?code=...&state=...
    6. Server validates, sets HttpOnly cookies, redirects to frontend dashboard
"""

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.schemas import (
    LoginRequest,
    LoginResponse,
    TokenRefreshResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
    ChangePasswordRequest,
    MessageResponse,
    OAuthStateResponse,
    OAuthLoginResponse,
    UserResponse,
)
from app.services.auth_service import AuthService
from app.services.oauth_service import OAuthService
from app.helpers.security import (
    set_auth_cookies,
    clear_auth_cookies,
    get_token_from_cookie,
    validate_csrf_token,
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from app.helpers.permissions import require_active_user
from app.helpers.request import get_client_ip
from app.models.user import User
from app.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ══════════════════════════════════════════════════════════════════════════════
# CREDENTIAL AUTH ENDPOINTS  (unchanged from original)
# ══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Credential login — email/student ID + password",
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    FR-01: Authenticate with email (or student matricule) + password.
    The email must be in ESI-SBA format: firstletter.lastname@esi-sba.dz
    On success sets access_token, refresh_token, csrf_token HttpOnly cookies.
    """
    service = AuthService(db)
    user, access_token, refresh_token = await service.login(
        credentials,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_auth_cookies(response, access_token, refresh_token)

    return LoginResponse(
        user_id=str(user.id),
        role=user.role.value,
        full_name=f"{user.first_name} {user.last_name}",
        avatar_url=user.avatar_url,
    )


@router.post(
    "/logout", response_model=MessageResponse, summary="Log out — revoke tokens"
)
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Blacklists both tokens in Redis and clears all auth cookies."""
    validate_csrf_token(request)
    access_token = get_token_from_cookie(request, ACCESS_COOKIE_NAME)
    refresh_token = get_token_from_cookie(request, REFRESH_COOKIE_NAME)

    service = AuthService(db)
    await service.logout(
        access_token=access_token,
        refresh_token=refresh_token,
        user=current_user,
        ip_address=get_client_ip(request),
    )
    clear_auth_cookies(response)
    return MessageResponse(message="Logged out successfully.")


@router.post(
    "/refresh", response_model=TokenRefreshResponse, summary="Refresh access token"
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    from fastapi import HTTPException

    refresh_tok = get_token_from_cookie(request, REFRESH_COOKIE_NAME)
    if not refresh_tok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found. Please log in again.",
        )

    service = AuthService(db)
    new_access, new_refresh = await service.refresh_access_token(refresh_tok)
    set_auth_cookies(response, new_access, new_refresh)
    return TokenRefreshResponse()


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Request password reset email",
)
async def request_password_reset(
    data: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    FR-04 Step 1 — always returns 200 OK to prevent user enumeration.
    Sends reset email if the account exists and has a password set.
    """
    service = AuthService(db)
    await service.request_password_reset(
        email=str(data.email),
        ip_address=get_client_ip(request),
    )
    return MessageResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


@router.post(
    "/reset-password/confirm",
    response_model=MessageResponse,
    summary="Confirm password reset with token",
)
async def confirm_password_reset(
    data: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """FR-04 Step 2 — validates the token and sets the new password."""
    service = AuthService(db)
    await service.confirm_password_reset(data=data, ip_address=get_client_ip(request))
    return MessageResponse(message="Password reset successfully. You can now log in.")


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Change password (authenticated credential users)",
)
async def change_password(
    data: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Only for users who have a password set. OAuth-only users cannot use this."""
    validate_csrf_token(request)

    if not current_user.hashed_password:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account uses Google login. There is no password to change.",
        )

    service = AuthService(db)
    await service.change_password(
        user=current_user, data=data, ip_address=get_client_ip(request)
    )
    return MessageResponse(message="Password changed successfully.")


@router.get("/me", response_model=UserResponse, summary="Get current user info")
async def get_current_user_info(current_user: User = Depends(require_active_user)):
    """
    Returns the logged-in user's profile.
    The React frontend calls this on startup to restore auth state.
    Works for both credential and OAuth sessions.
    """
    return current_user


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH ENDPOINTS  (new)
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/google",
    response_model=OAuthStateResponse,
    summary="Get Google OAuth authorization URL",
)
async def google_login(db: AsyncSession = Depends(get_db)):
    """
    Step 1 of the Google OAuth flow.

    Returns { authorization_url } — the frontend redirects the browser here.
    Stores a random 'state' token in Redis (10 min TTL) for CSRF protection.

    Frontend usage:
        const { authorization_url } = await api.get('/auth/google')
        window.location.href = authorization_url
    """
    service = OAuthService(db)
    url = await service.get_authorization_url()
    return OAuthStateResponse(authorization_url=url)


@router.get(
    "/google/callback",
    summary="Google OAuth callback — sets cookies and redirects to frontend",
)
async def google_callback(
    request: Request,
    response: Response,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2 of the Google OAuth flow — Google redirects here with ?code=&state=

    What happens:
        1. Validates state against Redis (CSRF check)
        2. Exchanges code for Google tokens
        3. Fetches Google profile, validates email is @esi-sba.dz
        4. Finds or creates the user in our DB
        5. Issues our own JWT tokens, sets HttpOnly cookies
        6. Redirects the browser to the React frontend dashboard

    The redirect carries a query param for the frontend:
        ?new=true  → first-time login (show welcome message)
        ?new=false → returning user

    This is a browser redirect (not a JSON API call), so the response
    is a 302 RedirectResponse, not JSON.
    """
    service = OAuthService(db)
    user, access_token, refresh_token, is_new_user = await service.handle_callback(
        code=code,
        state=state,
        ip_address=get_client_ip(request),
    )

    # Build redirect response to the frontend
    redirect_url = (
        f"{settings.FRONTEND_URL}/{user.role.value}?new={str(is_new_user).lower()}"
    )
    redirect_response = RedirectResponse(url=redirect_url, status_code=302)

    # Set auth cookies ON the redirect response (not on 'response')
    set_auth_cookies(redirect_response, access_token, refresh_token)

    return redirect_response
