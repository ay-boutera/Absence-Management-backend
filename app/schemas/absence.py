from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.config.enums import AbsenceSourceEnum, CorrectionStatusEnum


class AbsenceCreate(BaseModel):
    session_id: UUID
    student_matricule: str
    is_absent: bool = True
    source: AbsenceSourceEnum = AbsenceSourceEnum.PWA


class AbsenceOut(BaseModel):
    id: UUID
    session_id: UUID
    student_matricule: str
    is_absent: bool
    source: str
    synced_at: Optional[datetime] = None
    statut_justificatif: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AbsenceUpsertResponse(BaseModel):
    id: UUID
    session_id: UUID
    student_matricule: str
    is_absent: bool
    source: str
    created: bool = Field(..., description="True if newly created, False if updated")

    model_config = {"from_attributes": True}


class CorrectionCreate(BaseModel):
    absence_id: UUID
    new_value: bool
    reason: str


class CorrectionOut(BaseModel):
    id: UUID
    absence_id: UUID
    requested_by: UUID
    reviewed_by: Optional[UUID] = None
    original_value: bool
    new_value: bool
    reason: str
    status: str
    reviewed_at: Optional[datetime] = None
    requested_at: datetime

    model_config = {"from_attributes": True}


class CorrectionReview(BaseModel):
    status: CorrectionStatusEnum
