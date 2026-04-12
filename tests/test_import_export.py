import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import create_access_token
from app.main import app
from app.models.academic import (
    Absence,
    Module,
    PlanningSession,
    Salle,
    SessionType,
    Student as AcademicStudent,
)
from app.models.user import Account, Student as StudentProfile, UserRole

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


@pytest_asyncio.fixture
async def teacher_user():
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="One",
            email="teacher.one@esi-sba.dz",
            hashed_password="unused",
            role=UserRole.TEACHER,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_teacher_user():
    async with TestSessionLocal() as session:
        user = Account(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="Two",
            email="teacher.two@esi-sba.dz",
            hashed_password="unused",
            role=UserRole.TEACHER,
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
async def test_import_students_atomic_rejects_on_any_invalid_row(
    client: AsyncClient, admin_user: Account
):
    csv_content = (
        "matricule,nom,prenom,filiere,niveau,groupe,email\n"
        "ST001,Doe,John,INFO,L3,G1,john.doe@esi-sba.dz\n"
        "ST002,Doe,Jane,INFO,L3,G1,invalid-email\n"
        "ST003,Smith,Ana,INFO,L2,G2,ana.smith@esi-sba.dz\n"
    )

    response = await client.post(
        "/api/v1/import/students",
        files={"file": ("students.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 400
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] == 1
    assert len(data["error_report"]) == 1
    assert data["error_report"][0]["field"] == "email"
    assert data["error_report"][0]["line"] == 3
    assert data["error_report"][0]["row_data"]["matricule"] == "ST002"

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(AcademicStudent).where(
                AcademicStudent.matricule.in_(["ST001", "ST002", "ST003"])
            )
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_import_students_with_access_cookie(client: AsyncClient, admin_user: Account):
    csv_content = (
        "matricule,nom,prenom,filiere,niveau,groupe,email\n"
        "ST010,Cookie,Admin,INFO,L3,G1,cookie.admin@esi-sba.dz\n"
    )
    token = create_access_token({"sub": str(admin_user.id), "role": admin_user.role.value})
    client.cookies.set("access_token", token)

    response = await client.post(
        "/api/v1/import/students",
        files={"file": ("students.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert data["errors"] == 0

    async with TestSessionLocal() as session:
        academic_result = await session.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == "ST010")
        )
        academic_student = academic_result.scalar_one_or_none()
        assert academic_student is not None
        assert academic_student.email == "cookie.admin@esi-sba.dz"

        account_result = await session.execute(
            select(Account).where(Account.email == "cookie.admin@esi-sba.dz")
        )
        imported_account = account_result.scalar_one_or_none()
        assert imported_account is not None
        assert imported_account.role == UserRole.STUDENT
        assert imported_account.first_name == "Admin"
        assert imported_account.last_name == "Cookie"

        profile_result = await session.execute(
            select(StudentProfile).where(StudentProfile.user_id == imported_account.id)
        )
        imported_profile = profile_result.scalar_one_or_none()
        assert imported_profile is not None
        assert imported_profile.student_id == "ST010"
        assert imported_profile.program == "INFO"
        assert imported_profile.level == "L3"
        assert imported_profile.group == "G1"


@pytest.mark.asyncio
async def test_import_students_forbidden_for_teacher(client: AsyncClient, teacher_user: Account):
    csv_content = "matricule,nom,prenom,filiere,niveau,groupe,email\nST001,Doe,John,INFO,L3,G1,john.doe@esi-sba.dz\n"

    response = await client.post(
        "/api/v1/import/students",
        files={"file": ("students.csv", csv_content, "text/csv")},
        headers=bearer_headers(teacher_user),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_import_planning_with_referential_validation(
    client: AsyncClient,
    admin_user: Account,
    teacher_user: Account,
):
    async with TestSessionLocal() as session:
        session.add(Module(code="ALG101", nom="Algebra"))
        session.add(Salle(code="A101"))
        await session.commit()

    csv_content = (
        "id_seance,code_module,type_seance,date,heure_debut,heure_fin,salle,id_enseignant\n"
        f"S1,ALG101,cours,2026-04-01,08:00,10:00,A101,{teacher_user.id}\n"
        "S2,MISSING,TD,2026-04-01,10:00,12:00,A101,00000000-0000-0000-0000-000000000001\n"
    )

    response = await client.post(
        "/api/v1/import/planning",
        files={"file": ("planning.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert data["errors"] == 1
    assert "id_enseignant" in data["error_report"][0]["field"] or "code_module" in data["error_report"][0]["field"]


@pytest.mark.asyncio
async def test_export_absences_admin_and_teacher_scope(
    client: AsyncClient,
    admin_user: Account,
    teacher_user: Account,
    other_teacher_user: Account,
):
    async with TestSessionLocal() as session:
        student_one = AcademicStudent(
            matricule="ST100",
            nom="Alpha",
            prenom="One",
            filiere="INFO",
            niveau="L3",
            groupe="G1",
            email="alpha.one@esi-sba.dz",
        )
        student_two = AcademicStudent(
            matricule="ST200",
            nom="Beta",
            prenom="Two",
            filiere="INFO",
            niveau="L3",
            groupe="G2",
            email="beta.two@esi-sba.dz",
        )
        module = Module(code="ALG101", nom="Algebra")
        salle = Salle(code="A101")

        session.add_all([student_one, student_two, module, salle])
        await session.flush()

        own_session = PlanningSession(
            id_seance="SES-1",
            code_module="ALG101",
            type_seance=SessionType.COURS,
            date=date(2026, 4, 1),
            heure_debut=time(8, 0),
            heure_fin=time(10, 0),
            salle="A101",
            id_enseignant=teacher_user.id,
        )
        other_session = PlanningSession(
            id_seance="SES-2",
            code_module="ALG101",
            type_seance=SessionType.TD,
            date=date(2026, 4, 2),
            heure_debut=time(10, 0),
            heure_fin=time(12, 0),
            salle="A101",
            id_enseignant=other_teacher_user.id,
        )
        session.add_all([own_session, other_session])
        await session.flush()

        session.add_all(
            [
                Absence(
                    student_matricule="ST100",
                    planning_session_id=own_session.id,
                    statut_justificatif="en_attente",
                ),
                Absence(
                    student_matricule="ST200",
                    planning_session_id=other_session.id,
                    statut_justificatif="valide",
                ),
            ]
        )
        await session.commit()

    admin_response = await client.get(
        "/api/v1/export/absences?page=1&page_size=100",
        headers=bearer_headers(admin_user),
    )
    assert admin_response.status_code == 200
    assert admin_response.headers["content-type"].startswith("text/csv")
    assert admin_response.headers["x-total-count"] == "2"

    teacher_response = await client.get(
        "/api/v1/export/absences?page=1&page_size=100",
        headers=bearer_headers(teacher_user),
    )
    assert teacher_response.status_code == 200
    assert teacher_response.headers["x-total-count"] == "1"
    content = teacher_response.text
    assert "ST100" in content
    assert "ST200" not in content