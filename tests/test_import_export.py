import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.helpers.security import create_access_token
from app.main import app
from app.models.academic import Absence, Module, PlanningSession, Salle, SessionType, Student
from app.models.user import User, UserRole

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


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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
        user = User(
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
        user = User(
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
        user = User(
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


def bearer_headers(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_import_students_partial_success(client: AsyncClient, admin_user: User):
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

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 2
    assert data["errors"] == 1
    assert len(data["error_report"]) == 1
    assert data["error_report"][0]["field"] == "email"
    assert data["history_id"]


@pytest.mark.asyncio
async def test_import_students_forbidden_for_teacher(client: AsyncClient, teacher_user: User):
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
    admin_user: User,
    teacher_user: User,
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
    admin_user: User,
    teacher_user: User,
    other_teacher_user: User,
):
    async with TestSessionLocal() as session:
        student_one = Student(
            matricule="ST100",
            nom="Alpha",
            prenom="One",
            filiere="INFO",
            niveau="L3",
            groupe="G1",
            email="alpha.one@esi-sba.dz",
        )
        student_two = Student(
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
