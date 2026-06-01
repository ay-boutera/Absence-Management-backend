"""
services/notification_service.py — Real-time notification delivery
===================================================================

ConnectionManager
  Keeps an in-memory registry of active WebSocket connections keyed by the
  user's UUID string.  Suitable for single-process deployments (Uvicorn).

Helpers
  create_and_push  — persist a Notification row + push to any live connection
  notify_admins    — broadcast a notification to every active admin
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin
from app.models.notification import Notification

logger = logging.getLogger(__name__)


# ── WebSocket connection manager ───────────────────────────────────────────────

class ConnectionManager:
    """Thread-safe enough for asyncio; not safe across multiple OS processes."""

    def __init__(self) -> None:
        # user_id (str) → list of open WebSockets for that user
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, []).append(websocket)
        logger.debug("WS connected  user=%s  slots=%d", user_id, len(self._connections[user_id]))

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        slots = self._connections.get(user_id, [])
        if websocket in slots:
            slots.remove(websocket)
        if not slots:
            self._connections.pop(user_id, None)
        logger.debug("WS disconnected  user=%s", user_id)

    async def push(self, user_id: str, payload: dict) -> None:
        """Deliver payload to all open connections for user_id; silently drop if offline."""
        for ws in list(self._connections.get(user_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                # Stale connection — will be cleaned up on WebSocketDisconnect
                logger.debug("WS push failed for user=%s (stale connection)", user_id)


# Module-level singleton used by all routers
manager = ConnectionManager()


# ── Notification helpers ───────────────────────────────────────────────────────

async def create_and_push(
    db: AsyncSession,
    *,
    recipient_id: UUID,
    recipient_role: str,
    type: str,
    title: str,
    body: str,
    justification_id: UUID | None = None,
    module_name: str | None = None,
) -> Notification:
    """
    Persist a Notification row and push it immediately to any live WebSocket
    connection for that recipient.  Always returns the saved Notification.
    """
    notif = Notification(
        recipient_id=recipient_id,
        recipient_role=recipient_role,
        type=type,
        title=title,
        body=body,
        justification_id=justification_id,
        module_name=module_name,
    )
    db.add(notif)
    await db.flush()
    await db.refresh(notif)

    # Push to any live WS connection — fire-and-forget
    await manager.push(
        str(recipient_id),
        {
            "id": str(notif.id),
            "type": type,
            "title": title,
            "body": body,
            "justification_id": str(justification_id) if justification_id else None,
            "module_name": module_name,
            "created_at": notif.created_at.isoformat(),
            "is_read": False,
        },
    )
    return notif


async def notify_admins(
    db: AsyncSession,
    *,
    type: str,
    title: str,
    body: str,
    justification_id: UUID | None = None,
) -> None:
    """Create a notification for every active admin and push to live connections."""
    admin_ids = (
        await db.execute(select(Admin.id).where(Admin.is_active == True))
    ).scalars().all()

    for admin_id in admin_ids:
        await create_and_push(
            db,
            recipient_id=admin_id,
            recipient_role="admin",
            type=type,
            title=title,
            body=body,
            justification_id=justification_id,
        )
