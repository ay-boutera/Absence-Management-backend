from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Feature 3 — PATCH /students/{id}/status ──────────────────────────────────

class StudentStatusUpdate(BaseModel):
    status: Literal["normal", "exclu", "bloque", "abandonné"]


class AcademicStudentStatusOut(BaseModel):
    id: UUID
    matricule: str
    nom: str
    prenom: str
    filiere: str
    niveau: str
    groupe: str
    email: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
