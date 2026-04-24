from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.config.enums import JustificationStatusEnum


class JustificationOut(BaseModel):
    id: UUID
    absence_id: UUID
    student_matricule: str
    file_name: str
    file_type: str
    file_size: int
    status: JustificationStatusEnum
    admin_comment: Optional[str] = None
    deadline: datetime
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[UUID] = None

    @computed_field
    @property
    def seconds_remaining(self) -> Optional[int]:
        now = datetime.now(timezone.utc)
        deadline = self.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        remaining = int((deadline - now).total_seconds())
        return max(remaining, 0)

    @computed_field
    @property
    def is_deadline_critical(self) -> bool:
        return 0 < (self.seconds_remaining or 0) < 86_400  # < 24 h

    model_config = {"from_attributes": True}


class JustificationReview(BaseModel):
    status: Literal[JustificationStatusEnum.JUSTIFIEE, JustificationStatusEnum.REJETEE]
    admin_comment: str  # mandatory — informs the student


class AbsenceJustificationOut(BaseModel):
    """Absence enriched with justification status for the student dashboard (US-27)."""

    absence_id: UUID
    session_id: UUID
    session_date: Optional[datetime] = None
    is_absent: bool
    statut_justificatif: JustificationStatusEnum
    deadline: Optional[datetime] = None
    seconds_remaining: Optional[int] = None
    is_deadline_critical: bool = False
    justification: Optional[JustificationOut] = None

    model_config = {"from_attributes": True}


class JustificationQueueItem(BaseModel):
    """Item in the admin justification queue (US-30, US-35)."""

    id: UUID
    absence_id: UUID
    student_matricule: str
    student_name: Optional[str] = None
    filiere: Optional[str] = None
    module_name: Optional[str] = None
    session_date: Optional[datetime] = None
    file_name: str
    file_type: str
    file_size: int
    status: JustificationStatusEnum
    admin_comment: Optional[str] = None
    deadline: datetime
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
