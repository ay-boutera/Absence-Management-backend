from pydantic import BaseModel, Field
from uuid import UUID


class ImportErrorItem(BaseModel):
    line: int = Field(..., example=5)
    field: str = Field(..., example="email")
    reason: str = Field(..., example="Invalid email format")
    row_data: dict[str, str] = Field(default_factory=dict)


class ImportResponse(BaseModel):
    imported: int = Field(..., example=42)
    errors: int = Field(..., example=3)
    error_report: list[ImportErrorItem] = Field(default_factory=list)
    history_id: UUID
