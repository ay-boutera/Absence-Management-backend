import csv
import io
import uuid
from datetime import datetime, date as dt_date
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.helpers.permissions import (
    require_can_import_data_bearer,
    require_can_export_data_bearer,
    require_admin_or_teacher_bearer,
)
from app.models.academic import (
    Absence,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportExportLog,
    Module,
    PlanningSession,
    Salle,
    SessionType,
    Student as AcademicStudent,
)
from app.models.user import Account, Student as StudentProfile, Teacher as TeacherProfile, UserRole
from app.schemas.import_export import ImportErrorItem, ImportResponse

router = APIRouter(tags=["Import/Export"])
email_adapter = TypeAdapter(EmailStr)


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file encoding. CSV must be UTF-8.",
        ) from exc


def _validate_columns(actual_columns: Sequence[str], expected_columns: list[str]) -> None:
    if not actual_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV header is missing.",
        )

    if len(actual_columns) == 1 and ";" in actual_columns[0]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid delimiter. CSV must be comma-delimited.",
        )

    missing = [col for col in expected_columns if col not in actual_columns]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required columns: {', '.join(missing)}",
        )


def _parse_csv(content: str, expected_columns: list[str]) -> csv.DictReader:
    csv_stream = io.StringIO(content)
    reader = csv.DictReader(csv_stream, delimiter=",")
    _validate_columns(reader.fieldnames or [], expected_columns)
    return reader


@router.post(
    "/import/students",
    response_model=ImportResponse,
    summary="Import students from CSV",
)
async def import_students_csv(
    file: UploadFile = File(...),
    current_user: Account = Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Import students with ALL-OR-NOTHING semantics.
    - Validates every row first
    - If any row is invalid, nothing is saved
    - Returns detailed row-level errors with row data

    Expected UTF-8 comma-delimited columns:
    matricule, nom, prenom, filiere, niveau, groupe, email
    """
    raw = await file.read()
    content = _decode_utf8(raw)

    expected_columns = [
        "matricule",
        "nom",
        "prenom",
        "filiere",
        "niveau",
        "groupe",
        "email",
    ]
    reader = _parse_csv(content, expected_columns)

    error_report: list[ImportErrorItem] = []
    total_rows = 0
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    seen_matricules: set[str] = set()
    seen_emails: set[str] = set()

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned_row = {column: (row.get(column) or "").strip() for column in expected_columns}

        missing_fields = [field for field, value in cleaned_row.items() if not value]
        for field in missing_fields:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field=field,
                    reason=f"{field} is required",
                    row_data=cleaned_row,
                )
            )

        matricule = cleaned_row["matricule"]
        if matricule:
            if matricule in seen_matricules:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="matricule",
                        reason="Duplicate matricule found in file",
                        row_data=cleaned_row,
                    )
                )
            else:
                seen_matricules.add(matricule)

        email_value = cleaned_row["email"].lower()
        cleaned_row["email"] = email_value
        if email_value:
            if email_value in seen_emails:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Duplicate email found in file",
                        row_data=cleaned_row,
                    )
                )
            else:
                seen_emails.add(email_value)

        if email_value:
            try:
                email_adapter.validate_python(email_value)
            except (ValidationError, ValueError):
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Invalid email format",
                        row_data=cleaned_row,
                    )
                )

        parsed_rows.append((line_number, cleaned_row))

    # Stop early on format errors. Nothing has been written yet.
    if error_report:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Import rejected: one or more rows are invalid. No data was saved.",
                "imported": 0,
                "errors": len(error_report),
                "error_report": [item.model_dump() for item in error_report],
            },
        )

    if not parsed_rows:
        history = ImportExportLog(
            performed_by_id=current_user.id,
            action=ImportExportAction.IMPORT,
            file_type=ImportExportFileType.CSV,
            file_name=file.filename or "students.csv",
            data_type=ImportExportDataType.STUDENTS,
            row_count=0,
            success_count=0,
            error_count=0,
            error_details={"error_report": []},
        )
        db.add(history)
        await db.flush()
        return ImportResponse(imported=0, errors=0, error_report=[], history_id=history.id)

    matricules = [row["matricule"] for _, row in parsed_rows]
    emails = [row["email"] for _, row in parsed_rows]

    academic_students_result = await db.execute(
        select(AcademicStudent).where(AcademicStudent.matricule.in_(matricules))
    )
    academic_by_matricule = {student.matricule: student for student in academic_students_result.scalars().all()}

    profiles_result = await db.execute(
        select(StudentProfile)
        .options(selectinload(StudentProfile.user))
        .where(StudentProfile.student_id.in_(matricules))
    )
    profiles_by_student_id = {profile.student_id: profile for profile in profiles_result.scalars().all()}

    accounts_result = await db.execute(
        select(Account)
        .options(selectinload(Account.student_profile))
        .where(func.lower(Account.email).in_(emails))
    )
    accounts_by_email = {account.email.lower(): account for account in accounts_result.scalars().all()}

    prepared_rows: list[dict] = []
    for line_number, row in parsed_rows:
        matricule = row["matricule"]
        email_value = row["email"]

        existing_profile = profiles_by_student_id.get(matricule)
        account_with_email = accounts_by_email.get(email_value)

        selected_account: Account | None = None
        selected_profile: StudentProfile | None = None

        if existing_profile is not None:
            if existing_profile.user is None:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="matricule",
                        reason="Student profile is orphaned (no linked account).",
                        row_data=row,
                    )
                )
                continue
            if existing_profile.user.role != UserRole.STUDENT:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="matricule",
                        reason="Student profile is linked to a non-student account.",
                        row_data=row,
                    )
                )
                continue
            if account_with_email is not None and account_with_email.id != existing_profile.user.id:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Email is already used by another account.",
                        row_data=row,
                    )
                )
                continue
            selected_account = existing_profile.user
            selected_profile = existing_profile
        else:
            if account_with_email is not None:
                if account_with_email.role != UserRole.STUDENT:
                    error_report.append(
                        ImportErrorItem(
                            line=line_number,
                            field="email",
                            reason="Email is already used by a non-student account.",
                            row_data=row,
                        )
                    )
                    continue
                if (
                    account_with_email.student_profile is not None
                    and account_with_email.student_profile.student_id != matricule
                ):
                    error_report.append(
                        ImportErrorItem(
                            line=line_number,
                            field="email",
                            reason=(
                                "Email is already linked to another student profile "
                                f"({account_with_email.student_profile.student_id})."
                            ),
                            row_data=row,
                        )
                    )
                    continue
                selected_account = account_with_email
                selected_profile = account_with_email.student_profile

        prepared_rows.append(
            {
                "line": line_number,
                "row": row,
                "academic_student": academic_by_matricule.get(matricule),
                "account": selected_account,
                "student_profile": selected_profile,
            }
        )

    # Reject everything if any row failed DB-level validation.
    if error_report:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Import rejected: one or more rows are invalid. No data was saved.",
                "imported": 0,
                "errors": len(error_report),
                "error_report": [item.model_dump() for item in error_report],
            },
        )

    # Apply every upsert in a single transaction scope.
    async with db.begin_nested():
        for item in prepared_rows:
            row = item["row"]
            academic_student = item["academic_student"]
            account = item["account"]
            student_profile = item["student_profile"]

            if academic_student is None:
                academic_student = AcademicStudent(
                    matricule=row["matricule"],
                    nom=row["nom"],
                    prenom=row["prenom"],
                    filiere=row["filiere"],
                    niveau=row["niveau"],
                    groupe=row["groupe"],
                    email=row["email"],
                )
                db.add(academic_student)
            else:
                academic_student.nom = row["nom"]
                academic_student.prenom = row["prenom"]
                academic_student.filiere = row["filiere"]
                academic_student.niveau = row["niveau"]
                academic_student.groupe = row["groupe"]
                academic_student.email = row["email"]
                db.add(academic_student)

            if account is None:
                account = Account(
                    email=row["email"],
                    first_name=row["prenom"],
                    last_name=row["nom"],
                    phone=None,
                    hashed_password=None,
                    role=UserRole.STUDENT,
                    is_active=True,
                )
                db.add(account)
                await db.flush()
            else:
                account.email = row["email"]
                account.first_name = row["prenom"]
                account.last_name = row["nom"]
                account.role = UserRole.STUDENT
                db.add(account)

            if student_profile is None:
                student_profile = StudentProfile(
                    user_id=account.id,
                    student_id=row["matricule"],
                    program=row["filiere"],
                    level=row["niveau"],
                    group=row["groupe"],
                )
                db.add(student_profile)
            else:
                student_profile.student_id = row["matricule"]
                student_profile.program = row["filiere"]
                student_profile.level = row["niveau"]
                student_profile.group = row["groupe"]
                db.add(student_profile)

        history = ImportExportLog(
            performed_by_id=current_user.id,
            action=ImportExportAction.IMPORT,
            file_type=ImportExportFileType.CSV,
            file_name=file.filename or "students.csv",
            data_type=ImportExportDataType.STUDENTS,
            row_count=total_rows,
            success_count=len(prepared_rows),
            error_count=0,
            error_details={"error_report": []},
        )
        db.add(history)
        await db.flush()

    return ImportResponse(
        imported=len(prepared_rows),
        errors=0,
        error_report=[],
        history_id=history.id,
    )


@router.post(
    "/import/teachers",
    response_model=ImportResponse,
    summary="Import teachers from CSV",
)
async def import_teachers_csv(
    file: UploadFile = File(...),
    current_user: Account = Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Import teachers with ALL-OR-NOTHING semantics.
    - Validates every row first
    - If any row is invalid, nothing is saved
    - Returns detailed row-level errors with row data

    Expected UTF-8 comma-delimited columns:
    employee_id, email, first_name, last_name, phone, subjects, groups

    `subjects` and `groups` are pipe-separated values (e.g. "MATH01|PHY02", "G1|G2").
    `phone` is optional (leave blank if not available).
    """
    raw = await file.read()
    content = _decode_utf8(raw)

    expected_columns = [
        "employee_id",
        "email",
        "first_name",
        "last_name",
        "phone",
        "subjects",
        "groups",
    ]
    reader = _parse_csv(content, expected_columns)

    error_report: list[ImportErrorItem] = []
    total_rows = 0
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    seen_employee_ids: set[str] = set()
    seen_emails: set[str] = set()

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned_row = {col: (row.get(col) or "").strip() for col in expected_columns}

        # Required fields (phone, subjects, groups are optional)
        required_fields = ["employee_id", "email", "first_name", "last_name"]
        for field in required_fields:
            if not cleaned_row[field]:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field=field,
                        reason=f"{field} is required",
                        row_data=cleaned_row,
                    )
                )

        employee_id = cleaned_row["employee_id"]
        if employee_id:
            if employee_id in seen_employee_ids:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="employee_id",
                        reason="Duplicate employee_id found in file",
                        row_data=cleaned_row,
                    )
                )
            else:
                seen_employee_ids.add(employee_id)

        email_value = cleaned_row["email"].lower()
        cleaned_row["email"] = email_value
        if email_value:
            if email_value in seen_emails:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Duplicate email found in file",
                        row_data=cleaned_row,
                    )
                )
            else:
                seen_emails.add(email_value)

            try:
                email_adapter.validate_python(email_value)
            except (ValidationError, ValueError):
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Invalid email format",
                        row_data=cleaned_row,
                    )
                )

        parsed_rows.append((line_number, cleaned_row))

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Import rejected: one or more rows are invalid. No data was saved.",
                "imported": 0,
                "errors": len(error_report),
                "error_report": [item.model_dump() for item in error_report],
            },
        )

    if not parsed_rows:
        history = ImportExportLog(
            performed_by_id=current_user.id,
            action=ImportExportAction.IMPORT,
            file_type=ImportExportFileType.CSV,
            file_name=file.filename or "teachers.csv",
            data_type=ImportExportDataType.TEACHERS,
            row_count=0,
            success_count=0,
            error_count=0,
            error_details={"error_report": []},
        )
        db.add(history)
        await db.flush()
        return ImportResponse(imported=0, errors=0, error_report=[], history_id=history.id)

    employee_ids = [row["employee_id"] for _, row in parsed_rows]
    emails = [row["email"] for _, row in parsed_rows]

    teacher_profiles_result = await db.execute(
        select(TeacherProfile)
        .options(selectinload(TeacherProfile.user))
        .where(TeacherProfile.employee_id.in_(employee_ids))
    )
    profiles_by_employee_id = {p.employee_id: p for p in teacher_profiles_result.scalars().all()}

    accounts_result = await db.execute(
        select(Account)
        .options(selectinload(Account.teacher_profile))
        .where(func.lower(Account.email).in_(emails))
    )
    accounts_by_email = {a.email.lower(): a for a in accounts_result.scalars().all()}

    prepared_rows: list[dict] = []
    for line_number, row in parsed_rows:
        employee_id = row["employee_id"]
        email_value = row["email"]

        existing_profile = profiles_by_employee_id.get(employee_id)
        account_with_email = accounts_by_email.get(email_value)

        selected_account: Account | None = None
        selected_profile: TeacherProfile | None = None

        if existing_profile is not None:
            if existing_profile.user is None:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="employee_id",
                        reason="Teacher profile is orphaned (no linked account).",
                        row_data=row,
                    )
                )
                continue
            if existing_profile.user.role != UserRole.TEACHER:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="employee_id",
                        reason="Teacher profile is linked to a non-teacher account.",
                        row_data=row,
                    )
                )
                continue
            if account_with_email is not None and account_with_email.id != existing_profile.user.id:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Email is already used by another account.",
                        row_data=row,
                    )
                )
                continue
            selected_account = existing_profile.user
            selected_profile = existing_profile
        else:
            if account_with_email is not None:
                if account_with_email.role != UserRole.TEACHER:
                    error_report.append(
                        ImportErrorItem(
                            line=line_number,
                            field="email",
                            reason="Email is already used by a non-teacher account.",
                            row_data=row,
                        )
                    )
                    continue
                if (
                    account_with_email.teacher_profile is not None
                    and account_with_email.teacher_profile.employee_id != employee_id
                ):
                    error_report.append(
                        ImportErrorItem(
                            line=line_number,
                            field="email",
                            reason=(
                                "Email is already linked to another teacher profile "
                                f"({account_with_email.teacher_profile.employee_id})."
                            ),
                            row_data=row,
                        )
                    )
                    continue
                selected_account = account_with_email
                selected_profile = account_with_email.teacher_profile

        prepared_rows.append(
            {
                "line": line_number,
                "row": row,
                "account": selected_account,
                "teacher_profile": selected_profile,
            }
        )

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Import rejected: one or more rows are invalid. No data was saved.",
                "imported": 0,
                "errors": len(error_report),
                "error_report": [item.model_dump() for item in error_report],
            },
        )

    async with db.begin_nested():
        for item in prepared_rows:
            row = item["row"]
            account = item["account"]
            teacher_profile = item["teacher_profile"]

            if account is None:
                account = Account(
                    email=row["email"],
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    phone=row["phone"] or None,
                    hashed_password=None,
                    role=UserRole.TEACHER,
                    is_active=True,
                )
                db.add(account)
                await db.flush()
            else:
                account.email = row["email"]
                account.first_name = row["first_name"]
                account.last_name = row["last_name"]
                account.phone = row["phone"] or None
                account.role = UserRole.TEACHER
                db.add(account)

            subjects_value = row["subjects"] or None
            groups_value = row["groups"] or None

            if teacher_profile is None:
                teacher_profile = TeacherProfile(
                    user_id=account.id,
                    employee_id=row["employee_id"],
                    subjects=subjects_value,
                    groups=groups_value,
                )
                db.add(teacher_profile)
            else:
                teacher_profile.employee_id = row["employee_id"]
                teacher_profile.subjects = subjects_value
                teacher_profile.groups = groups_value
                db.add(teacher_profile)

        history = ImportExportLog(
            performed_by_id=current_user.id,
            action=ImportExportAction.IMPORT,
            file_type=ImportExportFileType.CSV,
            file_name=file.filename or "teachers.csv",
            data_type=ImportExportDataType.TEACHERS,
            row_count=total_rows,
            success_count=len(prepared_rows),
            error_count=0,
            error_details={"error_report": []},
        )
        db.add(history)
        await db.flush()

    return ImportResponse(
        imported=len(prepared_rows),
        errors=0,
        error_report=[],
        history_id=history.id,
    )


@router.post(
    "/import/planning",
    response_model=ImportResponse,
    summary="Import planning sessions from CSV",
)
async def import_planning_csv(
    file: UploadFile = File(...),
    current_user: Account = Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Import planning sessions CSV with partial import and referential checks.

    Expected UTF-8 comma-delimited columns:
    id_seance, code_module, type_seance, date, heure_debut, heure_fin, salle, id_enseignant

    `type_seance` must be one of: cours, TD, TP, examen.
    """
    raw = await file.read()
    content = _decode_utf8(raw)

    expected_columns = [
        "id_seance",
        "code_module",
        "type_seance",
        "date",
        "heure_debut",
        "heure_fin",
        "salle",
        "id_enseignant",
    ]
    reader = _parse_csv(content, expected_columns)

    type_mapping = {
        "cours": SessionType.COURS,
        "td": SessionType.TD,
        "tp": SessionType.TP,
        "examen": SessionType.EXAMEN,
    }

    error_report: list[ImportErrorItem] = []
    imported_count = 0
    total_rows = 0

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1

        id_seance = (row.get("id_seance") or "").strip()
        code_module = (row.get("code_module") or "").strip()
        type_raw = (row.get("type_seance") or "").strip().lower()
        date_raw = (row.get("date") or "").strip()
        heure_debut_raw = (row.get("heure_debut") or "").strip()
        heure_fin_raw = (row.get("heure_fin") or "").strip()
        salle_raw = (row.get("salle") or "").strip()
        id_enseignant_raw = (row.get("id_enseignant") or "").strip()

        if not id_seance:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="id_seance",
                    reason="id_seance is required",
                )
            )
            continue

        try:
            teacher_uuid = uuid.UUID(id_enseignant_raw)
        except ValueError:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="id_enseignant",
                    reason=f"Ligne {line_number} : id_enseignant {id_enseignant_raw} introuvable",
                )
            )
            continue

        if type_raw not in type_mapping:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="type_seance",
                    reason="type_seance must be one of: cours, TD, TP, examen",
                )
            )
            continue

        try:
            parsed_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="date",
                    reason="date must be in YYYY-MM-DD format",
                )
            )
            continue

        try:
            parsed_start = datetime.strptime(heure_debut_raw, "%H:%M").time()
        except ValueError:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="heure_debut",
                    reason="heure_debut must be in HH:MM format",
                )
            )
            continue

        try:
            parsed_end = datetime.strptime(heure_fin_raw, "%H:%M").time()
        except ValueError:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="heure_fin",
                    reason="heure_fin must be in HH:MM format",
                )
            )
            continue

        teacher_count = (
            await db.execute(
                select(func.count())
                .select_from(Account)
                .where(and_(Account.id == teacher_uuid, Account.role == UserRole.TEACHER))
            )
        ).scalar_one()
        if teacher_count == 0:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="id_enseignant",
                    reason=f"Ligne {line_number} : id_enseignant {id_enseignant_raw} introuvable",
                )
            )
            continue

        module_result = await db.execute(select(Module).where(Module.code == code_module))
        module = module_result.scalar_one_or_none()
        if not module:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="code_module",
                    reason=f"Ligne {line_number} : code_module {code_module} introuvable",
                )
            )
            continue

        salle_result = await db.execute(select(Salle).where(Salle.code == salle_raw))
        salle = salle_result.scalar_one_or_none()
        if not salle:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="salle",
                    reason=f"Ligne {line_number} : salle {salle_raw} introuvable",
                )
            )
            continue

        session_result = await db.execute(
            select(PlanningSession).where(PlanningSession.id_seance == id_seance)
        )
        session = session_result.scalar_one_or_none()
        if session is None:
            session = PlanningSession(
                id_seance=id_seance,
                code_module=code_module,
                type_seance=type_mapping[type_raw],
                date=parsed_date,
                heure_debut=parsed_start,
                heure_fin=parsed_end,
                salle=salle_raw,
                id_enseignant=teacher_uuid,
            )
            db.add(session)
        else:
            await db.execute(
                update(PlanningSession)
                .where(PlanningSession.id == session.id)
                .values(
                    code_module=code_module,
                    type_seance=type_mapping[type_raw],
                    date=parsed_date,
                    heure_debut=parsed_start,
                    heure_fin=parsed_end,
                    salle=salle_raw,
                    id_enseignant=teacher_uuid,
                )
            )

        imported_count += 1

    history = ImportExportLog(
        performed_by_id=current_user.id,
        action=ImportExportAction.IMPORT,
        file_type=ImportExportFileType.CSV,
        file_name=file.filename or "planning.csv",
        data_type=ImportExportDataType.SCHEDULE,
        row_count=total_rows,
        success_count=imported_count,
        error_count=len(error_report),
        error_details={"error_report": [item.model_dump() for item in error_report]},
    )
    db.add(history)
    await db.flush()

    return ImportResponse(
        imported=imported_count,
        errors=len(error_report),
        error_report=error_report,
        history_id=uuid.UUID(str(history.id)),
    )


@router.get(
    "/export/absences",
    summary="Export absences as CSV",
)
async def export_absences_csv(
    filiere: Optional[str] = Query(default=None),
    code_module: Optional[str] = Query(default=None),
    date_from: Optional[dt_date] = Query(default=None),
    date_to: Optional[dt_date] = Query(default=None),
    matricule_etudiant: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: Account = Depends(require_can_export_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Export absences to CSV with filters and pagination.

    CSV columns:
    matricule, nom_etudiant, prenom_etudiant, filiere, groupe, code_module,
    nom_module, type_seance, date_seance, heure_debut, heure_fin, statut_justificatif
    """
    base_query = (
        select(
            AcademicStudent.matricule,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.filiere,
            AcademicStudent.groupe,
            Module.code,
            Module.nom,
            PlanningSession.type_seance,
            PlanningSession.date,
            PlanningSession.heure_debut,
            PlanningSession.heure_fin,
            Absence.statut_justificatif,
        )
        .select_from(Absence)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .join(PlanningSession, PlanningSession.id == Absence.planning_session_id)
        .join(Module, Module.code == PlanningSession.code_module)
    )

    filters = []
    if filiere:
        filters.append(AcademicStudent.filiere == filiere)
    if code_module:
        filters.append(Module.code == code_module)
    if date_from:
        filters.append(PlanningSession.date >= date_from)
    if date_to:
        filters.append(PlanningSession.date <= date_to)
    if matricule_etudiant:
        filters.append(AcademicStudent.matricule == matricule_etudiant)

    if getattr(current_user.role, "value", str(current_user.role)) == UserRole.TEACHER.value:
        filters.append(PlanningSession.id_enseignant == current_user.id)

    if filters:
        base_query = base_query.where(and_(*filters))

    count_query = select(func.count()).select_from(base_query.subquery())
    total_count = (await db.execute(count_query)).scalar_one()

    paged_query = base_query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(paged_query)).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "matricule",
            "nom_etudiant",
            "prenom_etudiant",
            "filiere",
            "groupe",
            "code_module",
            "nom_module",
            "type_seance",
            "date_seance",
            "heure_debut",
            "heure_fin",
            "statut_justificatif",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7].value,
                row[8].isoformat() if row[8] else "",
                row[9].strftime("%H:%M") if row[9] else "",
                row[10].strftime("%H:%M") if row[10] else "",
                row[11] or "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    filename = f"absences_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    db.add(
        ImportExportLog(
            performed_by_id=current_user.id,
            action=ImportExportAction.EXPORT,
            file_type=ImportExportFileType.CSV,
            file_name=filename,
            data_type=ImportExportDataType.ATTENDANCE,
            row_count=total_count,
            success_count=len(rows),
            error_count=0,
            error_details={},
        )
    )

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Total-Count": str(total_count),
        "X-Page": str(page),
        "X-Page-Size": str(page_size),
    }

    return StreamingResponse(iter([csv_bytes]), media_type="text/csv", headers=headers)


@router.get(
    "/planning",
    summary="Get planning sessions",
)
async def get_planning(
    date_from: Optional[dt_date] = Query(default=None),
    date_to: Optional[dt_date] = Query(default=None),
    code_module: Optional[str] = Query(default=None),
    type_seance: Optional[SessionType] = Query(default=None),
    id_enseignant: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: Account = Depends(require_admin_or_teacher_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Return planning sessions with optional filters.

    - Admins see all sessions; teachers see only their own.
    - Filters: date_from, date_to, code_module, type_seance, id_enseignant (admin only).
    """
    query = (
        select(
            PlanningSession.id,
            PlanningSession.id_seance,
            PlanningSession.code_module,
            Module.nom.label("nom_module"),
            PlanningSession.type_seance,
            PlanningSession.date,
            PlanningSession.heure_debut,
            PlanningSession.heure_fin,
            PlanningSession.salle,
            PlanningSession.id_enseignant,
        )
        .join(Module, Module.code == PlanningSession.code_module)
    )

    filters = []
    if date_from:
        filters.append(PlanningSession.date >= date_from)
    if date_to:
        filters.append(PlanningSession.date <= date_to)
    if code_module:
        filters.append(PlanningSession.code_module == code_module)
    if type_seance:
        filters.append(PlanningSession.type_seance == type_seance)

    if getattr(current_user.role, "value", str(current_user.role)) == UserRole.TEACHER.value:
        filters.append(PlanningSession.id_enseignant == current_user.id)
    elif id_enseignant:
        try:
            filters.append(PlanningSession.id_enseignant == uuid.UUID(id_enseignant))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid id_enseignant format.",
            )

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(PlanningSession.date.asc(), PlanningSession.heure_debut.asc())

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(row.id),
                "id_seance": row.id_seance,
                "code_module": row.code_module,
                "nom_module": row.nom_module,
                "type_seance": row.type_seance.value,
                "date": row.date.isoformat() if row.date else None,
                "heure_debut": row.heure_debut.strftime("%H:%M") if row.heure_debut else None,
                "heure_fin": row.heure_fin.strftime("%H:%M") if row.heure_fin else None,
                "salle": row.salle,
                "id_enseignant": str(row.id_enseignant),
            }
            for row in rows
        ],
    }


@router.get(
    "/import-export/history",
    summary="Get import/export audit history",
)
async def import_export_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: Account = Depends(require_admin_or_teacher_bearer),
    db: AsyncSession = Depends(get_db),
):
    query = select(ImportExportLog).order_by(ImportExportLog.created_at.desc())

    if getattr(current_user.role, "value", str(current_user.role)) == UserRole.TEACHER.value:
        query = query.where(ImportExportLog.performed_by_id == current_user.id)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(item.id),
                "performed_by_id": str(item.performed_by_id) if item.performed_by_id is not None else None,
                "action": item.action.value,
                "file_type": item.file_type.value,
                "file_name": item.file_name,
                "data_type": item.data_type.value,
                "row_count": item.row_count,
                "success_count": item.success_count,
                "error_count": item.error_count,
                "error_details": item.error_details,
                "created_at": item.created_at.isoformat() if item.created_at is not None else None,
            }
            for item in rows
        ],
    }
