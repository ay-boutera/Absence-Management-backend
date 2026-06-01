"""
routers/notifications.py — Real-time notification channel and inbox
====================================================================

WS   /api/v1/ws/notifications?token=<access_jwt>
     Connect to receive notifications in real time.
     The server sends JSON objects that match NotificationOut.

GET  /api/v1/notifications?unread_only=false&page=1&page_size=20
     Paginated inbox — newest first.

PATCH /api/v1/notifications/{id}/read
     Mark one notification as read.

POST /api/v1/notifications/read-all
     Mark every unread notification as read.

GET  /api/v1/notifications/unread-count
     Returns {"count": N} — lightweight poll for badge updates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import get_current_user, require_active_user
from app.helpers.role_users import user_role
from app.config.enums import UserRole
from app.helpers.security import decode_token
from app.models.notification import Notification
from app.models.admin import Admin
from app.models.student import Student
from app.db import engine
from app.services.notification_service import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Notifications"])


# ── Pydantic schema ────────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: UUID
    type: str
    title: str
    body: str
    justification_id: Optional[UUID] = None
    module_name: Optional[str] = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    data: list[NotificationOut]


class UnreadCountResponse(BaseModel):
    count: int


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws/notifications")
async def notifications_ws(
    websocket: WebSocket,
    token: str = Query(..., description="Access JWT token"),
):
    """
    Real-time notification channel.

    Connect with:
        ws://<host>/api/v1/ws/notifications?token=<access_jwt>

    The server pushes JSON whenever a notification is created for this user:
    ```json
    {
      "id": "<uuid>",
      "type": "justification_approved",
      "title": "Justification approuvée",
      "body": "Votre justification a été approuvée.",
      "justification_id": "<uuid>",
      "module_name": null,
      "created_at": "2026-06-01T12:00:00+00:00",
      "is_read": false
    }
    ```
    Send any message back (e.g. `"ping"`) to keep the connection alive.
    """
    # --- Authenticate from query-string token ---
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=4001, reason="Invalid token type")
            return
        user_id_str: str = payload.get("sub", "")
        role_str: str = payload.get("role", "")
        if not user_id_str:
            await websocket.close(code=4001, reason="Missing subject")
            return
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # Verify user actually exists (use a fresh async session)
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    async with _AS(engine, expire_on_commit=False) as db:
        if role_str == UserRole.ADMIN:
            user = (await db.execute(
                select(Admin).where(Admin.id == UUID(user_id_str), Admin.is_active == True)
            )).scalar_one_or_none()
        else:
            user = (await db.execute(
                select(Student).where(Student.id == UUID(user_id_str), Student.is_active == True)
            )).scalar_one_or_none()

    if user is None:
        await websocket.close(code=4001, reason="User not found or inactive")
        return

    await manager.connect(user_id_str, websocket)
    try:
        while True:
            # Just drain incoming messages to keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id_str, websocket)


# ── REST inbox ─────────────────────────────────────────────────────────────────

@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    summary="Get my notifications",
    description="""
Returns the authenticated user's notification inbox, newest first.

**Query params:**
- `unread_only` (bool, default `false`) — return only unread notifications
- `page` / `page_size` — pagination

**Auth:** Any authenticated user (admin or student).
""",
)
async def get_notifications(
    unread_only: bool = Query(False, description="Return only unread notifications"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    role = user_role(current_user)
    filters = [Notification.recipient_id == current_user.id]
    if unread_only:
        filters.append(Notification.is_read == False)

    total: int = (
        await db.execute(select(func.count()).select_from(Notification).where(and_(*filters)))
    ).scalar() or 0

    rows = (
        await db.execute(
            select(Notification)
            .where(and_(*filters))
            .order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return NotificationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        data=[NotificationOut.model_validate(n) for n in rows],
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count",
    description="Lightweight endpoint for badge counters. Returns `{count: N}`.",
)
async def get_unread_count(
    current_user=Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> UnreadCountResponse:
    count: int = (
        await db.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_id == current_user.id,
                Notification.is_read == False,
            )
        )
    ).scalar() or 0
    return UnreadCountResponse(count=count)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark a notification as read",
)
async def mark_as_read(
    notification_id: UUID,
    current_user=Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationOut:
    notif = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_id == current_user.id,
            )
        )
    ).scalar_one_or_none()

    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    notif.is_read = True
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    return NotificationOut.model_validate(notif)


@router.post(
    "/notifications/read-all",
    summary="Mark all notifications as read",
    description="Marks every unread notification for the current user as read.",
)
async def mark_all_read(
    current_user=Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        update(Notification)
        .where(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"marked_read": result.rowcount or 0}
