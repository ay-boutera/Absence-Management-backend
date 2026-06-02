import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base

NONCE_TTL_SECONDS = 30
_NONCE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_nonce(length: int = 6) -> str:
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(length))


def _nonce_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=NONCE_TTL_SECONDS)


class SessionNonce(Base):
    __tablename__ = "session_nonces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    nonce = Column(String(10), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, default=_nonce_expiry)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("Session")
