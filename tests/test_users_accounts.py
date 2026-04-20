import uuid
from contextlib import nullcontext

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import hash_password
from app.main import app
from app.models.audit_log import AuditLog
from app.models import Admin, Student, Teacher, UserRole

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def mock_redis():
    return nullcontext()


def fixture_password(role: str, index: int) -> str:
    return f"{role.title()}Fixture{index}!"


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    app.dependency_overrides[get_db] = override_get_db
    required_tables = [
        Admin.__table__,
        Teacher.__table__,
        Student.__table__,
        AuditLog.__table__,
    ]

    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                bind=sync_conn,
                tables=required_tables,
            )
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.drop_all(
                bind=sync_conn,
                tables=required_tables,
            )
        )
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def admin_user():
    async with TestSessionLocal() as session:
        user = Admin(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="a.admin@esi-sba.dz",
            hashed_password=hash_password("Admin@1234"),
            is_active=True,
            department="Administration",
            admin_level="super",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def regular_admin_user():
    async with TestSessionLocal() as session:
        user = Admin(
            id=uuid.uuid4(),
            first_name="Regular",
            last_name="Admin",
            email="r.admin@esi-sba.dz",
            hashed_password=hash_password("Regular@1234"),
            is_active=True,
            department="Administration",
            admin_level="regular",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def teacher_user():
    async with TestSessionLocal() as session:
        user = Teacher(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="User",
            email="t.teacher@esi-sba.dz",
            hashed_password=hash_password("Teacher@1234"),
            is_active=True,
            employee_id="EMP-T-001",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def login(client: AsyncClient, identifier: str, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_first_super_admin_public_success(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/super-admins",
        json={
            "email": "first.super@esi-sba.dz",
            "password": "Super@1234",
            "first_name": "First",
            "last_name": "Super",
            "department": "Administration",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "admin"

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(Admin).where(Admin.id == uuid.UUID(payload["id"]))
        )
        created_profile = result.scalar_one_or_none()
        assert created_profile is not None
        assert created_profile.admin_level == "super"


@pytest.mark.asyncio
async def test_create_super_admin_blocked_when_one_exists(client: AsyncClient, admin_user: Admin):
    response = await client.post(
        "/api/v1/accounts/super-admins",
        json={
            "email": "second.super@esi-sba.dz",
            "password": "Super@1234",
            "first_name": "Second",
            "last_name": "Super",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Super admin already exists."


@pytest.mark.asyncio
async def test_create_student_account_success(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        response = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.one@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "One",
                "student_id": "ST-001",
                "program": "INFO",
                "level": "L3",
                "group": "G1",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "student"
    assert payload["email"] == "s.one@esi-sba.dz"

    async with TestSessionLocal() as session:
        result = await session.execute(select(Student).where(Student.student_id == "ST-001"))
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_create_teacher_account_success(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        response = await client.post(
            "/api/v1/accounts/teachers",
            json={
                "email": "t.one@esi-sba.dz",
                "password": "Teacher@123",
                "first_name": "Teacher",
                "last_name": "One",
                "employee_id": "EMP-001",
                "specialization": "Mathematics",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "teacher"
    assert payload["email"] == "t.one@esi-sba.dz"

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(Teacher).where(Teacher.employee_id == "EMP-001")
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_create_admin_account_success(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        response = await client.post(
            "/api/v1/accounts/admins",
            json={
                "email": "n.admin@esi-sba.dz",
                "password": fixture_password("admin", 2),
                "first_name": "New",
                "last_name": "Admin",
                "department": "Pedagogy",
                "admin_level": "super",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "admin"
    assert payload["email"] == "n.admin@esi-sba.dz"


@pytest.mark.asyncio
async def test_regular_admin_cannot_create_admin(
    client: AsyncClient, regular_admin_user: Admin
):
    await login(client, "r.admin@esi-sba.dz", "Regular@1234")

    with mock_redis():
        response = await client.post(
            "/api/v1/accounts/admins",
            json={
                "email": "denied.admin@esi-sba.dz",
                "password": fixture_password("admin", 3),
                "first_name": "Denied",
                "last_name": "Admin",
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_teacher_cannot_create_accounts(client: AsyncClient, teacher_user: Teacher):
    await login(client, "t.teacher@esi-sba.dz", "Teacher@1234")

    with mock_redis():
        response = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.two@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "Two",
                "student_id": "ST-002",
                "program": "INFO",
                "level": "L2",
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_student_id_returns_400(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        first = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.first@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "First",
                "student_id": "ST-777",
                "program": "INFO",
                "level": "L1",
            },
        )
        second = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.second@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "Second",
                "student_id": "ST-777",
                "program": "INFO",
                "level": "L1",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 400
    assert second.json()["detail"] == "Student ID is already registered."


@pytest.mark.asyncio
async def test_get_admins_returns_admin_accounts_only(
    client: AsyncClient,
    admin_user: Admin,
):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.three@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "Three",
                "student_id": "ST-003",
                "program": "INFO",
                "level": "L3",
            },
        )
        response = await client.get("/api/v1/accounts/admins")

    assert response.status_code == 200
    admins = response.json()
    assert len(admins) >= 1
    assert all(account["role"] == "admin" for account in admins)


@pytest.mark.asyncio
async def test_get_account_by_id_success(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        response = await client.get(f"/api/v1/accounts/{admin_user.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(admin_user.id)
    assert payload["email"] == "a.admin@esi-sba.dz"


@pytest.mark.asyncio
async def test_get_account_by_id_not_found(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")
    missing_id = uuid.uuid4()

    with mock_redis():
        response = await client.get(f"/api/v1/accounts/{missing_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found."


@pytest.mark.asyncio
async def test_update_teacher_account_success(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        create_response = await client.post(
            "/api/v1/accounts/teachers",
            json={
                "email": "t.two@esi-sba.dz",
                "password": "Teacher@123",
                "first_name": "Teacher",
                "last_name": "Two",
                "employee_id": "EMP-202",
                "specialization": "Mathematics",
            },
        )
        teacher_id = create_response.json()["id"]

        update_response = await client.patch(
            f"/api/v1/accounts/teachers/{teacher_id}",
            json={
                "first_name": "UpdatedTeacher",
                "specialization": "Computer Science",
            },
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert update_response.json()["first_name"] == "UpdatedTeacher"

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(Teacher).where(Teacher.id == uuid.UUID(teacher_id))
        )
        teacher_profile = result.scalar_one_or_none()
        assert teacher_profile is not None
        assert teacher_profile.specialization == "Computer Science"


@pytest.mark.asyncio
async def test_update_account_duplicate_email_returns_400(
    client: AsyncClient,
    admin_user: Admin,
):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        first = await client.post(
            "/api/v1/accounts/admins",
            json={
                "email": "z.admin@esi-sba.dz",
                "password": fixture_password("admin", 1),
                "first_name": "Zed",
                "last_name": "Admin",
            },
        )
        second = await client.post(
            "/api/v1/accounts/admins",
            json={
                "email": "y.admin@esi-sba.dz",
                "password": fixture_password("admin", 2),
                "first_name": "Yas",
                "last_name": "Admin",
            },
        )
        second_id = second.json()["id"]

        update = await client.patch(
            f"/api/v1/accounts/admins/{second_id}",
            json={"email": "z.admin@esi-sba.dz"},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert update.status_code == 400
    assert update.json()["detail"] == "Email is already registered."


@pytest.mark.asyncio
async def test_update_account_status_deactivate_and_activate(
    client: AsyncClient,
    admin_user: Admin,
):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        created = await client.post(
            "/api/v1/accounts/students",
            json={
                "email": "s.four@esi-sba.dz",
                "password": "Student@123",
                "first_name": "Student",
                "last_name": "Four",
                "student_id": "ST-004",
                "program": "INFO",
                "level": "L2",
            },
        )
        student_id = created.json()["id"]

        deactivate = await client.patch(
            f"/api/v1/accounts/{student_id}/status",
            json={"is_active": False},
        )
        activate = await client.patch(
            f"/api/v1/accounts/{student_id}/status",
            json={"is_active": True},
        )

    assert created.status_code == 201
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False
    assert activate.status_code == 200
    assert activate.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(client: AsyncClient, admin_user: Admin):
    await login(client, "a.admin@esi-sba.dz", "Admin@1234")

    with mock_redis():
        response = await client.patch(
            f"/api/v1/accounts/{admin_user.id}/status",
            json={"is_active": False},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "You cannot deactivate your own account."
