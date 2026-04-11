import csv
import io
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.models.user import Account, Teacher


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _decode_utf8(file_content: bytes) -> str:
    return file_content.decode("utf-8")


def _validate_columns(actual_columns: list[str] | None, expected_columns: list[str]) -> list[dict]:
    errors: list[dict] = []

    if not actual_columns:
        errors.append({"line": 1, "column": "header", "reason": "CSV header is missing"})
        return errors

    if len(actual_columns) == 1 and ";" in actual_columns[0]:
        errors.append(
            {
                "line": 1,
                "column": "header",
                "reason": "Invalid delimiter. CSV must be comma-delimited",
            }
        )

    for column in expected_columns:
        if column not in actual_columns:
            errors.append(
                {
                    "line": 1,
                    "column": column,
                    "reason": "Missing required column",
                }
            )

    return errors


async def import_teachers_csv(file_content: bytes, db: AsyncSession) -> dict:
    expected_columns = [
        "id_enseignant",
        "nom",
        "prenom",
        "email",
        "grade",
        "departement",
    ]

    try:
        content = _decode_utf8(file_content)
    except UnicodeDecodeError:
        return {
            "created": 0,
            "updated": 0,
            "errors": [
                {
                    "line": 0,
                    "column": "file",
                    "reason": "Invalid file encoding. CSV must be UTF-8",
                }
            ],
        }

    stream = io.StringIO(content)
    reader = csv.DictReader(stream, delimiter=",")

    header_errors = _validate_columns(reader.fieldnames, expected_columns)
    if header_errors:
        return {"created": 0, "updated": 0, "errors": header_errors}

    errors: list[dict] = []
    created = 0
    updated = 0
    seen_in_file: set[str] = set()

    employee_ids: list[str] = []
    emails: list[str] = []
    parsed_rows: list[tuple[int, dict[str, str]]] = []

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        cleaned = {column: (row.get(column) or "").strip() for column in expected_columns}

        if not cleaned["id_enseignant"]:
            errors.append(
                {
                    "line": line_number,
                    "column": "id_enseignant",
                    "reason": "id_enseignant is required",
                }
            )
            continue

        if not cleaned["email"]:
            errors.append(
                {
                    "line": line_number,
                    "column": "email",
                    "reason": "email is required",
                }
            )
            continue

        email_lower = cleaned["email"].lower()
        cleaned["email"] = email_lower
        if not EMAIL_PATTERN.match(email_lower):
            errors.append(
                {
                    "line": line_number,
                    "column": "email",
                    "reason": "Invalid email format",
                }
            )
            continue

        employee_ids.append(cleaned["id_enseignant"])
        emails.append(email_lower)
        parsed_rows.append((line_number, cleaned))

    if not parsed_rows:
        return {"created": 0, "updated": 0, "errors": errors}

    teachers_result = await db.execute(
        select(Teacher).where(Teacher.employee_id.in_(employee_ids))
    )
    teachers_by_employee_id = {
        teacher.employee_id: teacher for teacher in teachers_result.scalars().all()
    }

    accounts_result = await db.execute(
        select(Account).where(func.lower(Account.email).in_(emails))
    )
    accounts_by_email = {account.email.lower(): account for account in accounts_result.scalars().all()}

    for line_number, row in parsed_rows:
        employee_id = row["id_enseignant"]
        email_value = row["email"]
        teacher = teachers_by_employee_id.get(employee_id)
        account_with_email = accounts_by_email.get(email_value)

        if teacher is None and account_with_email is not None and account_with_email.role != UserRole.TEACHER:
            errors.append(
                {
                    "line": line_number,
                    "column": "email",
                    "reason": "Email is already used by a non-teacher account",
                }
            )
            continue

        specialization_value = f"{row['grade']} | {row['departement']}"

        if teacher is None:
            if account_with_email is None:
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
                accounts_by_email[email_value] = account
            else:
                account = account_with_email
                account.first_name = row["prenom"]
                account.last_name = row["nom"]
                db.add(account)

            teacher = Teacher(
                user_id=account.id,
                employee_id=employee_id,
                specialization=specialization_value,
            )
            db.add(teacher)
            await db.flush()

            teachers_by_employee_id[employee_id] = teacher
            if employee_id in seen_in_file:
                updated += 1
            else:
                created += 1
                seen_in_file.add(employee_id)
            continue

        account = await db.get(Account, teacher.user_id)
        if account is None:
            errors.append(
                {
                    "line": line_number,
                    "column": "id_enseignant",
                    "reason": "Teacher profile is orphaned (no linked account)",
                }
            )
            continue

        if account.email.lower() != email_value and account_with_email is not None and account_with_email.id != account.id:
            errors.append(
                {
                    "line": line_number,
                    "column": "email",
                    "reason": "Email is already linked to another account",
                }
            )
            continue

        account.email = email_value
        account.first_name = row["prenom"]
        account.last_name = row["nom"]
        teacher.specialization = specialization_value
        db.add(account)
        db.add(teacher)

        updated += 1
        seen_in_file.add(employee_id)
        accounts_by_email[email_value] = account

    await db.flush()

    return {"created": created, "updated": updated, "errors": errors}
