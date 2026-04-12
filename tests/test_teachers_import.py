import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import create_access_token
from app.main import app
from app.models.user import Account, Teacher, UserRole

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
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def admin_user():
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="admin.user@esi-sba.dz",
            hashed_password="unused",
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer_headers(user: Account) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_import_teachers_happy_path_create_and_update(
    client: AsyncClient,
    admin_user: Account,
):
    existing_teacher_account_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        existing_account = Account(
            id=existing_teacher_account_id,
            first_name="Old",
            last_name="Teacher",
            email="old.teacher@esi-sba.dz",
            hashed_password=None,
            role=UserRole.TEACHER,
            is_active=True,
        )
        session.add(existing_account)
        await session.flush()

        session.add(
            Teacher(
                user_id=existing_teacher_account_id,
                employee_id="T-100",
                specialization="OldGrade | OldDepartment",
            )
        )
        await session.commit()

    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-001,Doe,John,john.doe@esi-sba.dz,MCF,INFO\n"
        "T-100,Smith,Jane,jane.smith@esi-sba.dz,PR,Math\n"
    )

    response = await client.post(
        "/api/v1/teachers/import-csv",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1
    assert data["updated"] == 1
    assert data["errors"] == []

    async with TestSessionLocal() as session:
        new_teacher_result = await session.execute(
            select(Teacher).where(Teacher.employee_id == "T-001")
        )
        assert new_teacher_result.scalar_one_or_none() is not None

        updated_account_result = await session.execute(
            select(Account).where(Account.email == "jane.smith@esi-sba.dz")
        )
        updated_account = updated_account_result.scalar_one_or_none()
        assert updated_account is not None
        assert updated_account.first_name == "Jane"
        assert updated_account.last_name == "Smith"


@pytest.mark.asyncio
async def test_import_teachers_missing_column_returns_error_report(
    client: AsyncClient,
    admin_user: Account,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade\n"
        "T-001,Doe,John,john.doe@esi-sba.dz,MCF\n"
    )

    response = await client.post(
        "/api/v1/teachers/import-csv",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 0
    assert data["updated"] == 0
    assert len(data["errors"]) == 1
    assert data["errors"][0]["column"] == "departement"
    assert "Missing required column" in data["errors"][0]["reason"]


@pytest.mark.asyncio
async def test_import_teachers_duplicate_id_in_same_file_treated_as_update(
    client: AsyncClient,
    admin_user: Account,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-900,Alpha,One,alpha.one@esi-sba.dz,MCF,INFO\n"
        "T-900,Alpha,Two,alpha.two@esi-sba.dz,PR,Math\n"
    )

    response = await client.post(
        "/api/v1/teachers/import-csv",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1
    assert data["updated"] == 1
    assert data["errors"] == []

    async with TestSessionLocal() as session:
        teacher_result = await session.execute(
            select(Teacher).where(Teacher.employee_id == "T-900")
        )
        teacher = teacher_result.scalar_one_or_none()
        assert teacher is not None

        account = await session.get(Account, teacher.user_id)
        assert account is not None
        assert account.email == "alpha.two@esi-sba.dz"
        assert account.first_name == "Two"


@pytest.mark.asyncio
async def test_import_teachers_invalid_email_row_rejected(
    client: AsyncClient,
    admin_user: Account,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-700,Bad,Email,invalid-email,MCF,INFO\n"
        "T-701,Good,Email,good.email@esi-sba.dz,MCF,INFO\n"
    )

    response = await client.post(
        "/api/v1/teachers/import-csv",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1
    assert data["updated"] == 0
    assert len(data["errors"]) == 1
    assert data["errors"][0]["line"] == 2
    assert data["errors"][0]["column"] == "email"
    assert data["errors"][0]["reason"] == "Invalid email format"