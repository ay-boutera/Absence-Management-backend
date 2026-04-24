"""
routers/justifications.py — Sprint 4: Gestion des Justificatifs
================================================================

Student endpoints:
  GET  /api/v1/justifications/my-absences               US-27: list absences with justification status
  POST /api/v1/justifications/{absence_id}               US-28: upload justification document
  GET  /api/v1/justifications/my-absences/{absence_id}/deadline   US-29: deadline countdown

Admin endpoints:
  GET   /api/v1/justifications                           US-30/35: queue with filters
  PATCH /api/v1/justifications/{justification_id}/review US-31: approve / reject
  GET   /api/v1/justifications/{justification_id}/file   US-33: download archived document
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.enums import JustificationStatusEnum
from app.db import get_db
from app.helpers.permissions import require_admin, require_student
from app.models import (
    Absence,
    AcademicStudent,
    Admin,
    Justification,
    Session,
)
from app.models.module import Module
from app.schemas.justification import (
    AbsenceJustificationOut,
    JustificationOut,
    JustificationQueueItem,
    JustificationReview,
)
from app.services.email_service import send_justification_status_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Justifications"])

# ── Constants ──────────────────────────────────────────────────────────────────
JUSTIFICATION_DEADLINE_HOURS = 72
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
}

UPLOAD_DIR = Path("uploads/justifications")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _compute_deadline(session: Session) -> datetime:
    session_end = datetime.combine(session.date, session.end_time).replace(tzinfo=timezone.utc)
    return session_end + timedelta(hours=JUSTIFICATION_DEADLINE_HOURS)


def _justification_status(absence: Absence) -> JustificationStatusEnum:
    if not absence.is_absent:
        return JustificationStatusEnum.NON_JUSTIFIEE
    if absence.justification is None:
        return JustificationStatusEnum.NON_JUSTIFIEE
    return absence.justification.status


# ── Student: list absences with justification status (US-27) ──────────────────
@router.get(
    "/justifications/my-absences",
    response_model=list[AbsenceJustificationOut],
    summary="Student: list absences with justification status (US-27)",
)
async def list_my_absences(
    current_user=Depends(require_student),
    db: AsyncSession = Depends(get_db),
):
    academic = (
        await db.execute(
            select(AcademicStudent).where(
                AcademicStudent.matricule == current_user.student_id
            )
        )
    ).scalar_one_or_none()

    if academic is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dossier académique introuvable.",
        )

    absences = list(
        (
            await db.execute(
                select(Absence)
                .where(Absence.student_matricule == academic.matricule)
                .options(selectinload(Absence.justification), selectinload(Absence.session))
            )
        )
        .scalars()
        .all()
    )

    result = []
    now = datetime.now(timezone.utc)
    for absence in absences:
        justi_status = _justification_status(absence)
        deadline = None
        seconds_remaining = None
        is_critical = False

        if absence.is_absent and absence.session:
            deadline = _compute_deadline(absence.session)
            secs = int((deadline - now).total_seconds())
            seconds_remaining = max(secs, 0)
            is_critical = 0 < seconds_remaining < 86_400

        result.append(
            AbsenceJustificationOut(
                absence_id=absence.id,
                session_id=absence.session_id,
                session_date=datetime.combine(absence.session.date, absence.session.start_time).replace(tzinfo=timezone.utc) if absence.session else None,
                is_absent=absence.is_absent,
                statut_justificatif=justi_status,
                deadline=deadline,
                seconds_remaining=seconds_remaining,
                is_deadline_critical=is_critical,
                justification=JustificationOut.model_validate(absence.justification) if absence.justification else None,
            )
        )

    return result


# ── Student: upload justification (US-28) ─────────────────────────────────────
@router.post(
    "/justifications/{absence_id}",
    response_model=JustificationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Student: upload justification document (US-28)",
)
async def submit_justification(
    absence_id: UUID,
    file: UploadFile = File(...),
    current_user=Depends(require_student),
    db: AsyncSession = Depends(get_db),
):
    # Validate file type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Type de fichier non accepté. Utilisez PDF, JPG ou PNG.",
        )

    # Read file and check size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Fichier trop volumineux. Taille maximale : 5 Mo.",
        )

    # Get absence and validate ownership
    absence = (
        await db.execute(
            select(Absence)
            .where(Absence.id == absence_id)
            .options(selectinload(Absence.justification), selectinload(Absence.session))
        )
    ).scalar_one_or_none()

    if absence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Absence introuvable.")

    if absence.student_matricule != current_user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé.")

    if not absence.is_absent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de soumettre un justificatif pour une présence.",
        )

    # Check deadline
    if absence.session is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Séance introuvable.")

    deadline = _compute_deadline(absence.session)
    if datetime.now(timezone.utc) > deadline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le délai de soumission du justificatif est dépassé.",
        )

    # One justification per absence
    if absence.justification is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un justificatif a déjà été soumis pour cette absence.",
        )

    # Check student permission
    if not current_user.can_submit_justifications:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'êtes pas autorisé à soumettre des justificatifs.",
        )

    # Save file permanently
    ext = ALLOWED_CONTENT_TYPES[file.content_type]
    stored_name = f"{uuid.uuid4()}.{ext}"
    file_path = UPLOAD_DIR / stored_name
    file_path.write_bytes(file_bytes)

    justification = Justification(
        absence_id=absence_id,
        student_matricule=current_user.student_id,
        file_path=str(file_path),
        file_name=file.filename or stored_name,
        file_type=ext,
        file_size=len(file_bytes),
        status=JustificationStatusEnum.EN_ATTENTE,
        deadline=deadline,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(justification)

    # Update denormalized field on absence
    absence.statut_justificatif = JustificationStatusEnum.EN_ATTENTE.value
    db.add(absence)

    await db.flush()
    await db.refresh(justification)
    return justification


# ── Student: get deadline countdown for one absence (US-29) ───────────────────
@router.get(
    "/justifications/my-absences/{absence_id}/deadline",
    summary="Student: get deadline countdown (US-29)",
)
async def get_deadline(
    absence_id: UUID,
    current_user=Depends(require_student),
    db: AsyncSession = Depends(get_db),
):
    absence = (
        await db.execute(
            select(Absence)
            .where(Absence.id == absence_id)
            .options(selectinload(Absence.session))
        )
    ).scalar_one_or_none()

    if absence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Absence introuvable.")

    if absence.student_matricule != current_user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé.")

    if not absence.is_absent or absence.session is None:
        return {"deadline": None, "seconds_remaining": None, "is_deadline_critical": False, "is_expired": True}

    deadline = _compute_deadline(absence.session)
    now = datetime.now(timezone.utc)
    secs = int((deadline - now).total_seconds())
    seconds_remaining = max(secs, 0)

    return {
        "deadline": deadline.isoformat(),
        "seconds_remaining": seconds_remaining,
        "is_deadline_critical": 0 < seconds_remaining < 86_400,
        "is_expired": secs <= 0,
    }


# ── Admin: list justification queue with filters (US-30, US-35) ───────────────
@router.get(
    "/justifications",
    response_model=list[JustificationQueueItem],
    summary="Admin: list justifications queue with filters (US-30, US-35)",
)
async def list_justifications(
    statut: Optional[JustificationStatusEnum] = Query(None, description="Filter by status"),
    filiere: Optional[str] = Query(None, description="Filter by filière"),
    module_id: Optional[UUID] = Query(None, description="Filter by module"),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Justification)
        .options(
            selectinload(Justification.absence).selectinload(Absence.session).selectinload(Session.module),
            selectinload(Justification.student),
        )
        .order_by(Justification.submitted_at.asc())
    )

    if statut is not None:
        stmt = stmt.where(Justification.status == statut)

    justifications = list((await db.execute(stmt)).scalars().all())

    result = []
    for j in justifications:
        absence = j.absence
        session = absence.session if absence else None
        module = session.module if session else None
        academic_student = j.student

        # Filter by filiere
        if filiere and (academic_student is None or academic_student.filiere != filiere):
            continue

        # Filter by module
        if module_id and (module is None or module.id != module_id):
            continue

        session_date = None
        if session:
            session_date = datetime.combine(session.date, session.start_time).replace(tzinfo=timezone.utc)

        result.append(
            JustificationQueueItem(
                id=j.id,
                absence_id=j.absence_id,
                student_matricule=j.student_matricule,
                student_name=f"{academic_student.prenom} {academic_student.nom}" if academic_student else None,
                filiere=academic_student.filiere if academic_student else None,
                module_name=module.nom if module else None,
                session_date=session_date,
                file_name=j.file_name,
                file_type=j.file_type,
                file_size=j.file_size,
                status=j.status,
                admin_comment=j.admin_comment,
                deadline=j.deadline,
                submitted_at=j.submitted_at,
                reviewed_at=j.reviewed_at,
            )
        )

    return result


# ── Admin: approve / reject justification (US-31) ─────────────────────────────
@router.patch(
    "/justifications/{justification_id}/review",
    response_model=JustificationOut,
    summary="Admin: approve or reject a justification (US-31)",
)
async def review_justification(
    justification_id: UUID,
    data: JustificationReview,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    justification = (
        await db.execute(
            select(Justification)
            .where(Justification.id == justification_id)
            .options(
                selectinload(Justification.absence).selectinload(Absence.session).selectinload(Session.module),
                selectinload(Justification.student),
            )
        )
    ).scalar_one_or_none()

    if justification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justificatif introuvable.")

    if justification.status != JustificationStatusEnum.EN_ATTENTE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ce justificatif a déjà été traité ({justification.status.value}).",
        )

    if data.status == JustificationStatusEnum.REJETEE and not data.admin_comment.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Un commentaire est obligatoire en cas de rejet.",
        )

    justification.status = data.status
    justification.admin_comment = data.admin_comment
    justification.reviewed_by = current_user.id
    justification.reviewed_at = datetime.now(timezone.utc)
    db.add(justification)

    # Sync denormalized field on absence
    absence = justification.absence
    if absence:
        absence.statut_justificatif = data.status.value
        db.add(absence)

    await db.flush()
    await db.refresh(justification)

    # Send email notification (US-32) — fire and forget
    academic_student = justification.student
    if academic_student and academic_student.email:
        session = absence.session if absence else None
        module = session.module if session else None
        session_info = f"{module.nom if module else 'séance'} du {session.date if session else '?'}"
        try:
            await send_justification_status_email(
                email=academic_student.email,
                full_name=f"{academic_student.prenom} {academic_student.nom}",
                status=data.status.value,
                admin_comment=data.admin_comment,
                session_info=session_info,
            )
        except Exception as exc:
            logger.warning("Could not send justification email: %s", exc)

    return justification


# ── Download archived justification file (US-33) ──────────────────────────────
@router.get(
    "/justifications/{justification_id}/file",
    summary="Download archived justification document (US-33)",
)
async def download_justification_file(
    justification_id: UUID,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    justification = (
        await db.execute(
            select(Justification).where(Justification.id == justification_id)
        )
    ).scalar_one_or_none()

    if justification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justificatif introuvable.")

    file_path = Path(justification.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier introuvable sur le serveur.",
        )

    media_type_map = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}
    media_type = media_type_map.get(justification.file_type, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=justification.file_name,
    )
