import csv
import io
from datetime import datetime
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import and_, func, select
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
    Student as AcademicStudent,
)
from app.models.user import Account, Student as StudentProfile, UserRole
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
    - Validate all rows before any write
    - Abort whole import on any validation/duplicate error
    - Duplicate check key: matricule already existing in DB

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

    academic_students_result = await db.execute(
        select(AcademicStudent).where(AcademicStudent.matricule.in_(matricules))
    )
    existing_academic_matricules = {
        student.matricule for student in academic_students_result.scalars().all()
    }

    profiles_result = await db.execute(
        select(StudentProfile)
        .options(selectinload(StudentProfile.user))
        .where(StudentProfile.student_id.in_(matricules))
    )
    profiles_by_student_id = {
        profile.student_id: profile for profile in profiles_result.scalars().all()
    }

    accounts_result = await db.execute(
        select(Account)
        .options(selectinload(Account.student_profile))
        .where(func.lower(Account.email).in_(emails))
    )
    accounts_by_email = {
        account.email.lower(): account for account in accounts_result.scalars().all()
    }

    prepared_rows: list[dict[str, object]] = []
    for line_number, row in parsed_rows:
        matricule = row["matricule"]
        email_value = row["email"]

        if matricule in existing_academic_matricules or matricule in profiles_by_student_id:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="matricule",
                    reason=f"Étudiant déjà importé — matricule {matricule} existe déjà",
                )
            )
            continue

        selected_account: Account | None = None
        selected_profile: StudentProfile | None = None
        account_with_email = accounts_by_email.get(email_value)

        if account_with_email is not None:
            if account_with_email.role != UserRole.STUDENT:
                error_report.append(
                    ImportErrorItem(
                        line=line_number,
                        field="email",
                        reason=f"Email déjà utilisé — email {email_value} existe déjà",
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
                        reason=f"Email déjà utilisé — email {email_value} existe déjà",
                    )
                )
                continue
            selected_account = account_with_email
            selected_profile = account_with_email.student_profile

        prepared_rows.append(
            {
                "row": row,
                "account": selected_account,
                "student_profile": selected_profile,
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
                account = item["account"]
                student_profile = item["student_profile"]

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


# NOTE: POST /import/planning is now handled by app.routers.planning
# (see routers/planning.py — full validation + upsert semantics)



@router.get(
    "/export/absences",
    summary="Export absences as CSV",
)
async def export_absences_csv(
    matricule_etudiant: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: Account = Depends(require_can_export_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Export absences to CSV.

    CSV columns:
    matricule, nom_etudiant, prenom_etudiant, filiere, groupe, statut_justificatif
    """
    base_query = (
        select(
            AcademicStudent.matricule,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            AcademicStudent.filiere,
            AcademicStudent.groupe,
            Absence.statut_justificatif,
        )
        .select_from(Absence)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
    )

    filters = []
    if matricule_etudiant:
        filters.append(AcademicStudent.matricule == matricule_etudiant)

    if getattr(current_user.role, "value", str(current_user.role)) == UserRole.TEACHER.value:
        # Teachers see absences for their own sessions only
        from app.models.user import Teacher as TeacherModel
        from app.models.academic import PlanningSession as PS
        teacher_result = await db.execute(
            select(TeacherModel).where(TeacherModel.user_id == current_user.id)
        )
        teacher = teacher_result.scalar_one_or_none()
        if teacher:
            base_query = base_query.join(
                PS, PS.id == Absence.planning_session_id
            ).where(PS.teacher_id == teacher.id)

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
                row[5] or "",
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
