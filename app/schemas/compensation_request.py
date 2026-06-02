from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CompensationRequestCreate(BaseModel):
    session_id: UUID
    reason: str = Field(..., min_length=1, max_length=1000)


class CompensationRequestOut(BaseModel):
    id: UUID
    status: str

    session_id: UUID
    student_matricule: str

    # Enriched fields for the teacher's UI (CompensationRequests.jsx)
    studentName: Optional[str] = None
    studentGroup: Optional[str] = None
    targetGroup: Optional[str] = None
    room: Optional[str] = None
    date: Optional[date] = None
    time: Optional[str] = None      # "10:00 - 12:00"
    subject: Optional[str] = None

    reason: str
    rejection_reason: Optional[str] = None

    created_at: datetime
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CompensationRequestListResponse(BaseModel):
    data: list[CompensationRequestOut]
    total: int


class CompensationRequestRejectBody(BaseModel):
    reason: Optional[str] = None


class CompensationRequestApproveOut(BaseModel):
    id: UUID
    status: str
    approved_at: datetime

    model_config = {"from_attributes": True}


class CompensationRequestRejectOut(BaseModel):
    id: UUID
    status: str
    rejection_reason: Optional[str] = None
    rejected_at: datetime

    model_config = {"from_attributes": True}
