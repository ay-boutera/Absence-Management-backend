from pydantic import BaseModel, Field


class ImportError(BaseModel):
    line: int = Field(..., example=2)
    column: str = Field(..., example="email")
    reason: str = Field(..., example="Invalid email format")


class TeacherImportReport(BaseModel):
    created: int = Field(..., example=10)
    updated: int = Field(..., example=3)
    errors: list[ImportError] = Field(default_factory=list)
