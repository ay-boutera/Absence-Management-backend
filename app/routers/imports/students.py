import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_can_import_data_bearer
from app.helpers.role_users import get_user_by_email
from app.models import (
    Admin,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportExportLog,
    Teacher,
)
from app.models.student import AcademicStudent, Student as StudentProfile
from app.schemas.import_export import ImportErrorItem, ImportResponse

router = APIRouter(tags=["Imports"])
email_adapter = TypeAdapter(EmailStr)


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file encoding. CSV must be UTF-8.",
        ) from exc


def _validate_columns(actual_columns, expected_columns: list[str]) -> None:
    if not actual_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": "En-tête CSV manquant."},
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
            detail={"error": "Format CSV invalide", "detail": f"Colonnes manquantes: {', '.join(missing)}"},
        )


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

    expected_columns = ["matricule", "nom", "prenom", "filiere", "niveau", "groupe", "email"]
    csv_stream = io.StringIO(content)
    reader = csv.DictReader(csv_stream, delimiter=",")
    _validate_columns(reader.fieldnames or [], expected_columns)

    error_report: list[ImportErrorItem] = []
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    total_rows = 0
    seen_matricules: set[str] = set()

    for line_number, row in enumerate(reader, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned_row = {col: (row.get(col) or "").strip() for col in expected_columns}

        for field, value in cleaned_row.items():
            if not value:
                error_report.append(ImportErrorItem(line=line_number, field=field, reason=f"{field} est requis"))

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
                error_report.append(ImportErrorItem(line=line_number, field="email", reason="Format email invalide"))

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

    academic_result = await db.execute(select(AcademicStudent).where(AcademicStudent.matricule.in_(matricules)))
    existing_matricules = {s.matricule for s in academic_result.scalars().all()}

    profiles_result = await db.execute(select(StudentProfile).where(StudentProfile.student_id.in_(matricules)))
    profiles_by_id = {p.student_id: p for p in profiles_result.scalars().all()}

    students_result = await db.execute(select(StudentProfile).where(func.lower(StudentProfile.email).in_(emails)))
    students_by_email = {s.email.lower(): s for s in students_result.scalars().all()}

    admin_emails = {
        v
        for v in (
            await db.execute(select(func.lower(Admin.email)).where(func.lower(Admin.email).in_(emails)))
        ).scalars().all()
        if v
    }
    teacher_emails = {
        v
        for v in (
            await db.execute(select(func.lower(Teacher.email)).where(func.lower(Teacher.email).in_(emails)))
        ).scalars().all()
        if v
    }

    prepared_rows: list[dict] = []
    for line_number, row in parsed_rows:
        matricule = row["matricule"]
        email_value = row["email"]

        if matricule in existing_matricules or matricule in profiles_by_id:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="matricule",
                    reason=f"Étudiant déjà importé — matricule {matricule} existe déjà",
                )
            )
            continue

        if email_value in admin_emails or email_value in teacher_emails:
            error_report.append(
                ImportErrorItem(line=line_number, field="email", reason=f"Email déjà utilisé — email {email_value} existe déjà")
            )
            continue

        profile = students_by_email.get(email_value)
        if profile is not None and profile.student_id != matricule:
            error_report.append(
                ImportErrorItem(line=line_number, field="email", reason=f"Email déjà utilisé — email {email_value} existe déjà")
            )
            continue

        prepared_rows.append({"row": row, "student_profile": profile})

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "imported": 0,
                "errors": len(error_report),
                "error_report": [{"line": e.line, "field": e.field, "reason": e.reason} for e in error_report],
            },
        )

    try:
        async with db.begin_nested():
            for item in prepared_rows:
                row = item["row"]
                profile = item["student_profile"]

                db.add(
                    AcademicStudent(
                        matricule=row["matricule"],
                        nom=row["nom"],
                        prenom=row["prenom"],
                        filiere=row["filiere"],
                        niveau=row["niveau"],
                        groupe=row["groupe"],
                        email=row["email"],
                    )
                )

                if profile is None:
                    db.add(
                        StudentProfile(
                            email=row["email"],
                            first_name=row["prenom"],
                            last_name=row["nom"],
                            phone=None,
                            hashed_password=None,
                            is_active=True,
                            student_id=row["matricule"],
                            program=row["filiere"],
                            level=row["niveau"],
                            group=row["groupe"],
                        )
                    )
                else:
                    profile.email = row["email"]
                    profile.first_name = row["prenom"]
                    profile.last_name = row["nom"]
                    profile.student_id = row["matricule"]
                    profile.program = row["filiere"]
                    profile.level = row["niveau"]
                    profile.group = row["groupe"]
                    db.add(profile)

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

    return ImportResponse(imported=len(prepared_rows), errors=0, error_report=[], history_id=history.id)
