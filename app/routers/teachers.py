from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin_bearer
from app.models.user import Account
from app.schemas.teachers import TeacherImportReport
from app.services.teachers import import_teachers_csv


router = APIRouter(prefix="/teachers", tags=["Teachers"])


@router.post(
    "/import-csv",
    response_model=TeacherImportReport,
    summary="Import teachers from CSV",
)
async def import_teachers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(require_admin_bearer),
):
    """
    Import teachers from a UTF-8 comma-delimited CSV file.

    Expected columns:
    id_enseignant, nom, prenom, email, grade, departement

    Mapping to ORM fields:
    - id_enseignant -> Teacher.employee_id
    - grade + departement -> Teacher.specialization ("grade | departement")

    Behavior:
    - Upsert key is id_enseignant
    - Invalid rows are skipped and reported
    - Valid rows are imported in the same request (partial import)
    """
    raw = await file.read()
    return await import_teachers_csv(raw, db)
