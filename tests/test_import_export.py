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
from app.models import Admin, Student as StudentProfile, Teacher, UserRole

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
            last_name="One",
            email="teacher.one@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            employee_id="EMP-001",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_teacher_user():
    async with TestSessionLocal() as session:
        user = Teacher(
            id=uuid.uuid4(),
            first_name="Teacher",
            last_name="Two",
            email="teacher.two@esi-sba.dz",
            hashed_password="unused",
            is_active=True,
            employee_id="EMP-002",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer_headers(user: Admin | Teacher | StudentProfile) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_import_students_atomic_rejects_on_any_invalid_row(
    client: AsyncClient, admin_user: Admin
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

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] == 1
    assert len(data["error_report"]) == 1
    assert data["error_report"][0]["field"] == "email"
    assert data["error_report"][0]["line"] == 2
    assert data["error_report"][0]["reason"] == "Format email invalide"

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(AcademicStudent).where(
                AcademicStudent.matricule.in_(["ST001", "ST002", "ST003"])
            )
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_import_students_rejects_existing_matricule_without_writing(
    client: AsyncClient, admin_user: Admin
):
    async with TestSessionLocal() as session:
        session.add(
            AcademicStudent(
                matricule="ST001",
                nom="Existing",
                prenom="Student",
                filiere="INFO",
                niveau="L3",
                groupe="G1",
                email="existing.student@esi-sba.dz",
            )
        )
        await session.commit()

    csv_content = (
        "matricule,nom,prenom,filiere,niveau,groupe,email\n"
        "ST001,Doe,John,INFO,L3,G1,john.doe@esi-sba.dz\n"
        "ST002,Doe,Jane,INFO,L3,G1,jane.doe@esi-sba.dz\n"
    )

    response = await client.post(
        "/api/v1/import/students",
        files={"file": ("students.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] >= 1
    assert any(
        error["field"] == "matricule"
        and error["line"] == 1
        and "Étudiant déjà importé" in error["reason"]
        for error in data["error_report"]
    )

    async with TestSessionLocal() as session:
        result = await session.execute(
            select(AcademicStudent).where(AcademicStudent.matricule == "ST002")
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_import_students_with_access_cookie(client: AsyncClient, admin_user: Admin):
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

        profile_by_email_result = await session.execute(
            select(StudentProfile).where(StudentProfile.email == "cookie.admin@esi-sba.dz")
        )
        imported_account = profile_by_email_result.scalar_one_or_none()
        assert imported_account is not None
        assert imported_account.role == UserRole.STUDENT
        assert imported_account.first_name == "Admin"
        assert imported_account.last_name == "Cookie"

        profile_result = await session.execute(
            select(StudentProfile).where(StudentProfile.id == imported_account.id)
        )
        imported_profile = profile_result.scalar_one_or_none()
        assert imported_profile is not None
        assert imported_profile.student_id == "ST010"
        assert imported_profile.program == "INFO"
        assert imported_profile.level == "L3"
        assert imported_profile.group == "G1"


@pytest.mark.asyncio
async def test_import_students_forbidden_for_teacher(client: AsyncClient, teacher_user: Teacher):
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
    admin_user: Admin,
    teacher_user: Teacher,
):
    async with TestSessionLocal() as session:
        session.add(Module(code="ALG101", nom="Algebra"))
        session.add(Salle(code="A101"))
        await session.commit()

    csv_content = (
        "year,section,speciality,semester,day,time_start,time_end,type,subject,teacher,room,group\n"
        f"2CS,A,ISI,S1,Dimanche,08:00,10:00,Cours,Algebra,{teacher_user.employee_id},A101,G1\n"
        "2CS,B,SIW,S1,Lundi,10:00,12:00,Cours,Networks,EMP-404,A101,G1\n"
    )

    response = await client.post(
        "/api/v1/import/planning",
        files={"file": ("planning.csv", csv_content, "text/csv")},
        headers=bearer_headers(admin_user),
    )

    assert response.status_code == 409
    data = response.json()
    assert data["imported"] == 0
    assert data["errors"] == 1
    assert data["error_report"][0]["field"] == "teacher"


@pytest.mark.asyncio
async def test_export_absences_admin_and_teacher_scope(
    client: AsyncClient,
    admin_user: Admin,
    teacher_user: Teacher,
    other_teacher_user: Teacher,
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
        other_session = PlanningSession(
            year=AcademicYear.CS_2,
            section=SectionEnum.B,
            speciality=SpecialityEnum.SIW,
            semester="S1",
            day="Lundi",
            time_start=time(10, 0),
            time_end=time(12, 0),
            type="TD",
            subject="Algebra",
            room="A101",
            group="G2",
            teacher_id=other_teacher_user.id,
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