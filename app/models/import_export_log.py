import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SQLAlchemyEnum, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base
from app.config.enums import (
    ImportExportAction,
    ImportExportDataType,
    ImportExportFileType,
)


class ImportExportLog(Base):
    __tablename__ = "import_export_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    performed_by_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    action = Column(SQLAlchemyEnum(ImportExportAction), nullable=False)
    file_type = Column(SQLAlchemyEnum(ImportExportFileType), nullable=False)
    file_name = Column(String(255), nullable=False)
    data_type = Column(SQLAlchemyEnum(ImportExportDataType), nullable=False)
    row_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    error_details = Column(JSON, default=dict, nullable=False)
    file_path = Column(String(500), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
