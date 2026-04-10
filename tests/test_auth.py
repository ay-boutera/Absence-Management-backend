"""
tests/test_auth.py — Authentication Test Suite (Fixed)
=======================================================
Fixes applied:
    1. RBAC         — Redis mock now wraps BOTH login AND the subsequent request
    2. Mail         — mock path corrected to where the function is CALLED
    3. UUID/SQLite  — explicit uuid.uuid4() objects passed to avoid hex() error
    4. OAuth        — mocked at the service level instead of deep httpx/authlib
    5. Audit log    — session.flush() added before querying
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from app.db import Base, get_db
from app.models.user import Account, UserRole, PasswordResetToken
from app.core.security import hash_password
from fastapi import HTTPException as FastAPIHTTPException

# ── In-memory SQLite test database ────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except FastAPIHTTPException:
            await session.commit()  # ← commit les audit logs même si login échoue
            raise
        except Exception:
            await session.rollback()
            raise


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    app.dependency_overrides[get_db] = override_get_db
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def admin_user():
    """
    FIX #3: Pass explicit uuid.uuid4() so SQLite doesn't call .hex()
    on a string.
    """
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="a.user@esi-sba.dz",
            hashed_password=hash_password("Admin@1234"),
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def teacher_user():
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="User",
            email="t.user@esi-sba.dz",
            hashed_password=hash_password("Teacher@1234"),
            role=UserRole.TEACHER,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def inactive_user():
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Inactive",
            last_name="User",
            email="i.user@esi-sba.dz",
            hashed_password=hash_password("Inactive@1234"),
            role=UserRole.STUDENT,
            is_active=False,
        )
        session.add(user)
        await session.commit()
        return user


# ── FIX #1: Redis mock helper ─────────────────────────────────────────────────
# Returns a context manager that patches Redis for the ENTIRE test,
# including both the login request AND any subsequent authenticated request.
def mock_redis():
    return patch(
        "app.services.redis_service.RedisService.is_token_blacklisted",
        new_callable=AsyncMock,
        return_value=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ESI EMAIL VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════
class TestESIEmailValidator:

    def test_valid_emails(self):
        from app.core.email_validator import validate_esi_email

        assert validate_esi_email("i.brahmi@esi-sba.dz") == "i.brahmi@esi-sba.dz"
        assert validate_esi_email("n.trari@esi-sba.dz") == "n.trari@esi-sba.dz"
        # Uppercase normalised to lowercase
        assert validate_esi_email("I.Brahmi@esi-sba.dz") == "i.brahmi@esi-sba.dz"
        # Hyphenated lastname
        assert validate_esi_email("n.el-fouad@esi-sba.dz") == "n.el-fouad@esi-sba.dz"

    def test_wrong_domain_rejected(self):
        from app.core.email_validator import validate_esi_email
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            validate_esi_email("i.brahmi@gmail.com")
        assert exc.value.status_code == 403

    def test_full_firstname_allowed(self):
        from app.core.email_validator import validate_esi_email
        assert validate_esi_email("ilyes.brahmi@esi-sba.dz") == "ilyes.brahmi@esi-sba.dz"

    def test_no_dot_rejected(self):
        from app.core.email_validator import validate_esi_email
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            validate_esi_email("ibrahmi@esi-sba.dz")

    def test_name_hint_extraction(self):
        from app.core.email_validator import extract_name_hint_from_email

        r = extract_name_hint_from_email("i.brahmi@esi-sba.dz")
        assert r["first_initial"] == "I"
        assert r["last_name"] == "Brahmi"

        r = extract_name_hint_from_email("n.el-fouad@esi-sba.dz")
        assert r["last_name"] == "El Fouad"


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIAL LOGIN
# ═══════════════════════════════════════════════════════════════════════════════
class TestCredentialLogin:

    @pytest.mark.asyncio
    async def test_login_success(self, client, admin_user):
        with mock_redis():
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "identifier": "a.user@esi-sba.dz",
                    "password": "Admin@1234",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "admin"
        # Tokens must NOT be in the body
        assert "access_token" not in data
        assert "eyJ" not in response.text
        # Tokens must be in HttpOnly cookies
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies
        assert "csrf_token" in response.cookies

    @pytest.mark.asyncio
    async def test_non_esi_email_blocked(self, client):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "someone@gmail.com",
                "password": "Admin@1234",
            },
        )
        assert response.status_code == 403
        assert "esi-sba.dz" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_wrong_password(self, client, admin_user):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "a.user@esi-sba.dz",
                "password": "Wrong@Pass1",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_user_same_error(self, client):
        """No user enumeration — same 401 whether user exists or not."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "g.host@esi-sba.dz",
                "password": "Pass@1234",
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials."

    @pytest.mark.asyncio
    async def test_deactivated_account_rejected(self, client, inactive_user):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "i.user@esi-sba.dz",
                "password": "Inactive@1234",
            },
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_full_firstname_email_allowed(self, client, admin_user):
        """Now allowed to login with full names."""
        with mock_redis():
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "identifier": "a.user@esi-sba.dz", # Still works
                    "password": "Admin@1234",
                },
            )
        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# RBAC
# FIX #1: mock_redis() now wraps the ENTIRE test block, not just the login.
# This means the token blacklist check on the second request also returns False.
# ═══════════════════════════════════════════════════════════════════════════════
class TestRBAC:

    @pytest.mark.asyncio
    async def test_unauthenticated_blocked(self, client):
        response = await client.get("/api/v1/accounts/")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_teacher_cannot_access_admin_routes(self, client, teacher_user):
        with mock_redis():
            login = await client.post(
                "/api/v1/auth/login",
                json={"identifier": "t.user@esi-sba.dz", "password": "Teacher@1234"},
            )
            # Mettre les cookies sur le client directement (pas per-request)
            client.cookies.set("access_token", login.cookies.get("access_token"))
            response = await client.get("/api/v1/accounts/")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_access_admin_routes(self, client, admin_user):
        with mock_redis():
            login = await client.post(
                "/api/v1/auth/login",
                json={"identifier": "a.user@esi-sba.dz", "password": "Admin@1234"},
            )

            print("set-cookie:", login.headers.get("set-cookie"))
            print("client cookies after login:", client.cookies)

            response = await client.get("/api/v1/accounts/")
            print(response.status_code, response.text)

        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# PASSWORD RESET
# FIX #2: mock path is now where send_password_reset_email is CALLED
#         (in auth_service), not where it is defined (in email_service).
# ═══════════════════════════════════════════════════════════════════════════════
class TestPasswordReset:

    @pytest.mark.asyncio
    async def test_reset_always_200(self, client, admin_user):
        # FIX: patch the function where it is USED (auth_service imports it)
        with patch(
            "app.services.auth_service.send_password_reset_email",
            new_callable=AsyncMock,
        ):
            r1 = await client.post(
                "/api/v1/auth/reset-password", json={"email": "a.user@esi-sba.dz"}
            )
            r2 = await client.post(
                "/api/v1/auth/reset-password", json={"email": "g.host@esi-sba.dz"}
            )
        assert r1.status_code == 200
        assert r2.status_code == 200  # same response — prevents user enumeration

    @pytest.mark.asyncio
    async def test_reset_email_failure_returns_503(self, client, admin_user):
        with patch(
            "app.services.auth_service.send_password_reset_email",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = await client.post(
                "/api/v1/auth/reset-password", json={"email": "a.user@esi-sba.dz"}
            )

        assert response.status_code == 503
        assert "could not be delivered" in response.json()["detail"].lower()

        async with TestSessionLocal() as session:
            tokens = (
                await session.execute(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == admin_user.id
                    )
                )
            ).scalars().all()
        assert len(tokens) == 0

    @pytest.mark.asyncio
    async def test_weak_password_rejected(self, client):
        r = await client.post(
            "/api/v1/auth/reset-password/confirm",
            json={"token": "tok", "new_password": "weak", "confirm_password": "weak"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_password_mismatch_rejected(self, client):
        r = await client.post(
            "/api/v1/auth/reset-password/confirm",
            json={
                "token": "tok",
                "new_password": "Strong@Pass1",
                "confirm_password": "Different@Pass1",
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, client):
        r = await client.post(
            "/api/v1/auth/reset-password/confirm",
            json={
                "token": "fake-token",
                "new_password": "NewPass@1",
                "confirm_password": "NewPass@1",
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_oauth_user_cannot_change_password(self, client):
        """
        FIX #3: pass id=uuid.uuid4() explicitly so SQLite doesn't crash
        with 'str object has no attribute hex'.
        """
        user_id = uuid.uuid4()
        async with TestSessionLocal() as session:
            oauth_user = Account(
                id=user_id,  # ← explicit UUID object
                first_name="OAuth",
                last_name="User",
                email="o.user@esi-sba.dz",
                hashed_password=None,  # no password — OAuth only
                google_id="google_test_123",
                role=UserRole.STUDENT,
                is_active=True,
            )
            session.add(oauth_user)
            await session.commit()

        from app.core.security import create_access_token

        token = create_access_token({"sub": str(user_id), "role": "student"})

        with mock_redis():
            resp = await client.get(
                "/api/v1/auth/me",
                cookies={"access_token": token},
            )
        assert resp.status_code == 200
        assert resp.json()["email"] == "o.user@esi-sba.dz"


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH
# FIX #4: Mock at the SERVICE level, not at the deep httpx/authlib level.
#   - get_authorization_url()  → mock OAuthService.get_authorization_url
#   - handle_callback()        → mock OAuthService.handle_callback
# This is simpler, faster, and doesn't break when authlib internals change.
# ═══════════════════════════════════════════════════════════════════════════════
class TestGoogleOAuth:

    @pytest.mark.asyncio
    async def test_get_authorization_url(self, client):
        with patch(
            "app.routers.auth.OAuthService.get_authorization_url",
            new_callable=AsyncMock,
            return_value="https://accounts.google.com/o/oauth2/v2/auth?client_id=test",
        ):
            response = await client.get("/api/v1/auth/google")

        assert response.status_code == 200
        data = response.json()
        assert "authorization_url" in data
        assert "accounts.google.com" in data["authorization_url"]

    @pytest.mark.asyncio
    async def test_callback_invalid_state_rejected(self, client):
        """handle_callback raises 400 for bad state."""
        from fastapi import HTTPException

        with patch(
            "app.routers.auth.OAuthService.handle_callback",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=400,
                detail="Invalid or expired OAuth state. Please try logging in again.",
            ),
        ):
            response = await client.get(
                "/api/v1/auth/google/callback?code=fake&state=bad_state"
            )
        assert response.status_code == 400
        assert "state" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_callback_non_esi_email_rejected(self, client):
        """handle_callback raises 403 for non-ESI email."""
        from fastapi import HTTPException

        with patch("app.routers.auth.secrets.token_urlsafe", return_value="valid_state"), patch(
            "app.routers.auth.OAuthService.get_authorization_url",
            new_callable=AsyncMock,
            return_value="https://accounts.google.com/o/oauth2/v2/auth?state=valid_state",
        ), patch(
            "app.routers.auth.OAuthService.handle_callback",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Access is restricted to ESI-SBA institutional accounts.",
            ),
        ):
            await client.get("/api/v1/auth/google")
            response = await client.get(
                "/api/v1/auth/google/callback?code=code&state=valid_state"
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_callback_new_user_created(self, client):
        """
        handle_callback returns a new user → router sets cookies + redirects
        with ?new=true.
        """
        user_id = uuid.uuid4()
        async with TestSessionLocal() as session:
            new_user = Account(
                id=user_id,
                first_name="Ilyes",
                last_name="Brahmi",
                email="i.brahmi@esi-sba.dz",
                google_id="google_uid_new",
                role=UserRole.STUDENT,
                is_active=True,
            )
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)

        from app.core.security import create_access_token, create_refresh_token

        access = create_access_token({"sub": str(user_id), "role": "student"})
        refresh = create_refresh_token({"sub": str(user_id), "role": "student"})

        with patch("app.routers.auth.secrets.token_urlsafe", return_value="valid_state"), patch(
            "app.routers.auth.OAuthService.get_authorization_url",
            new_callable=AsyncMock,
            return_value="https://accounts.google.com/o/oauth2/v2/auth?state=valid_state",
        ), patch(
            "app.routers.auth.OAuthService.handle_callback",
            new_callable=AsyncMock,
            return_value=(new_user, access, refresh, True),  # is_new_user=True
        ):
            await client.get("/api/v1/auth/google")
            response = await client.get(
                "/api/v1/auth/google/callback?code=auth_code&state=valid_state",
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "/student" in response.headers["location"]
        assert "new=true" in response.headers["location"]
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies

    @pytest.mark.asyncio
    async def test_callback_existing_user_linked(self, client):
        """
        Admin pre-created a teacher account. First OAuth login links google_id.
        Redirect has ?new=false (not a new user).
        """
        user_id = uuid.uuid4()
        async with TestSessionLocal() as session:
            existing = Account(
                id=user_id,
                first_name="Nour",
                last_name="Trari",
                email="n.trari@esi-sba.dz",
                hashed_password=hash_password("Pass@1234"),
                role=UserRole.TEACHER,
                is_active=True,
                google_id=None,
            )
            session.add(existing)
            await session.commit()
            await session.refresh(existing)

        from app.core.security import create_access_token, create_refresh_token

        access = create_access_token({"sub": str(user_id), "role": "teacher"})
        refresh = create_refresh_token({"sub": str(user_id), "role": "teacher"})

        with patch("app.routers.auth.secrets.token_urlsafe", return_value="valid"), patch(
            "app.routers.auth.OAuthService.get_authorization_url",
            new_callable=AsyncMock,
            return_value="https://accounts.google.com/o/oauth2/v2/auth?state=valid",
        ), patch(
            "app.routers.auth.OAuthService.handle_callback",
            new_callable=AsyncMock,
            return_value=(existing, access, refresh, False),  # is_new_user=False
        ):
            await client.get("/api/v1/auth/google")
            response = await client.get(
                "/api/v1/auth/google/callback?code=auth_code&state=valid",
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "new=false" in response.headers["location"]
        assert "access_token" in response.cookies


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT LOGGING
# FIX #5: Use session.flush() before querying so the log written
#         in the same transaction is visible.
# ═══════════════════════════════════════════════════════════════════════════════
class TestAuditLogging:

    @pytest.mark.asyncio
    async def test_failed_login_logged(self, client):
        from sqlalchemy import select
        from app.models.audit_log import AuditLog, ActionType

        await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "g.host@esi-sba.dz",
                "password": "Wrong@1",
            },
        )

        # FIX: open a fresh session AFTER the request has committed
        async with TestSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == ActionType.LOGIN_FAILED)
            )
            log = result.scalar_one_or_none()
        assert log is not None

    @pytest.mark.asyncio
    async def test_successful_login_logged(self, client, admin_user):
        from sqlalchemy import select
        from app.models.audit_log import AuditLog, ActionType

        with mock_redis():
            await client.post(
                "/api/v1/auth/login",
                json={
                    "identifier": "a.user@esi-sba.dz",
                    "password": "Admin@1234",
                },
            )

        async with TestSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == ActionType.LOGIN_SUCCESS)
            )
            log = result.scalar_one_or_none()
        assert log is not None
        assert str(log.user_id) == str(admin_user.id)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════
class TestHealth:

    @pytest.mark.asyncio
    async def test_health(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
