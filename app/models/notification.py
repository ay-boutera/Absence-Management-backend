"""
models/notification.py — Persisted in-app notifications
=========================================================
Notifications are always saved to this table so they can be retrieved even
when the user is offline.  Real-time delivery is handled separately by the
WebSocket connection manager in app/services/notification_service.py.

Notification types
------------------
  justification_submitted  → sent to every active admin when a student submits
  justification_approved   → sent to the student when admin approves
  justification_rejected   → sent to the student when admin rejects
  absence_warning          → sent to student when they hit the warning threshold
                             (e.g. 3 absences in the same module)
  absence_exclusion        → sent to student when they hit the exclusion threshold
                             (e.g. 5 absences in the same module)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Recipient ──────────────────────────────────────────────────────────────
    # UUID of the recipient user (admins.id  or  student_users.id)
    # No FK here because admins and students live in different tables.
    recipient_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    recipient_role = Column(String(20), nullable=False)   # "admin" | "student"

    # ── Payload ────────────────────────────────────────────────────────────────
    type = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)

    # Optional context references (nullable — not all types need them)
    justification_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    module_name = Column(String(255), nullable=True)   # for threshold notifications

    # ── State ──────────────────────────────────────────────────────────────────
    is_read = Column(Boolean, default=False, nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Notification type={self.type!r} "
            f"recipient={self.recipient_id} read={self.is_read}>"
        )
