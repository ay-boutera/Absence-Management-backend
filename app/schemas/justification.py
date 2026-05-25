from __future__ import annotations

from datetime import date, datetime
from math import ceil
from typing import Literal, Optional
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.enums import JustificationScopeType, JustificationStatus


class JustificationSubmitRequest(BaseModel):
    scope_type: JustificationScopeType
    absence_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: str

    @model_validator(mode="after")
    def validate_scope(self) -> "JustificationSubmitRequest":
        if len(self.reason) > 500:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reason must not exceed 500 characters")

        if self.scope_type == JustificationScopeType.ABSENCE:
            if self.absence_id is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='absence' requires 'absence_id'")
            if any(v is not None for v in [self.session_id, self.start_date, self.end_date]):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='absence' only allows 'absence_id'")

        elif self.scope_type == JustificationScopeType.SESSION:
            if self.session_id is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='session' requires 'session_id'")
            if any(v is not None for v in [self.absence_id, self.start_date, self.end_date]):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='session' only allows 'session_id'")

        elif self.scope_type == JustificationScopeType.RANGE:
            if self.start_date is None or self.end_date is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='range' requires 'start_date' and 'end_date'")
            if self.end_date <= self.start_date:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date must be after start_date")
            if any(v is not None for v in [self.absence_id, self.session_id]):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type='range' only allows 'start_date' and 'end_date'")

        return self


class JustificationEditRequest(BaseModel):
    reason: Optional[str] = None


class RejectRequest(BaseModel):
    reason: Optional[str] = None


class BulkApproveRequest(BaseModel):
    ids: Optional[list[UUID]] = None


class BulkRejectRequest(BaseModel):
    ids: Optional[list[UUID]] = None
    reason: Optional[str] = None


class JustificationStudentInfo(BaseModel):
    id: UUID
    full_name: str
    email: str


class JustificationDocumentInfo(BaseModel):
    name: str
    url: str


class JustificationResponse(BaseModel):
    id: UUID
    student: JustificationStudentInfo
    scope_type: JustificationScopeType
    absence_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: str
    document: JustificationDocumentInfo
    status: JustificationStatus
    rejection_reason: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JustificationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    data: list[JustificationResponse]

    @staticmethod
    def from_items(*, total: int, page: int, page_size: int, data: list[JustificationResponse]) -> "JustificationListResponse":
        total_pages = ceil(total / page_size) if page_size > 0 else 0
        return JustificationListResponse(total=total, page=page, page_size=page_size, total_pages=total_pages, data=data)


class ApproveResponse(BaseModel):
    id: UUID
    status: Literal["approved"] = "approved"
    reviewed_at: datetime
    reviewed_by: str


class RejectResponse(BaseModel):
    id: UUID
    status: Literal["rejected"] = "rejected"
    rejection_reason: Optional[str] = None
    reviewed_at: datetime
    reviewed_by: str


class BulkReviewResponse(BaseModel):
    affected: int
    status: Literal["approved", "rejected"]
    rejection_reason: Optional[str] = None
    reviewed_at: datetime
    reviewed_by: str


class JustificationDocumentResponse(BaseModel):
    document_name: str
    url: str
