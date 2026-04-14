import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SQLAlchemyEnum, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base
from app.config.enums import ImportType


class ImportHistory(Base):
    __tablename__ = "import_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    import_type = Column(SQLAlchemyEnum(ImportType), nullable=False)
    total_rows = Column(Integer, nullable=False)
    success_count = Column(Integer, nullable=False)
    error_count = Column(Integer, nullable=False)
