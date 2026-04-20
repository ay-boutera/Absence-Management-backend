import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_or_teacher_bearer, require_can_export_data_bearer
from app.helpers.role_users import user_role
from app.models import (
    Absence,
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
    ImportExportLog,
    PlanningSession,
    UserRole,
)
from app.models.student import AcademicStudent

router = APIRouter(tags=["Exports"])


@router.get(
    "/export/absences",
    summary="Export absences as CSV",
)
async def export_absences_csv(
    matricule_etudiant: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user=Depends(require_can_export_data_bearer),
    db: AsyncSession = Depends(get_db),
):
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

    if user_role(current_user) == UserRole.TEACHER:
        base_query = base_query.join(
            PlanningSession, PlanningSession.id == Absence.planning_session_id
        ).where(PlanningSession.teacher_id == current_user.id)

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
        writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5] or ""])

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
    current_user=Depends(require_admin_or_teacher_bearer),
    db: AsyncSession = Depends(get_db),
):
    query = select(ImportExportLog).order_by(ImportExportLog.created_at.desc())

    if user_role(current_user) == UserRole.TEACHER:
        query = query.where(ImportExportLog.performed_by_id == current_user.id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
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
