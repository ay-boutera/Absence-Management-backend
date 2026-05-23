import csv
import io
import logging
from typing import Sequence

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_bearer, require_can_import_data_bearer
from app.helpers.role_users import get_user_by_email
from app.models import (
    Admin,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportExportLog,
    ImportHistory,
    ImportType,
    Student as StudentProfile,
    Teacher,
)
from app.models.student import AcademicStudent
from app.schemas.import_export import ImportErrorItem, ImportResponse

router = APIRouter(tags=["Imports"])
logger = logging.getLogger(__name__)
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
            detail={
                "error": "Format CSV invalide",
                "detail": "En-tête CSV manquant.",
            },
        )

    if len(actual_columns) == 1 and ";" in actual_columns[0]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": "Délimiteur invalide. Le CSV doit être séparé par des virgules.",
            },
        )

    missing = [col for col in expected_columns if col not in actual_columns]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": f"Colonnes manquantes: {', '.join(missing)}",
            },
        )


def _parse_csv(content: str, expected_columns: list[str]) -> csv.DictReader:
    csv_stream = io.StringIO(content)
    reader = csv.DictReader(csv_stream, delimiter=",")
    
    # Strip whitespace from fieldnames for robustness
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]
        
    _validate_columns(reader.fieldnames or [], expected_columns)
    return reader


@router.post(
    "/import/students",
    response_model=ImportResponse,
    summary="Import students from CSV",
)
async def import_students_csv(
    file: UploadFile = File(...),
    current_user=Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
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
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    total_rows = 0
    seen_matricules: set[str] = set()

    for line_number, row in enumerate(reader, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned_row = {
            column: (row.get(column) or "").strip() for column in expected_columns
        }

        for field, value in cleaned_row.items():
            if not value:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field=field,
                        reason=f"{field} est requis",
                    )
                )

        matricule = cleaned_row["matricule"]
        if matricule:
            if matricule in seen_matricules:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="matricule",
                        reason=f"Matricule dupliqué dans le fichier — matricule {matricule} apparaît plusieurs fois",
                    )
                )
            else:
                seen_matricules.add(matricule)

        email_value = cleaned_row["email"].lower()
        cleaned_row["email"] = email_value
        if email_value:
            try:
                email_adapter.validate_python(email_value)
            except (ValidationError, ValueError):
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason="Format email invalide",
                    )
                )

        parsed_rows.append((line_number, cleaned_row))

    if not parsed_rows and not error_report:
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

    matricules = [row["matricule"] for _, row in parsed_rows if row["matricule"]]
    emails = [row["email"] for _, row in parsed_rows if row["email"]]

    # 1. Fetch academic students
    as_res = await db.execute(select(AcademicStudent).where(AcademicStudent.matricule.in_(matricules)))
    existing_academic_by_matricule = {s.matricule: s for s in as_res.scalars().all()}

    # 2. Fetch profiles by student_id (matricule)
    p_res_id = await db.execute(select(StudentProfile).where(StudentProfile.student_id.in_(matricules)))
    profiles_by_student_id = {p.student_id: p for p in p_res_id.scalars().all()}

    # 3. Fetch profiles by email
    p_res_em = await db.execute(select(StudentProfile).where(func.lower(StudentProfile.email).in_(emails)))
    students_by_email = {p.email.lower(): p for p in p_res_em.scalars().all()}

    # 4. Check for email conflicts with Admins/Teachers
    adm_res = await db.execute(select(func.lower(Admin.email)).where(func.lower(Admin.email).in_(emails)))
    admin_emails = {v for v in adm_res.scalars().all() if v}
    
    t_res = await db.execute(select(func.lower(Teacher.email)).where(func.lower(Teacher.email).in_(emails)))
    teacher_emails = {v for v in t_res.scalars().all() if v}

    prepared_rows: list[dict[str, object]] = []
    for line_number, row in parsed_rows:
        matricule = row["matricule"]
        email_value = row["email"]

        if email_value in admin_emails or email_value in teacher_emails:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="email",
                    reason=f"Email déjà utilisé par un Admin/Enseignant — {email_value}",
                )
            )
            continue

        existing_academic = existing_academic_by_matricule.get(matricule)
        existing_profile = students_by_email.get(email_value) or profiles_by_student_id.get(matricule)

        prepared_rows.append(
            {
                "row": row,
                "student_profile": existing_profile,
                "existing_academic": existing_academic,
            }
        )

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "imported": 0,
                "errors": len(error_report),
                "error_report": [
                    {
                        "line": item.line,
                        "field": item.field,
                        "reason": item.reason,
                    }
                    for item in error_report
                ],
            },
        )

    try:
        async with db.begin_nested():
            for item in prepared_rows:
                row = item["row"]
                student_profile = item["student_profile"]
                existing_academic = item["existing_academic"]

                # Logic to combine niveau (e.g. 1) and filiere (e.g. CS) -> 1CS
                raw_niv = row.get("niveau", "").strip()
                raw_fil = row.get("filiere", "").strip()
                
                if raw_fil and raw_fil.upper() not in raw_niv.upper():
                    target_level = f"{raw_niv}{raw_fil}".upper()
                else:
                    target_level = raw_niv.upper()

                logger.info(f"Student {row.get('matricule')}: mapping '{raw_niv}' + '{raw_fil}' -> '{target_level}'")

                if existing_academic:
                    # UPDATE EXISTING
                    existing_academic.nom = row["nom"]
                    existing_academic.prenom = row["prenom"]
                    existing_academic.filiere = row["filiere"]
                    existing_academic.niveau = target_level
                    existing_academic.groupe = row["groupe"]
                    existing_academic.email = row["email"]
                    db.add(existing_academic)
                else:
                    # CREATE NEW
                    academic_student = AcademicStudent(
                        matricule=row["matricule"],
                        nom=row["nom"],
                        prenom=row["prenom"],
                        filiere=row["filiere"],
                        niveau=target_level,
                        groupe=row["groupe"],
                        email=row["email"],
                    )
                    db.add(academic_student)

                if student_profile is None:
                    # CREATE NEW PROFILE
                    student_profile = StudentProfile(
                        email=row["email"],
                        first_name=row["prenom"],
                        last_name=row["nom"],
                        phone=None,
                        hashed_password=None,
                        is_active=True,
                        student_id=row["matricule"],
                        program=row["filiere"],
                        level=target_level,
                        group=row["groupe"],
                    )
                    db.add(student_profile)
                else:
                    # UPDATE EXISTING PROFILE
                    student_profile.email = row["email"]
                    student_profile.first_name = row["prenom"]
                    student_profile.last_name = row["nom"]
                    student_profile.student_id = row["matricule"]
                    student_profile.program = row["filiere"]
                    student_profile.level = target_level
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
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'import des étudiants",
        ) from exc

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
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin_bearer),
):
    raw = await file.read()
    content = _decode_utf8(raw)

    expected_columns = [
        "id_enseignant",
        "nom",
        "prenom",
        "email",
        "grade",
        "departement",
    ]
    reader = _parse_csv(content, expected_columns)

    error_report: list[dict[str, str | int]] = []
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    total_rows = 0
    seen_employee_ids: set[str] = set()
    seen_emails: set[str] = set()

    for line_number, row in enumerate(reader, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned = {column: (row.get(column) or "").strip() for column in expected_columns}

        for field, value in cleaned.items():
            if not value:
                error_report.append(
                    {
                        "line": line_number,
                        "field": field,
                        "reason": f"{field} est requis",
                    }
                )

        employee_id = cleaned["id_enseignant"]
        email_value = cleaned["email"].lower()
        cleaned["email"] = email_value

        if employee_id:
            if employee_id in seen_employee_ids:
                error_report.append(
                    {
                        "line": line_number,
                        "field": "id_enseignant",
                        "reason": f"id_enseignant dupliqué dans le fichier — id_enseignant {employee_id} apparaît plusieurs fois",
                    }
                )
            else:
                seen_employee_ids.add(employee_id)

        if email_value:
            if email_value in seen_emails:
                error_report.append(
                    {
                        "line": line_number,
                        "field": "email",
                        "reason": f"Email dupliqué dans le fichier — email {email_value} apparaît plusieurs fois",
                    }
                )
            else:
                seen_emails.add(email_value)

            try:
                email_adapter.validate_python(email_value)
            except (ValidationError, ValueError):
                error_report.append(
                    {
                        "line": line_number,
                        "field": "email",
                        "reason": "Format email invalide",
                    }
                )

        parsed_rows.append((line_number, cleaned))

    employee_ids = [row["id_enseignant"] for _, row in parsed_rows if row["id_enseignant"]]

    teacher_by_employee_id: dict[str, Teacher] = {}
    teacher_by_email: dict[str, Teacher] = {}

    if employee_ids:
        teacher_rows = await db.execute(
            select(Teacher).where(Teacher.employee_id.in_(employee_ids))
        )
        for teacher in teacher_rows.scalars().all():
            if teacher.employee_id:
                teacher_by_employee_id[teacher.employee_id] = teacher

    if seen_emails:
        teacher_rows = await db.execute(
            select(Teacher).where(func.lower(Teacher.email).in_(seen_emails))
        )
        for teacher in teacher_rows.scalars().all():
            teacher_by_email[teacher.email.lower()] = teacher

    for line_number, row in parsed_rows:
        employee_id = row["id_enseignant"]
        email_value = row["email"]

        existing_by_email = await get_user_by_email(db, email_value)
        if existing_by_email is not None and not isinstance(existing_by_email, Teacher):
            error_report.append(
                {
                    "line": line_number,
                    "field": "email",
                    "reason": f"Email déjà utilisé — email {email_value} existe déjà",
                }
            )

        teacher = teacher_by_employee_id.get(employee_id)
        teacher_email = teacher_by_email.get(email_value)
        if teacher is None and teacher_email is not None:
            error_report.append(
                {
                    "line": line_number,
                    "field": "email",
                    "reason": f"Email déjà utilisé — email {email_value} existe déjà",
                }
            )

        if teacher is not None and teacher.email.lower() != email_value and teacher_email is not None:
            error_report.append(
                {
                    "line": line_number,
                    "field": "email",
                    "reason": f"Email déjà utilisé — email {email_value} existe déjà",
                }
            )

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "imported": 0,
                "errors": len(error_report),
                "error_report": error_report,
            },
        )

    imported_count = 0
    history: ImportHistory | None = None
    try:
        async with db.begin_nested():
            for _, row in parsed_rows:
                employee_id = row["id_enseignant"]
                email_value = row["email"]
                specialization_value = f"{row['grade']} | {row['departement']}"

                teacher = teacher_by_employee_id.get(employee_id)
                if teacher is None:
                    teacher = Teacher(
                        email=email_value,
                        first_name=row["prenom"],
                        last_name=row["nom"],
                        phone=None,
                        hashed_password=None,
                        is_active=True,
                        employee_id=employee_id,
                        specialization=specialization_value,
                    )
                    db.add(teacher)
                else:
                    teacher.email = email_value
                    teacher.first_name = row["prenom"]
                    teacher.last_name = row["nom"]
                    teacher.employee_id = employee_id
                    teacher.specialization = specialization_value
                    db.add(teacher)

                imported_count += 1

            history = ImportHistory(
                user_id=current_user.id,
                filename=file.filename or "teachers.csv",
                import_type=ImportType.TEACHERS,
                total_rows=total_rows,
                success_count=imported_count,
                error_count=0,
            )
            db.add(history)
            await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'import des enseignants",
        ) from exc

    return ImportResponse(
        imported=imported_count,
        errors=0,
        error_report=[],
        history_id=history.id,
    )
