import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.enums import SessionStatusEnum, SessionType
from app.db import Base, get_db
from app.main import app
from app.models import Absence, AcademicStudent, Admin, Module, Session, Teacher
from app.models.student import Student
from app.core.security import hash_password

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
        base_url="https://test",
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_get_student_profile_includes_module_attendance_and_history(client: AsyncClient):
    async with TestSessionLocal() as session:
        admin = Admin(
            id=uuid.uuid4(),
            first_name="Admin",
            last_name="User",
            email="admin.profile@esi-sba.dz",
            hashed_password=hash_password("Admin@1234"),
            is_active=True,
            department="Administration",
            admin_level="super",
        )
        teacher = Teacher(
            id=uuid.uuid4(),
            first_name="TRARI",
            last_name="NOUR ELFOUAD",
            email="trari.nour@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            employee_id="ENS002",
        )
        academic = AcademicStudent(
            matricule="232332029109",
            nom="ABADELIA",
            prenom="Mohammed imad eddine",
            filiere="CS",
            niveau="1CS",
            groupe="G3",
            email="mie.abadelia@esi-sba.dz",
            status="normal",
        )
        auth_student = Student(
            id=uuid.uuid4(),
            first_name="Mohammed",
            last_name="Abadelia",
            email="mie.abadelia@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            student_id="ST-232332029109",
            program="CS",
            level="1CS",
            group="G3",
        )
        module_archi = Module(code="ARCHI", nom="Archi")
        module_acsi = Module(code="ACSI", nom="ACSI")

        session.add_all([admin, teacher, academic, auth_student, module_archi, module_acsi])
        await session.flush()

        own_group_session = Session(
            module_id=module_archi.id,
            teacher_id=teacher.id,
            room_id=None,
            group="G3",
            year="1CS",
            section="A",
            speciality="ISI",
            semester="S1",
            date=date(2026, 5, 20),
            start_time=time(8, 0),
            end_time=time(15, 0),
            type=SessionType.COURS,
            status=SessionStatusEnum.SCHEDULED,
            is_makeup=False,
        )
        cross_group_session = Session(
            module_id=module_archi.id,
            teacher_id=teacher.id,
            room_id=None,
            group="G4",
            year="1CS",
            section="A",
            speciality="ISI",
            semester="S1",
            date=date(2026, 5, 21),
            start_time=time(8, 0),
            end_time=time(10, 0),
            type=SessionType.TD,
            status=SessionStatusEnum.SCHEDULED,
            is_makeup=False,
        )
        session.add_all([own_group_session, cross_group_session])
        await session.flush()

        session.add_all(
            [
                Absence(
                    session_id=own_group_session.id,
                    student_matricule=academic.matricule,
                    is_absent=False,
                    statut_justificatif=None,
                ),
                Absence(
                    session_id=cross_group_session.id,
                    student_matricule=academic.matricule,
                    is_absent=True,
                    statut_justificatif="en_attente",
                ),
            ]
        )
        await session.commit()

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "admin.profile@esi-sba.dz", "password": "Admin@1234"},
    )
    assert login_response.status_code == 200

    response = await client.get(f"/api/v1/students/{academic.matricule}")
    assert response.status_code == 200
    body = response.json()

    assert body["matricule"] == "232332029109"
    assert body["nom"] == "ABADELIA"
    assert body["total_sessions"] == 1
    assert body["total_absences"] == 0
    assert body["attendance_rate"] == 100.0
    assert body["cross_session_count"] == 1

    modules = {m["module_name"]: m for m in body["module_attendance"]}
    assert modules["Archi"]["total_sessions"] == 1
    assert modules["Archi"]["absences"] == 0
    assert modules["Archi"]["attendance_rate"] == 100.0
    assert modules["ACSI"]["total_sessions"] == 0
    assert modules["ACSI"]["absences"] == 0
    assert modules["ACSI"]["attendance_rate"] is None

    assert len(body["absence_history"]) == 2
    by_group = {item["session_group"]: item for item in body["absence_history"]}
    assert by_group["G3"]["is_own_group"] is True
    assert by_group["G3"]["is_cross_session"] is False
    assert by_group["G4"]["is_own_group"] is False
    assert by_group["G4"]["is_cross_session"] is True
