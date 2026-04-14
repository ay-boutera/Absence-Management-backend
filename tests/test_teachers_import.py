import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import create_access_token
from app.main import app
from app.models import Admin, ImportHistory, ImportType, Teacher, UserRole

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
        user = Admin(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="admin.user@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            department="Administration",
            admin_level="super",
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
            email="teacher.user@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            employee_id="EMP-TEACH-1",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer_headers(user: Admin | Teacher) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_import_teachers_happy_path_creates_all_and_logs_history(
    client: AsyncClient,
    admin_user: Admin,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-001,Doe,John,john.doe@esi-sba.dz,MCF,INFO\n"
        "T-002,Smith,Jane,jane.smith@esi-sba.dz,PR,Math\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 2
    assert data["errors"] == 0
    assert data["error_report"] == []
    assert data["history_id"]

    async with TestSessionLocal() as session:
        teachers_result = await session.execute(
            select(Teacher).where(Teacher.employee_id.in_(["T-001", "T-002"]))
        )
        teachers = teachers_result.scalars().all()
        assert len(teachers) == 2

        teacher_result = await session.execute(
            select(Teacher).where(Teacher.email == "jane.smith@esi-sba.dz")
        )
        teacher = teacher_result.scalar_one_or_none()
        assert teacher is not None
        assert teacher.first_name == "Jane"
        assert teacher.last_name == "Smith"
        assert teacher.role == UserRole.TEACHER

        history_result = await session.execute(select(ImportHistory))
        history = history_result.scalars().all()
        assert len(history) == 1
        assert history[0].import_type == ImportType.TEACHERS
        assert history[0].success_count == 2


@pytest.mark.asyncio
async def test_import_teachers_missing_column_returns_error_report(
    client: AsyncClient,
    admin_user: Admin,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade\n"
        "T-001,Doe,John,john.doe@esi-sba.dz,MCF\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["error"] == "Format CSV invalide"
    assert "Colonnes manquantes" in data["detail"]["detail"]


@pytest.mark.asyncio
async def test_import_teachers_duplicate_id_in_same_file_treated_as_update(
    client: AsyncClient,
    admin_user: Admin,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-900,Alpha,One,alpha.one@esi-sba.dz,MCF,INFO\n"
        "T-900,Alpha,Two,alpha.two@esi-sba.dz,PR,Math\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] >= 1
    assert any(
        error["field"] == "id_enseignant" and error["line"] == 2
        for error in data["error_report"]
    )

    async with TestSessionLocal() as session:
        teacher_result = await session.execute(
            select(Teacher).where(Teacher.employee_id == "T-900")
        )
        assert teacher_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_import_teachers_invalid_email_row_rejected(
    client: AsyncClient,
    admin_user: Admin,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-700,Bad,Email,invalid-email,MCF,INFO\n"
        "T-701,Good,Email,good.email@esi-sba.dz,MCF,INFO\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] >= 1
    assert any(
        error["line"] == 1
        and error["field"] == "email"
        and error["reason"] == "Format email invalide"
        for error in data["error_report"]
    )


@pytest.mark.asyncio
async def test_import_teachers_detects_existing_db_duplicates_and_aborts(
    client: AsyncClient,
    admin_user: Admin,
):
    async with TestSessionLocal() as session:
        existing_teacher = Teacher(
            id=uuid.uuid4(),
            first_name="Old",
            last_name="Teacher",
            email="existing.teacher@esi-sba.dz",
            hashed_password=None,
            is_active=True,
            employee_id="EMP-005",
            specialization="MCF | INFO",
        )
        session.add(existing_teacher)
        await session.commit()

    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "EMP-005,Doe,John,new.teacher@esi-sba.dz,MCF,INFO\n"
        "EMP-006,Doe,Jane,existing.teacher@esi-sba.dz,PR,Math\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] >= 1
    assert any("Email déjà utilisé" in error["reason"] for error in data["error_report"])

    async with TestSessionLocal() as session:
        teacher_result = await session.execute(
            select(Teacher).where(Teacher.employee_id == "EMP-006")
        )
        assert teacher_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_import_teachers_forbidden_for_non_admin(
    client: AsyncClient,
    teacher_user: Teacher,
):
    csv_content = (
        "id_enseignant,nom,prenom,email,grade,departement\n"
        "T-001,Doe,John,john.doe@esi-sba.dz,MCF,INFO\n"
    )

    response = await client.post(
        "/api/v1/import/teachers",
        files={"file": ("teachers.csv", csv_content, "text/csv")},
        headers=bearer_headers(teacher_user),
    )

    assert response.status_code == 403
