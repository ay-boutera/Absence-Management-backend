import uuid
from datetime import date, time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import create_access_token
from app.main import app
from app.models import (
    Absence,
    AcademicYear,
    AcademicStudent,
    Module,
    PlanningSession,
    Salle,
    SectionEnum,
    SpecialityEnum,
)
from app.models import Admin, PasswordResetToken, Student, Teacher, UserRole

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


def fake_hash_password(password: str) -> str:
    return f"hashed::{password}"


def fake_verify_password(password: str, hashed_password: str) -> bool:
    return hashed_password == fake_hash_password(password)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as c:
        yield c


async def seed_reference_data() -> dict[str, uuid.UUID]:
    async with TestSessionLocal() as session:
        admin_user = Admin(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="a.admin@esi-sba.dz",
            hashed_password=fake_hash_password("Admin@1234"),
            is_active=True,
            department="Administration",
            admin_level="super",
        )
        teacher_user = Teacher(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="User",
            email="t.teacher@esi-sba.dz",
            hashed_password=fake_hash_password("Teacher@1234"),
            is_active=True,
            employee_id="EMP-001",
            specialization="Computer Science",
        )
        student_user = Student(
            id=uuid.uuid4(),
            first_name="Student",
            last_name="User",
            email="s.student@esi-sba.dz",
            hashed_password=fake_hash_password("Student@1234"),
            is_active=True,
            student_id="ST-001",
            program="INFO",
            level="L3",
            group="G1",
        )
        session.add_all([admin_user, teacher_user, student_user])

        session.add(Module(code="ALG101", nom="Algebra"))
        session.add(Salle(code="A101"))
        session.add(
            AcademicStudent(
                matricule="ACS-001",
                nom="Alpha",
                prenom="Student",
                filiere="INFO",
                niveau="L3",
                groupe="G1",
                email="alpha.student@esi-sba.dz",
            )
        )
        await session.flush()

        planning = PlanningSession(
            year=AcademicYear.CS_2,
            section=SectionEnum.A,
            speciality=SpecialityEnum.ISI,
            semester="S1",
            day="Dimanche",
            time_start=time(8, 0),
            time_end=time(10, 0),
            type="Cours",
            subject="Algebra",
            room="A101",
            group="G1",
            teacher_id=teacher_user.id,
        )
        session.add(planning)
        await session.flush()

        session.add(
            Absence(
                student_matricule="ACS-001",
                planning_session_id=planning.id,
                statut_justificatif="en_attente",
            )
        )
        await session.commit()

        return {
            "admin_id": admin_user.id,
            "teacher_id": teacher_user.id,
            "student_id": student_user.id,
        }


@pytest.mark.asyncio
async def test_swagger_documented_endpoints_smoke(client: AsyncClient):
    seeded = await seed_reference_data()
    oauth_mock_user = Student(
        id=seeded["student_id"],
        first_name="Student",
        last_name="User",
        email="s.student@esi-sba.dz",
        hashed_password=None,
        is_active=True,
        student_id="ST-OAUTH",
        program="INFO",
        level="L1",
        group="G1",
    )

    with (
        patch(
            "app.services.auth_service.send_password_reset_email",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("app.services.auth_service.hash_password", side_effect=fake_hash_password),
        patch(
            "app.services.auth_service.verify_password",
            side_effect=fake_verify_password,
        ),
        patch(
            "app.services.oauth_service.OAuthService.get_authorization_url",
            new_callable=AsyncMock,
            return_value="https://accounts.google.com/o/oauth2/v2/auth?state=fake",
        ),
        patch(
            "app.services.oauth_service.OAuthService.handle_callback",
            new_callable=AsyncMock,
            return_value=(
                oauth_mock_user,
                "oauth_access_token",
                "oauth_refresh_token",
                True,
            ),
        ),
        patch("app.main._get_alembic_head_revision", return_value="test-revision"),
        patch(
            "app.main._get_database_revision",
            new_callable=AsyncMock,
            return_value="test-revision",
        ),
        patch(
            "app.main._get_critical_tables_status",
            new_callable=AsyncMock,
            return_value={
                "admins": True,
                "teachers": True,
                "student_users": True,
                "audit_logs": True,
                "password_reset_tokens": True,
                "import_history": True,
                "import_export_logs": True,
            },
        ),
    ):
        root_response = await client.get("/")
        assert root_response.status_code == 200

        health_response = await client.get("/health")
        assert health_response.status_code == 200

        db_health_response = await client.get("/health/db")
        assert db_health_response.status_code == 200

        db_tables_health_response = await client.get("/health/db/tables")
        assert db_tables_health_response.status_code == 200

        docs_response = await client.get("/api/v1/docs")
        assert docs_response.status_code == 200

        openapi_response = await client.get("/api/v1/openapi.json")
        assert openapi_response.status_code == 200
        assert openapi_response.json()["paths"]

        login_admin = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "a.admin@esi-sba.dz", "password": "Admin@1234"},
        )
        assert login_admin.status_code == 200
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token
        csrf_headers = {"X-CSRF-Token": csrf_token}

        auth_me = await client.get("/api/v1/auth/me")
        assert auth_me.status_code == 200

        accounts_me = await client.get("/api/v1/accounts/me")
        assert accounts_me.status_code == 200

        refresh = await client.post("/api/v1/auth/refresh")
        assert refresh.status_code == 200
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token
        csrf_headers = {"X-CSRF-Token": csrf_token}

        change_password = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "Admin@1234",
                "new_password": "AdminChanged1!",
                "confirm_password": "AdminChanged1!",
            },
            headers=csrf_headers,
        )
        assert change_password.status_code == 200

        relogin_admin = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "a.admin@esi-sba.dz",
                "password": "AdminChanged1!",
            },
        )
        assert relogin_admin.status_code == 200
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token
        csrf_headers = {"X-CSRF-Token": csrf_token}

        reset_request = await client.post(
            "/api/v1/auth/reset-password",
            json={"email": "s.student@esi-sba.dz"},
        )
        assert reset_request.status_code == 200

        async with TestSessionLocal() as session:
            reset_token_result = await session.execute(
                select(PasswordResetToken)
                .where(PasswordResetToken.user_id == seeded["student_id"])
                .order_by(PasswordResetToken.created_at.desc())
            )
            reset_token = reset_token_result.scalars().first()

        assert reset_token is not None
        reset_confirm = await client.post(
            "/api/v1/auth/reset-password/confirm",
            json={
                "token": reset_token.token,
                "new_password": "StudentChanged1!",
                "confirm_password": "StudentChanged1!",
            },
        )
        assert reset_confirm.status_code == 200

        with patch("app.routers.auth._build_signed_oauth_state", return_value="fake-state"):
            oauth_url = await client.get("/api/v1/auth/google")
        assert oauth_url.status_code == 200

        oauth_callback = await client.get(
            "/api/v1/auth/google/callback",
            params={"code": "fake-code", "state": "fake-state"},
            follow_redirects=False,
        )
        assert oauth_callback.status_code == 302

        relogin_admin = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "a.admin@esi-sba.dz", "password": "AdminChanged1!"},
        )
        assert relogin_admin.status_code == 200
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token
        csrf_headers = {"X-CSRF-Token": csrf_token}

        create_student = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.new@esi-sba.dz",
                "password": "StudentNew1!",
                "first_name": "Student",
                "last_name": "New",
                "student_id": "ST-NEW-1",
                "program": "INFO",
                "level": "L1",
                "group": "G1",
            },
        )
        assert create_student.status_code == 201
        created_student_id = create_student.json()["id"]

        create_teacher = await client.post(
            "/api/v1/accounts/teachers",
            json={
                "email": "t.new@esi-sba.dz",
                "password": "TeacherNew1!",
                "first_name": "Teacher",
                "last_name": "New",
                "employee_id": "EMP-NEW-1",
                "specialization": "Mathematics",
            },
        )
        assert create_teacher.status_code == 201
        created_teacher_id = create_teacher.json()["id"]

        create_admin = await client.post(
            "/api/v1/accounts/admins",
            json={
                "email": "n.admin@esi-sba.dz",
                "password": "AdminNew1!",
                "first_name": "Nadia",
                "last_name": "Admin",
                "department": "Pedagogy",
                "admin_level": "regular",
            },
        )
        assert create_admin.status_code == 201

        list_accounts = await client.get("/api/v1/accounts/")
        assert list_accounts.status_code == 200

        list_students = await client.get("/api/v1/accounts/students")
        assert list_students.status_code == 200

        list_teachers = await client.get("/api/v1/accounts/teachers")
        assert list_teachers.status_code == 200

        list_admins = await client.get("/api/v1/accounts/admins")
        assert list_admins.status_code == 200

        get_by_id = await client.get(f"/api/v1/accounts/{created_teacher_id}")
        assert get_by_id.status_code == 200

        update_account = await client.patch(
            f"/api/v1/accounts/teachers/{created_teacher_id}",
            json={
                "first_name": "TeacherUpdated",
                "specialization": "Physics",
            },
        )
        assert update_account.status_code == 200

        update_status = await client.patch(
            f"/api/v1/accounts/{created_student_id}/status",
            json={"is_active": False},
        )
        assert update_status.status_code == 200

        admin_bearer = create_access_token(
            {"sub": str(seeded["admin_id"]), "role": UserRole.ADMIN.value}
        )
        admin_bearer_headers = {"Authorization": f"Bearer {admin_bearer}"}

        import_students = await client.post(
            "/api/v1/import/students",
            files={
                "file": (
                    "students.csv",
                    (
                        "matricule,nom,prenom,filiere,niveau,groupe,email\n"
                        "ACS-002,Beta,Student,INFO,L2,G2,beta.student@esi-sba.dz\n"
                    ),
                    "text/csv",
                )
            },
            headers=admin_bearer_headers,
        )
        assert import_students.status_code == 200

        list_students_after_import = await client.get("/api/v1/accounts/students")
        assert list_students_after_import.status_code == 200
        imported_emails = {item["email"] for item in list_students_after_import.json()}
        assert "beta.student@esi-sba.dz" in imported_emails

        import_planning = await client.post(
            "/api/v1/import/planning",
            files={
                "file": (
                    "planning.csv",
                    (
                        "year,section,speciality,semester,day,time_start,time_end,type,subject,teacher,room,group\n"
                        "2CS,A,ISI,S1,Dimanche,08:00,10:00,Cours,Algebra,EMP-001,A101,G1\n"
                    ),
                    "text/csv",
                )
            },
            headers=admin_bearer_headers,
        )
        assert import_planning.status_code == 200

        export_absences = await client.get(
            "/api/v1/export/absences?page=1&page_size=100",
            headers=admin_bearer_headers,
        )
        assert export_absences.status_code == 200
        assert export_absences.headers["content-type"].startswith("text/csv")

        history_admin = await client.get(
            "/api/v1/import-export/history?page=1&page_size=20",
            headers=admin_bearer_headers,
        )
        assert history_admin.status_code == 200

        teacher_bearer = create_access_token(
            {"sub": str(seeded["teacher_id"]), "role": UserRole.TEACHER.value}
        )
        teacher_bearer_headers = {"Authorization": f"Bearer {teacher_bearer}"}
        history_teacher = await client.get(
            "/api/v1/import-export/history?page=1&page_size=20",
            headers=teacher_bearer_headers,
        )
        assert history_teacher.status_code == 200

        logout = await client.post("/api/v1/auth/logout", headers=csrf_headers)
        assert logout.status_code == 200
