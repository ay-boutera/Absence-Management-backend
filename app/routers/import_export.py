import csv
import io
import uuid
from datetime import datetime, date as dt_date
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_bearer, require_admin_or_teacher_bearer
from app.models.academic import (
    Absence,
    ImportHistory,
    ImportType,
    Module,
    PlanningSession,
    Salle,
    SessionType,
    Student,
)
from app.models.user import User, UserRole
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
    current_user: User = Depends(require_admin_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Import a students CSV with partial import and row-level error reporting.

    Expected UTF-8 comma-delimited columns:
    matricule, nom, prenom, filiere, niveau, groupe, email

    Example success response:
    {
      "imported": 42,
      "errors": 3,
      "error_report": [
        {"line": 5, "field": "email", "reason": "Invalid email format"}
      ],
      "history_id": "4e3de31e-d1ac-43bb-8abf-58cd4b5ef9e6"
    }
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
    imported_count = 0
    total_rows = 0

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        total_rows += 1

        matricule = (row.get("matricule") or "").strip()
        if not matricule:
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="matricule",
                    reason="Matricule is required",
                )
            )
            continue

        email_value = (row.get("email") or "").strip()
        try:
            email_adapter.validate_python(email_value)
        except (ValidationError, ValueError):
            error_report.append(
                ImportErrorItem(
                    line=line_number,
                    field="email",
                    reason="Invalid email format",
                )
            )
            continue

        student_result = await db.execute(
            select(Student).where(Student.matricule == matricule)
        )
        student = student_result.scalar_one_or_none()

        if student is None:
            student = Student(
                matricule=matricule,
                nom=(row.get("nom") or "").strip(),
                prenom=(row.get("prenom") or "").strip(),
                filiere=(row.get("filiere") or "").strip(),
                niveau=(row.get("niveau") or "").strip(),
                groupe=(row.get("groupe") or "").strip(),
                email=email_value,
            )
            db.add(student)
        else:
            await db.execute(
                update(Student)
                .where(Student.id == student.id)
                .values(
                    nom=(row.get("nom") or "").strip(),
                    prenom=(row.get("prenom") or "").strip(),
                    filiere=(row.get("filiere") or "").strip(),
                    niveau=(row.get("niveau") or "").strip(),
                    groupe=(row.get("groupe") or "").strip(),
                    email=email_value,
                )
            )

        imported_count += 1

    history = ImportHistory(
        user_id=current_user.id,
        filename=file.filename or "students.csv",
        import_type=ImportType.STUDENTS,
        total_rows=total_rows,
        success_count=imported_count,
        error_count=len(error_report),
    )
    db.add(history)
    await db.flush()

    return ImportResponse(
        imported=imported_count,
        errors=len(error_report),
        error_report=error_report,
        history_id=uuid.UUID(str(history.id)),
    )


@router.post(
    "/import/planning",
    response_model=ImportResponse,
    summary="Import planning sessions from CSV",
)
async def import_planning_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin_bearer),
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

        teacher_result = await db.execute(
            select(User).where(
                and_(
                    User.id == teacher_uuid,
                    User.role == UserRole.TEACHER,
                )
            )
        )
        teacher = teacher_result.scalar_one_or_none()
        if not teacher:
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
                id_enseignant=teacher.id,
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
                    id_enseignant=teacher.id,
                )
            )

        imported_count += 1

    history = ImportHistory(
        user_id=current_user.id,
        filename=file.filename or "planning.csv",
        import_type=ImportType.PLANNING,
        total_rows=total_rows,
        success_count=imported_count,
        error_count=len(error_report),
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
    current_user: User = Depends(require_admin_or_teacher_bearer),
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
            Student.matricule,
            Student.nom,
            Student.prenom,
            Student.filiere,
            Student.groupe,
            Module.code,
            Module.nom,
            PlanningSession.type_seance,
            PlanningSession.date,
            PlanningSession.heure_debut,
            PlanningSession.heure_fin,
            Absence.statut_justificatif,
        )
        .select_from(Absence)
        .join(Student, Student.matricule == Absence.student_matricule)
        .join(PlanningSession, PlanningSession.id == Absence.planning_session_id)
        .join(Module, Module.code == PlanningSession.code_module)
    )

    filters = []
    if filiere:
        filters.append(Student.filiere == filiere)
    if code_module:
        filters.append(Module.code == code_module)
    if date_from:
        filters.append(PlanningSession.date >= date_from)
    if date_to:
        filters.append(PlanningSession.date <= date_to)
    if matricule_etudiant:
        filters.append(Student.matricule == matricule_etudiant)

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

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Total-Count": str(total_count),
        "X-Page": str(page),
        "X-Page-Size": str(page_size),
    }

    return StreamingResponse(iter([csv_bytes]), media_type="text/csv", headers=headers)
