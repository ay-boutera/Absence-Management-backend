import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_bearer
from app.models.academic import ImportHistory, ImportType
from app.models.user import Account, Teacher, UserRole
from app.schemas.import_export import ImportResponse


router = APIRouter(prefix="/import", tags=["Teachers"])
email_adapter = TypeAdapter(EmailStr)


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": "Encodage invalide: le fichier doit être en UTF-8",
            },
        ) from exc


def _validate_header(actual_columns: list[str] | None, expected_columns: list[str]) -> None:
    if not actual_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": "En-tête CSV manquant",
            },
        )

    if len(actual_columns) == 1 and ";" in actual_columns[0]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": "Le séparateur CSV doit être une virgule",
            },
        )

    missing = [column for column in expected_columns if column not in actual_columns]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": f"Colonnes manquantes: {missing}",
            },
        )


@router.post(
    "/teachers",
    response_model=ImportResponse,
    summary="Import teachers from CSV",
    description=(
        "Importe des enseignants depuis un CSV UTF-8 (délimiteur virgule).\n\n"
        "Colonnes attendues: id_enseignant, nom, prenom, email, grade, departement.\n"
        "Mapping: id_enseignant -> Teacher.employee_id, nom -> Teacher.last_name, "
        "prenom -> Teacher.first_name, email -> Teacher.email/User.email, "
        "specialization = '{grade} | {departement}'.\n\n"
        "Comportement: validation complète de toutes les lignes avant écriture en base. "
        "S'il existe des erreurs, aucun enregistrement n'est créé/modifié (all-or-nothing)."
    ),
)
async def import_teachers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_admin_bearer),
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

    stream = io.StringIO(content)
    reader = csv.DictReader(stream, delimiter=",")
    _validate_header(reader.fieldnames, expected_columns)

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
    emails = [row["email"] for _, row in parsed_rows if row["email"]]

    if employee_ids:
        existing_teachers_result = await db.execute(
            select(Teacher.employee_id).where(Teacher.employee_id.in_(employee_ids))
        )
        existing_employee_ids = {value for value in existing_teachers_result.scalars().all() if value}
    else:
        existing_employee_ids = set()

    if emails:
        existing_users_result = await db.execute(
            select(func.lower(Account.email)).where(func.lower(Account.email).in_(emails))
        )
        existing_emails = {value for value in existing_users_result.scalars().all() if value}
    else:
        existing_emails = set()

    for line_number, row in parsed_rows:
        employee_id = row["id_enseignant"]
        email_value = row["email"]

        if employee_id and employee_id in existing_employee_ids:
            error_report.append(
                {
                    "line": line_number,
                    "field": "id_enseignant",
                    "reason": f"Enseignant déjà importé — id_enseignant {employee_id} existe déjà",
                }
            )

        if email_value and email_value in existing_emails:
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

    teacher_by_employee_id: dict[str, Teacher] = {}
    account_by_email: dict[str, Account] = {}
    if employee_ids:
        teacher_rows = await db.execute(select(Teacher).where(Teacher.employee_id.in_(employee_ids)))
        teacher_by_employee_id = {
            teacher.employee_id: teacher
            for teacher in teacher_rows.scalars().all()
            if teacher.employee_id is not None
        }
    if emails:
        account_rows = await db.execute(select(Account).where(func.lower(Account.email).in_(emails)))
        account_by_email = {account.email.lower(): account for account in account_rows.scalars().all()}

    imported_count = 0
    history: ImportHistory | None = None
    try:
        async with db.begin_nested():
            for _, row in parsed_rows:
                employee_id = row["id_enseignant"]
                email_value = row["email"]
                specialization_value = f"{row['grade']} | {row['departement']}"

                account = account_by_email.get(email_value)
                if account is None:
                    account = Account(
                        email=email_value,
                        first_name=row["prenom"],
                        last_name=row["nom"],
                        phone=None,
                        hashed_password=None,
                        role=UserRole.TEACHER,
                        is_active=True,
                    )
                    db.add(account)
                    await db.flush()
                else:
                    account.email = email_value
                    account.first_name = row["prenom"]
                    account.last_name = row["nom"]
                    account.role = UserRole.TEACHER
                    db.add(account)

                teacher = teacher_by_employee_id.get(employee_id)
                if teacher is None:
                    teacher = Teacher(
                        user_id=account.id,
                        employee_id=employee_id,
                        specialization=specialization_value,
                    )
                    db.add(teacher)
                else:
                    teacher.user_id = account.id
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
