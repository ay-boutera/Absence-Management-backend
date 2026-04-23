import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_bearer
from app.helpers.role_users import get_user_by_email
from app.models import ImportHistory, ImportType, Teacher
from app.schemas.import_export import ImportResponse

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

    expected_columns = ["id_enseignant", "nom", "prenom", "email", "grade", "departement"]

    csv_stream = io.StringIO(content)
    reader = csv.DictReader(csv_stream, delimiter=",")
    raw_fieldnames = list(reader.fieldnames or [])

    if not raw_fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": "En-tête CSV manquant."},
        )
    if len(raw_fieldnames) == 1 and ";" in raw_fieldnames[0]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": "Délimiteur invalide. Le CSV doit être séparé par des virgules."},
        )
    missing = [c for c in expected_columns if c not in raw_fieldnames]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": f"Colonnes manquantes: {', '.join(missing)}"},
        )

    error_report: list[dict] = []
    parsed_rows: list[tuple[int, dict[str, str]]] = []
    total_rows = 0
    seen_employee_ids: set[str] = set()
    seen_emails: set[str] = set()

    for line_number, row in enumerate(reader, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1
        cleaned = {col: (row.get(col) or "").strip() for col in expected_columns}

        for field, value in cleaned.items():
            if not value:
                error_report.append({"line": line_number, "field": field, "reason": f"{field} est requis"})

        employee_id = cleaned["id_enseignant"]
        email_value = cleaned["email"].lower()
        cleaned["email"] = email_value

        if employee_id:
            if employee_id in seen_employee_ids:
                error_report.append({
                    "line": line_number,
                    "field": "id_enseignant",
                    "reason": f"id_enseignant dupliqué dans le fichier — id_enseignant {employee_id} apparaît plusieurs fois",
                })
            else:
                seen_employee_ids.add(employee_id)

        if email_value:
            if email_value in seen_emails:
                error_report.append({
                    "line": line_number, "field": "email",
                    "reason": f"Email dupliqué dans le fichier — email {email_value} apparaît plusieurs fois",
                })
            else:
                seen_emails.add(email_value)
            try:
                email_adapter.validate_python(email_value)
            except (ValidationError, ValueError):
                error_report.append({"line": line_number, "field": "email", "reason": "Format email invalide"})

        parsed_rows.append((line_number, cleaned))

    employee_ids = [row["id_enseignant"] for _, row in parsed_rows if row["id_enseignant"]]
    teacher_by_employee_id: dict[str, Teacher] = {}
    teacher_by_email: dict[str, Teacher] = {}

    if employee_ids:
        for t in (await db.execute(select(Teacher).where(Teacher.employee_id.in_(employee_ids)))).scalars().all():
            if t.employee_id:
                teacher_by_employee_id[t.employee_id] = t

    if seen_emails:
        for t in (await db.execute(select(Teacher).where(func.lower(Teacher.email).in_(seen_emails)))).scalars().all():
            teacher_by_email[t.email.lower()] = t

    for line_number, row in parsed_rows:
        employee_id = row["id_enseignant"]
        email_value = row["email"]

        existing_by_email = await get_user_by_email(db, email_value)
        if existing_by_email is not None and not isinstance(existing_by_email, Teacher):
            error_report.append({"line": line_number, "field": "email", "reason": f"Email déjà utilisé — email {email_value} existe déjà"})

        teacher = teacher_by_employee_id.get(employee_id)
        teacher_email = teacher_by_email.get(email_value)
        if teacher is None and teacher_email is not None:
            error_report.append({"line": line_number, "field": "email", "reason": f"Email déjà utilisé — email {email_value} existe déjà"})
        if teacher is not None and teacher.email.lower() != email_value and teacher_email is not None:
            error_report.append({"line": line_number, "field": "email", "reason": f"Email déjà utilisé — email {email_value} existe déjà"})

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"imported": 0, "errors": len(error_report), "error_report": error_report},
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

    return ImportResponse(imported=imported_count, errors=0, error_report=[], history_id=history.id)
