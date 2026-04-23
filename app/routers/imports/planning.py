"""
routers/imports/planning.py — Planning CSV Import
==================================================

POST /api/v1/import/planning

Imports a weekly timetable from CSV and, when a semester_start_date is
provided, materialises Session rows for every week of the semester so
teachers can immediately see their daily session list.

Session generation logic:
  - For each parsed planning row × each week in [start_date, start_date + weeks)
  - Compute the concrete date from the day-of-week
  - Find-or-create Module (by subject string as code)
  - Find-or-create Salle (by room code)
  - UPSERT Session with UNIQUE constraint (planning_session_id, teacher_id, date)
"""

from __future__ import annotations

import csv
import io
import logging
import re
import string
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import SessionStatusEnum, SessionType
from app.db import get_db
from app.helpers.permissions import require_can_import_data_bearer
from app.models import (
    ImportHistory,
    ImportType,
    Module,
    PlanningSession,
    Salle,
    Session,
    Teacher,
)
from app.schemas.planning import PlanningImportResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Imports"])

# ── Constants ──────────────────────────────────────────────────────────────────
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

# French day-of-week name → ISO weekday (Monday=0 … Sunday=6)
DAY_TO_WEEKDAY: dict[str, int] = {
    "Lundi": 0,
    "Mardi": 1,
    "Mercredi": 2,
    "Jeudi": 3,
    "Dimanche": 6,
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "Format CSV invalide", "detail": "Encodage non reconnu (attendu UTF-8)"},
            ) from exc


def _detect_delimiter(first_line: str) -> str:
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _parse_time(raw: str) -> Optional[time]:
    if not TIME_RE.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _make_upsert_key(row: dict) -> tuple:
    return (
        row["year"], row["section"] or None, row["speciality"] or None,
        row["semester"], row["day"], row["time_start"],
        row["type"], row["subject"], row["group"] or None,
    )


def _date_for_week(start_date: date, day_name: str, week_offset: int) -> Optional[date]:
    """Return the concrete date for *day_name* in the week starting at start_date + week_offset*7."""
    weekday = DAY_TO_WEEKDAY.get(day_name)
    if weekday is None:
        return None
    week_start = start_date + timedelta(weeks=week_offset)
    # Align week_start to Monday of that week
    week_monday = week_start - timedelta(days=week_start.weekday())
    return week_monday + timedelta(days=weekday)


async def _get_or_create_module(db: AsyncSession, subject: str) -> Module:
    result = await db.execute(select(Module).where(Module.code == subject))
    module = result.scalar_one_or_none()
    if module is None:
        module = Module(code=subject, nom=subject)
        db.add(module)
        await db.flush()
    return module


async def _get_or_create_salle(db: AsyncSession, room_code: str) -> Salle:
    result = await db.execute(select(Salle).where(Salle.code == room_code))
    salle = result.scalar_one_or_none()
    if salle is None:
        salle = Salle(code=room_code)
        db.add(salle)
        await db.flush()
    return salle


# ── POST /import/planning ──────────────────────────────────────────────────────
@router.post(
    "/import/planning",
    response_model=PlanningImportResponse,
    summary="Import planning sessions from CSV",
    description="""
Import a weekly timetable from a UTF-8, comma-delimited CSV file.

**Required columns:** `year, section, speciality, semester, day, time_start, time_end, type, subject, teacher, room, group`

- Lines starting with `#` are skipped (comments).
- All-or-nothing: any row error rejects the whole file.
- Multiple teachers per session: slash-separated IDs (e.g. `ENS126/ENS137`). Unknown IDs are warned and ignored.
- When `semester_start_date` is provided, concrete **Session** rows are generated
  for every week of the semester (default 16 weeks) so teachers see today's sessions immediately.

**Auth:** Admin only (JWT).
""",
    responses={
        400: {"description": "CSV format error"},
        409: {"description": "Validation errors — nothing imported"},
        200: {"description": "Sessions imported/updated successfully"},
    },
)
async def import_planning_csv(
    file: UploadFile = File(..., description="UTF-8 CSV timetable file"),
    semester_start_date: Optional[date] = Query(
        default=None,
        description="First day of the semester (YYYY-MM-DD). When set, concrete Session rows are generated.",
    ),
    semester_weeks: int = Query(
        default=16,
        ge=1,
        le=52,
        description="Number of weeks to generate sessions for (default 16).",
    ),
    current_user=Depends(require_can_import_data_bearer),
    db: AsyncSession = Depends(get_db),
):
    # ── Decode & filter ────────────────────────────────────────────────────────
    raw = await file.read()
    content = _decode_utf8(raw)

    filtered_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        truly_empty = not stripped.replace(",", "").replace(";", "").replace('"', "").strip()
        if stripped.startswith("#") or truly_empty:
            continue
        filtered_lines.append(line)

    if not filtered_lines:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Format CSV invalide", "detail": "Fichier vide"},
        )

    delimiter = _detect_delimiter(filtered_lines[0])
    stream = io.StringIO("\n".join(filtered_lines))
    reader = csv.DictReader(stream, delimiter=delimiter)

    raw_fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    reader.fieldnames = raw_fieldnames  # type: ignore[assignment]
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in raw_fieldnames]
    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Format CSV invalide",
                "detail": f"Colonnes manquantes: {missing_cols}. Délimiteur détecté: '{delimiter}'. En-tête reçu: {raw_fieldnames}",
            },
        )

    # ── Per-row validation ─────────────────────────────────────────────────────
    error_report: list[dict] = []
    parsed_rows: list[dict] = []

    teachers_result = await db.execute(select(Teacher))
    all_teachers: list[Teacher] = list(teachers_result.scalars().all())
    teacher_by_employee_id: dict[str, Teacher] = {
        t.employee_id: t for t in all_teachers if t.employee_id
    }

    def add_error(line: int, field: str, reason: str) -> None:
        error_report.append({"line": line, "field": field, "reason": reason})

    data_line = 0
    for row in reader:
        if not any(str(v or "").strip() for v in row.values()):
            continue

        data_line += 1
        n = data_line

        def get(col: str) -> str:
            return (row.get(col) or "").strip()

        year        = get("year")
        section     = get("section") or None
        speciality  = get("speciality") or None
        semester    = get("semester")
        day         = get("day")
        ts_str      = get("time_start")
        te_str      = get("time_end")
        sess_type   = get("type")
        subject     = get("subject")
        teacher_raw = get("teacher")
        room        = get("room") or None
        group       = get("group") or None
        row_errors  = 0

        if year not in VALID_YEARS:
            add_error(n, "year", f"valeur '{year}' invalide. Valeurs acceptées: 1CP,2CP,1CS,2CS,3CS")
            row_errors += 1
        if section and section not in VALID_SECTIONS:
            add_error(n, "section", f"valeur '{section}' invalide. Attendu: lettre A-Z")
            row_errors += 1
        if group and not GROUP_RE.match(group):
            add_error(n, "group", f"valeur '{group}' invalide. Attendu: Gx (ex: G1, G2)")
            row_errors += 1
        if semester not in VALID_SEMESTERS:
            add_error(n, "semester", "doit être S1 ou S2")
            row_errors += 1
        if day not in VALID_DAYS:
            add_error(n, "day", f"valeur '{day}' invalide")
            row_errors += 1

        parsed_start = _parse_time(ts_str)
        if parsed_start is None:
            add_error(n, "time_start", "format invalide, attendu HH:MM")
            row_errors += 1

        parsed_end = _parse_time(te_str)
        if parsed_end is None:
            add_error(n, "time_end", "format invalide, attendu HH:MM")
            row_errors += 1

        if parsed_start and parsed_end and parsed_end <= parsed_start:
            add_error(n, "time_end", "time_end doit être après time_start")
            row_errors += 1

        if sess_type not in VALID_TYPES:
            add_error(n, "type", f"valeur '{sess_type}' invalide")
            row_errors += 1
        if not subject:
            add_error(n, "subject", "champ obligatoire manquant")
            row_errors += 1

        if year in YEARS_WITH_SPECIALITY and not speciality:
            add_error(n, "speciality", "obligatoire pour les années 2CS et 3CS")
            row_errors += 1
        elif year in YEARS_WITHOUT_SPECIALITY and speciality:
            add_error(n, "speciality", f"ne doit pas être renseigné pour {year}")
            row_errors += 1
        elif speciality and speciality not in VALID_SPECIALITIES:
            add_error(n, "speciality", f"valeur '{speciality}' invalide")
            row_errors += 1

        if year == "3CS" and semester == "S2":
            add_error(n, "semester", "3CS n'a pas de semestre S2 (stage en entreprise)")
            row_errors += 1

        found_teachers: list[Teacher] = []
        if teacher_raw:
            for raw_id in [t.strip() for t in teacher_raw.split("/") if t.strip()]:
                t_obj = teacher_by_employee_id.get(raw_id)
                if t_obj is None:
                    logger.warning("Ligne %d: id_enseignant '%s' introuvable (ignoré).", n, raw_id)
                else:
                    found_teachers.append(t_obj)

        parsed_rows.append({
            "line": n, "year": year, "section": section, "speciality": speciality,
            "semester": semester, "day": day, "time_start": ts_str, "time_end": te_str,
            "parsed_start": parsed_start, "parsed_end": parsed_end,
            "type": sess_type, "subject": subject, "teacher_objs": found_teachers,
            "room": room, "group": group, "has_error": row_errors > 0,
        })

    # ── Intra-file duplicate detection ─────────────────────────────────────────
    seen_keys: dict[tuple, int] = {}
    for pr in parsed_rows:
        if pr["has_error"]:
            continue
        key = _make_upsert_key(pr)
        n = pr["line"]
        if key in seen_keys:
            add_error(n, "session", f"doublon détecté (même session en ligne {seen_keys[key]})")
            pr["has_error"] = True
        else:
            seen_keys[key] = n

    if error_report:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"imported": 0, "updated": 0, "errors": len(error_report), "error_report": error_report},
        )

    # ── Upsert PlanningSession rows + generate Session rows ────────────────────
    imported_count = 0
    updated_count = 0
    sessions_created = 0

    try:
        async with db.begin_nested():
            for pr in parsed_rows:
                if pr["has_error"]:
                    continue

                # Build PlanningSession filters matching the unique constraint
                filters = [
                    PlanningSession.year == pr["year"],
                    PlanningSession.semester == pr["semester"],
                    PlanningSession.day == pr["day"],
                    PlanningSession.time_start == pr["parsed_start"],
                    PlanningSession.type == pr["type"],
                    PlanningSession.subject == pr["subject"],
                ]
                for col, val in [("section", pr["section"]), ("speciality", pr["speciality"]), ("group", pr["group"])]:
                    attr = getattr(PlanningSession, col)
                    filters.append(attr.is_(None) if val is None else attr == val)

                existing_ps = (await db.execute(select(PlanningSession).where(and_(*filters)))).scalar_one_or_none()

                if existing_ps is None:
                    existing_ps = PlanningSession(
                        year=pr["year"], section=pr["section"], speciality=pr["speciality"],
                        semester=pr["semester"], day=pr["day"],
                        time_start=pr["parsed_start"], time_end=pr["parsed_end"],
                        type=pr["type"], subject=pr["subject"],
                        room=pr["room"], group=pr["group"],
                        teachers=pr["teacher_objs"],
                    )
                    db.add(existing_ps)
                    await db.flush()
                    imported_count += 1
                else:
                    existing_ps.time_end = pr["parsed_end"]
                    existing_ps.room = pr["room"]
                    existing_ps.teachers = pr["teacher_objs"]
                    db.add(existing_ps)
                    updated_count += 1

                # ── Session generation (one per teacher per week) ─────────────
                if semester_start_date and pr["teacher_objs"]:
                    module = await _get_or_create_module(db, pr["subject"])
                    salle = await _get_or_create_salle(db, pr["room"]) if pr["room"] else None

                    for teacher in pr["teacher_objs"]:
                        for week in range(semester_weeks):
                            session_date = _date_for_week(semester_start_date, pr["day"], week)
                            if session_date is None:
                                continue

                            # UNIQUE: (planning_session_id, teacher_id, date)
                            existing_session = (
                                await db.execute(
                                    select(Session).where(
                                        and_(
                                            Session.planning_session_id == existing_ps.id,
                                            Session.teacher_id == teacher.id,
                                            Session.date == session_date,
                                        )
                                    )
                                )
                            ).scalar_one_or_none()

                            if existing_session is None:
                                new_session = Session(
                                    planning_session_id=existing_ps.id,
                                    module_id=module.id,
                                    teacher_id=teacher.id,
                                    room_id=salle.id if salle else None,
                                    group=pr["group"],
                                    year=pr["year"],
                                    section=pr["section"],
                                    speciality=pr["speciality"],
                                    semester=pr["semester"],
                                    date=session_date,
                                    start_time=pr["parsed_start"],
                                    end_time=pr["parsed_end"],
                                    type=pr["type"],
                                    status=SessionStatusEnum.SCHEDULED,
                                    is_makeup=False,
                                )
                                db.add(new_session)
                                sessions_created += 1

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
        sessions_generated=sessions_created,
    )
