"""
routers/planning.py — Planning Import & Schedule Endpoints
===========================================================

Routes
------
POST /api/v1/import/planning          — Admin only: import a weekly timetable CSV
GET  /api/v1/planning/my-schedule     — Any authenticated user: see own timetable
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import datetime, time
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.helpers.permissions import (
    get_current_user_bearer,
    require_can_import_data_bearer,
)
from app.config.enums import SessionType
from app.models import ImportHistory, ImportType, PlanningSession
from app.models import Teacher, UserRole
from app.models.student import Student as StudentUser
from app.schemas.planning import (
    PlanningImportResponse,
    PlanningSessionOut,
    ScheduleResponse,
    TeacherInfo,
)

router = APIRouter(tags=["Planning"])

# ── Constants ─────────────────────────────────────────────────────────────────
import string
VALID_YEARS = {"1CP", "2CP", "1CS", "2CS", "3CS"}
YEARS_WITH_SPECIALITY = {"2CS", "3CS"}
YEARS_WITHOUT_SPECIALITY = {"1CP", "2CP", "1CS"}
VALID_SPECIALITIES = {"ISI", "SIW", "IASD", "CyS"}
VALID_SECTIONS = set(string.ascii_uppercase)
GROUP_RE = re.compile(r"^G\d+$")
VALID_DAYS = {"Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi"}
VALID_TYPES = set(e.value for e in SessionType)
VALID_SEMESTERS = {"S1", "S2"}
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

REQUIRED_COLUMNS = [
    "year", "section", "speciality", "semester",
    "day", "time_start", "time_end",
    "type", "subject", "teacher", "room", "group",
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _decode_utf8(content: bytes) -> str:
    """Decode bytes as UTF-8, stripping BOM if present (LibreOffice/Excel habit)."""
    try:
        return content.decode("utf-8-sig")  # utf-8-sig strips the BOM automatically
    except UnicodeDecodeError:
        try:
            return content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "Format CSV invalide", "detail": "Encodage non reconnu (attendu UTF-8)"},
            ) from exc


def _detect_delimiter(first_line: str) -> str:
    """Return ',' or ';' based on whichever appears more in the header row."""
    if first_line.count(";") > first_line.count(","):
        return ";"
    return ","

def _parse_time(raw: str) -> Optional[time]:
    """Return a time object from HH:MM string, or None if invalid."""
    if not TIME_RE.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _make_upsert_key(row: dict) -> tuple:
    """Return the tuple that uniquely identifies a planning session."""
    return (
        row["year"],
        row["section"] or None,
        row["speciality"] or None,
        row["semester"],
        row["day"],
        row["time_start"],   # HH:MM string for dedup
        row["type"],
        row["subject"],
        row["group"] or None,
    )


def _fmt_time(t: Optional[time]) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


# ── POST /import/planning ─────────────────────────────────────────────────────
@router.post(
    "/import/planning",
    response_model=PlanningImportResponse,
    summary="Import planning sessions from CSV",
    description="""
Import a weekly timetable from a UTF-8, comma-delimited CSV file.

**Required columns (order flexible):**
`year, section, speciality, semester, day, time_start, time_end, type, subject, teacher, room, group`

- Lines starting with `#` are skipped (comments).
- All-or-nothing: if **any** row fails validation, **nothing** is written.
- On success, existing sessions matching the upsert key are updated.
- Multiple teachers can be assigned to a session by separating their IDs with a `/` (e.g. `ENS126 / ENS137`). Unrecognized teacher IDs will log a warning and be ignored without failing the row.

**Auth:** Admin only (JWT).
""",
    responses={
        400: {"description": "CSV format error (bad encoding, missing columns)"},
        409: {"description": "Validation errors found — nothing imported"},
        200: {"description": "Sessions imported/updated successfully"},
    },
)
async def import_planning_csv(
    file: UploadFile = File(..., description="UTF-8 comma-delimited CSV file"),
    current_user=Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    # ── Level 1: file decoding ────────────────────────────────────────────────
    raw = await file.read()
    content = _decode_utf8(raw)

    # Filter comment/blank lines before feeding to csv.DictReader
    filtered_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        # A line is completely empty if it's just commas/semicolons (Excel sometimes outputs empty cells as separators)
        truly_empty = not stripped.replace(",", "").replace(";", "").replace('"', "").strip()
        
        if stripped.startswith("#") or truly_empty:
            continue
        filtered_lines.append(line)

    if not filtered_lines:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": "Fichier vide"},
        )

    csv_text = "\n".join(filtered_lines)

    # Auto-detect delimiter (comma vs semicolon — LibreOffice French locale)
    delimiter = _detect_delimiter(filtered_lines[0])

    stream = io.StringIO(csv_text)
    reader = csv.DictReader(stream, delimiter=delimiter)

    # Validate header — strip whitespace from fieldnames for robustness
    raw_fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    reader.fieldnames = raw_fieldnames  # type: ignore[assignment]
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in raw_fieldnames]
    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": f"Colonnes manquantes: {missing_cols}. "
                        f"Délimiteur détecté: '{delimiter}'. "
                        f"En-tête reçu: {raw_fieldnames}",
            },
        )

    # ── Level 2: per-row validation ───────────────────────────────────────────
    error_report: list[dict] = []
    parsed_rows: list[dict] = []   # cleaned, validated rows

    # Pre-fetch all teacher employee_ids for O(1) lookup
    teachers_result = await db.execute(select(Teacher))
    all_teachers: list[Teacher] = list(teachers_result.scalars().all())
    teacher_by_employee_id: dict[str, Teacher] = {
        t.employee_id: t for t in all_teachers if t.employee_id
    }

    def add_error(line: int, field: str, reason: str) -> None:
        error_report.append({"line": line, "field": field, "reason": reason})

    data_line = 0
    for row in reader:
        # Skip comment/blank rows that slipped through (shouldn't happen after pre-filter)
        raw_values = [str(v or "").strip() for v in row.values()]
        if not any(raw_values):
            continue

        data_line += 1
        n = data_line  # human-readable line number (1-indexed in data rows)

        def get(col: str) -> str:
            return (row.get(col) or "").strip()

        year         = get("year")
        section      = get("section") or None
        speciality   = get("speciality") or None
        semester     = get("semester")
        day          = get("day")
        time_start_s = get("time_start")
        time_end_s   = get("time_end")
        sess_type    = get("type")
        subject      = get("subject")
        teacher_raw  = get("teacher")
        room         = get("room") or None
        group        = get("group") or None

        row_errors = 0

        # --- year ---
        if year not in VALID_YEARS:
            add_error(n, "year",
                    f"valeur '{year}' invalide. Valeurs acceptées: 1CP,2CP,1CS,2CS,3CS")
            row_errors += 1

        # --- section ---
        if section and section not in VALID_SECTIONS:
            add_error(n, "section", f"valeur '{section}' invalide. Attendu: lettre alphabétique (A-Z)")
            row_errors += 1

        # --- group ---
        if group and not GROUP_RE.match(group):
            add_error(n, "group", f"valeur '{group}' invalide. Attendu: Gx (ex: G1, G2)")
            row_errors += 1

        # --- semester ---
        if semester not in VALID_SEMESTERS:
            add_error(n, "semester", "doit être S1 ou S2")
            row_errors += 1

        # --- day ---
        if day not in VALID_DAYS:
            add_error(n, "day", f"valeur '{day}' invalide")
            row_errors += 1

        # --- time_start ---
        parsed_start = _parse_time(time_start_s)
        if parsed_start is None:
            add_error(n, "time_start", "format invalide, attendu HH:MM")
            row_errors += 1

        # --- time_end ---
        parsed_end = _parse_time(time_end_s)
        if parsed_end is None:
            add_error(n, "time_end", "format invalide, attendu HH:MM")
            row_errors += 1

        # --- time_end > time_start ---
        if parsed_start and parsed_end and parsed_end <= parsed_start:
            add_error(n, "time_end", "time_end doit être après time_start")
            row_errors += 1

        # --- type ---
        if sess_type not in VALID_TYPES:
            add_error(n, "type", f"valeur '{sess_type}' invalide")
            row_errors += 1

        # --- subject ---
        if not subject:
            add_error(n, "subject", "champ obligatoire manquant")
            row_errors += 1

        # --- business rules: speciality vs year ---
        if year in YEARS_WITH_SPECIALITY and not speciality:
            add_error(n, "speciality", f"obligatoire pour les années 2CS et 3CS")
            row_errors += 1
        elif year in YEARS_WITHOUT_SPECIALITY and speciality:
            add_error(n, "speciality",
                    f"ne doit pas être renseigné pour {year}")
            row_errors += 1
        elif speciality and speciality not in VALID_SPECIALITIES:
            add_error(n, "speciality", f"valeur '{speciality}' invalide")
            row_errors += 1

        # --- 3CS / S2 (stage) ---
        if year == "3CS" and semester == "S2":
            add_error(n, "semester",
                    "3CS n'a pas de semestre S2 (stage en entreprise — S1 uniquement)")
            row_errors += 1

        # --- teacher lookup ---
        found_teachers: list[Teacher] = []
        if teacher_raw:
            raw_ids = [t.strip() for t in teacher_raw.split("/") if t.strip()]
            for raw_id in raw_ids:
                t_obj = teacher_by_employee_id.get(raw_id)
                if t_obj is None:
                    logger.warning(f"Ligne {n}: id_enseignant '{raw_id}' introuvable dans la base (ignoré).")
                else:
                    found_teachers.append(t_obj)

        parsed_rows.append({
            "line": n,
            "year": year,
            "section": section,
            "speciality": speciality,
            "semester": semester,
            "day": day,
            "time_start": time_start_s,
            "time_end": time_end_s,
            "parsed_start": parsed_start,
            "parsed_end": parsed_end,
            "type": sess_type,
            "subject": subject,
            "teacher_objs": found_teachers,
            "room": room,
            "group": group,
            "has_error": row_errors > 0,
        })

    # ── Level 3: intra-file duplicate detection ───────────────────────────────
    seen_keys: dict[tuple, int] = {}
    for pr in parsed_rows:
        if pr["has_error"]:
            continue
        key = _make_upsert_key(pr)
        n = pr["line"]
        if key in seen_keys:
            add_error(n, "session",
                    f"doublon détecté dans le fichier (même session en ligne {seen_keys[key]})")
            pr["has_error"] = True
        else:
            seen_keys[key] = n

    # ── All-or-nothing gate ───────────────────────────────────────────────────
    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "imported": 0,
                "updated": 0,
                "errors": len(error_report),
                "error_report": error_report,
            },
        )

    # ── Upsert in single atomic transaction ───────────────────────────────────
    imported_count = 0
    updated_count = 0

    try:
        async with db.begin_nested():
            for pr in parsed_rows:
                if pr["has_error"]:
                    continue

                # Build the uniqueness filter matching uq_planning_session
                filters = [
                    PlanningSession.year == pr["year"],
                    PlanningSession.semester == pr["semester"],
                    PlanningSession.day == pr["day"],
                    PlanningSession.time_start == pr["parsed_start"],
                    PlanningSession.type == pr["type"],
                    PlanningSession.subject == pr["subject"],
                ]
                # nullable columns: use IS NULL or =
                if pr["section"] is None:
                    filters.append(PlanningSession.section.is_(None))
                else:
                    filters.append(PlanningSession.section == pr["section"])

                if pr["speciality"] is None:
                    filters.append(PlanningSession.speciality.is_(None))
                else:
                    filters.append(PlanningSession.speciality == pr["speciality"])

                if pr["group"] is None:
                    filters.append(PlanningSession.group.is_(None))
                else:
                    filters.append(PlanningSession.group == pr["group"])

                existing_result = await db.execute(
                    select(PlanningSession).where(and_(*filters))
                )
                session = existing_result.scalar_one_or_none()

                if session is None:
                    session = PlanningSession(
                        year=pr["year"],
                        section=pr["section"],
                        speciality=pr["speciality"],
                        semester=pr["semester"],
                        day=pr["day"],
                        time_start=pr["parsed_start"],
                        time_end=pr["parsed_end"],
                        type=pr["type"],
                        subject=pr["subject"],
                        room=pr["room"],
                        group=pr["group"],
                        teachers=pr["teacher_objs"],
                    )
                    db.add(session)
                    imported_count += 1
                else:
                    # Update mutable fields only
                    session.time_end = pr["parsed_end"]
                    session.room = pr["room"]
                    session.teachers = pr["teacher_objs"]
                    db.add(session)
                    updated_count += 1

            # Save import history
            history = ImportHistory(
                user_id=current_user.id,
                filename=file.filename or "planning.csv",
                import_type=ImportType.PLANNING,
                total_rows=len(parsed_rows),
                success_count=imported_count + updated_count,
                error_count=0,
            )
            db.add(history)
            await db.flush()

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'import du planning",
        ) from exc

    return PlanningImportResponse(
        imported=imported_count,
        updated=updated_count,
        errors=0,
        error_report=[],
        history_id=history.id,
    )


# ── GET /planning/my-schedule ─────────────────────────────────────────────────
@router.get(
    "/planning/my-schedule",
    response_model=ScheduleResponse,
    summary="Get my weekly schedule",
    description="""
Returns the planning sessions for the authenticated user.

- **Teacher**: all sessions where `teacher_id` matches the logged-in teacher.
- **Student**: sessions matching the student's `year`, `section`, `speciality`,
    and either `group` matches or `group IS NULL` (whole-promo Cours).
- **Admin**: returns all sessions (unfiltered by person).

**Optional query params:** `semester` (S1|S2), `day` (Dimanche|…|Jeudi)
""",
)
async def my_schedule(
    semester: Optional[str] = Query(
        default=None,
        description="Filter by semester: S1 or S2",
        pattern="^(S1|S2)$",
    ),
    day: Optional[str] = Query(
        default=None,
        description="Filter by day of week",
    ),
    current_user=Depends(get_current_user_bearer),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.role

    # Base query with teacher eagerly loaded
    base_q = select(PlanningSession).options(
        selectinload(PlanningSession.teachers)
    )

    if role == UserRole.TEACHER:
        # Find the teacher profile
        teacher_result = await db.execute(
            select(Teacher).where(Teacher.id == current_user.id)
        )
        teacher = teacher_result.scalar_one_or_none()
        if teacher is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profil enseignant introuvable.",
            )
        base_q = base_q.where(PlanningSession.teachers.any(Teacher.id == teacher.id))

    elif role == UserRole.STUDENT:
        student_result = await db.execute(
            select(StudentUser).where(StudentUser.id == current_user.id)
        )
        student = student_result.scalar_one_or_none()
        if student is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profil étudiant introuvable.",
            )

        # Map student profile fields to planning session columns.
        # StudentUser.level → year, StudentUser.program → speciality/section context
        # StudentUser.group → group (G1, G2, etc.)
        student_year = student.level            # e.g. "1CS"
        student_group = student.group           # e.g. "G2" or None

        # Derive section and speciality from program field.
        # Convention: program may encode "section:speciality" — adapt as needed.
        # For now we treat program as speciality for 2CS/3CS, and section separately.
        student_speciality: Optional[str] = None
        student_section: Optional[str] = None

        if student_year in YEARS_WITH_SPECIALITY:
            # program stores speciality for upper years
            student_speciality = student.program or None
        else:
            # program may store section for lower years; leave both None if absent
            student_section = student.program if student.program else None

        session_filters = [PlanningSession.year == student_year]

        if student_section is not None:
            session_filters.append(
                or_(
                    PlanningSession.section == student_section,
                    PlanningSession.section.is_(None),
                )
            )

        if student_speciality is not None:
            session_filters.append(
                or_(
                    PlanningSession.speciality == student_speciality,
                    PlanningSession.speciality.is_(None),
                )
            )

        # Group: student sees sessions for their group OR whole-promo sessions
        if student_group:
            session_filters.append(
                or_(
                    PlanningSession.group == student_group,
                    PlanningSession.group.is_(None),
                )
            )
        else:
            session_filters.append(PlanningSession.group.is_(None))

        base_q = base_q.where(and_(*session_filters))

    # role == ADMIN → no additional filter (sees everything)

    # Optional query param filters
    if semester:
        base_q = base_q.where(PlanningSession.semester == semester)
    if day:
        base_q = base_q.where(PlanningSession.day == day)

    # Order for readability: day, time
    DAY_ORDER = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi"]
    base_q = base_q.order_by(
        PlanningSession.semester,
        PlanningSession.time_start,
    )

    result = await db.execute(base_q)
    sessions: list[PlanningSession] = list(result.scalars().all())

    def _serialize(s: PlanningSession) -> PlanningSessionOut:
        teacher_infos = [
            TeacherInfo(
                id=t.id,
                employee_id=t.employee_id,
                first_name=t.first_name,
                last_name=t.last_name,
            )
            for t in s.teachers
        ]
        return PlanningSessionOut(
            id=s.id,
            day=s.day,
            time_start=_fmt_time(s.time_start),
            time_end=_fmt_time(s.time_end),
            type=s.type,
            subject=s.subject,
            room=s.room,
            group=s.group,
            year=s.year,
            section=s.section,
            speciality=s.speciality,
            semester=s.semester,
            teachers=teacher_infos,
        )

    return ScheduleResponse(
        total=len(sessions),
        sessions=[_serialize(s) for s in sessions],
    )
