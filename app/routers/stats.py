"""
routers/stats.py — Sprint 6: Tableaux de Bord & Statistiques
=============================================================

Admin:
  GET  /api/v1/stats/admin/dashboard    US-45/50: global dashboard + filters
  GET  /api/v1/stats/admin/motifs       US-46: absence motive breakdown
  GET  /api/v1/stats/admin/compare      US-51: period comparison
  GET  /api/v1/stats/admin/realtime     US-53: quasi-realtime polling

Teacher:
  GET  /api/v1/stats/teacher/dashboard  US-47: module stats + semester trend
  GET  /api/v1/stats/teacher/alerts     US-48: threshold breach alerts

Student:
  GET  /api/v1/stats/student/me         US-52: self-stats + at-risk modules

Shared:
  GET  /api/v1/stats/export/pdf         US-49: ESI-SBA branded PDF export
  POST /api/v1/stats/reports/weekly     US-54: send weekly report by email
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.helpers.permissions import require_admin, require_student, require_teacher
from app.helpers.role_users import user_role
from app.models import UserRole
from app.schemas.stats import (
    AdminDashboardResponse,
    MotifBreakdownResponse,
    PeriodComparisonResponse,
    RealtimeStatsResponse,
    StudentStatsResponse,
    TeacherDashboardResponse,
    ThresholdAlertsResponse,
    WeeklyReportRequest,
)
from app.services import stats_service
from app.services.email_service import send_weekly_report_email
from app.services.pdf_service import (
    generate_admin_dashboard_pdf,
    generate_motif_breakdown_pdf,
    generate_student_stats_pdf,
    generate_teacher_dashboard_pdf,
)

router = APIRouter(prefix="/stats", tags=["Statistics"])


# ── US-45 / US-50: Admin global dashboard ─────────────────────────────────────
@router.get(
    "/admin/dashboard",
    response_model=AdminDashboardResponse,
    summary="Admin: global absence dashboard with filters (US-45, US-50)",
)
async def admin_dashboard(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    filiere: Optional[str] = Query(None),
    module_id: Optional[UUID] = Query(None),
    session_type: Optional[str] = Query(None, description="SessionType enum value"),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_admin_dashboard(
        db, date_from, date_to, filiere, module_id, session_type
    )


# ── US-46: Motif breakdown ────────────────────────────────────────────────────
@router.get(
    "/admin/motifs",
    response_model=MotifBreakdownResponse,
    summary="Admin: absence motive distribution (US-46)",
)
async def admin_motifs(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    filiere: Optional[str] = Query(None),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_motif_breakdown(db, date_from, date_to, filiere)


# ── US-51: Period comparison ───────────────────────────────────────────────────
@router.get(
    "/admin/compare",
    response_model=PeriodComparisonResponse,
    summary="Admin: compare absence rates between two periods (US-51)",
)
async def admin_compare_periods(
    p1_from: date = Query(..., description="Period 1 start date"),
    p1_to: date = Query(..., description="Period 1 end date"),
    p2_from: date = Query(..., description="Period 2 start date"),
    p2_to: date = Query(..., description="Period 2 end date"),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if p1_from > p1_to or p2_from > p2_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La date de début doit être antérieure à la date de fin.",
        )
    return await stats_service.compare_periods(db, p1_from, p1_to, p2_from, p2_to)


# ── US-53: Quasi-realtime polling ─────────────────────────────────────────────
@router.get(
    "/admin/realtime",
    response_model=RealtimeStatsResponse,
    summary="Admin: real-time absence counters for today (US-53)",
)
async def admin_realtime(
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_realtime_stats(db)


# ── US-47: Teacher dashboard ──────────────────────────────────────────────────
@router.get(
    "/teacher/dashboard",
    response_model=TeacherDashboardResponse,
    summary="Teacher: module stats + semester trend (US-47)",
)
async def teacher_dashboard(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user=Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_teacher_dashboard(
        db, current_user.id, date_from, date_to
    )


# ── US-48: Threshold alerts ────────────────────────────────────────────────────
@router.get(
    "/teacher/alerts",
    response_model=ThresholdAlertsResponse,
    summary="Teacher: students exceeding absence threshold (US-48)",
)
async def teacher_alerts(
    threshold_pct: float = Query(
        default=20.0,
        ge=1.0,
        le=100.0,
        description="Alert threshold in percent (default 20%)",
    ),
    current_user=Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_threshold_alerts(db, current_user.id, threshold_pct)


# ── US-52: Student self-stats ─────────────────────────────────────────────────
@router.get(
    "/student/me",
    response_model=StudentStatsResponse,
    summary="Student: personal absence stats and at-risk modules (US-52)",
)
async def student_stats(
    current_user=Depends(require_student),
    db: AsyncSession = Depends(get_db),
):
    return await stats_service.get_student_stats(db, current_user.student_id)


# ── US-49: PDF export ─────────────────────────────────────────────────────────
@router.get(
    "/export/pdf",
    summary="Export dashboard view as ESI-SBA branded PDF (US-49)",
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export_pdf(
    view: str = Query(
        ...,
        description="View to export: admin_dashboard | teacher_dashboard | student_me | motifs",
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    filiere: Optional[str] = Query(None),
    module_id: Optional[UUID] = Query(None),
    session_type: Optional[str] = Query(None),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    role = user_role(current_user)

    if view == "admin_dashboard":
        data = await stats_service.get_admin_dashboard(
            db, date_from, date_to, filiere, module_id, session_type
        )
        pdf_bytes = generate_admin_dashboard_pdf(data.model_dump())

    elif view == "teacher_dashboard":
        data = await stats_service.get_teacher_dashboard(
            db, current_user.id, date_from, date_to
        )
        pdf_bytes = generate_teacher_dashboard_pdf(data.model_dump())

    elif view == "student_me":
        data = await stats_service.get_student_stats(db, current_user.student_id if hasattr(current_user, "student_id") else "")
        pdf_bytes = generate_student_stats_pdf(data.model_dump())

    elif view == "motifs":
        data = await stats_service.get_motif_breakdown(db, date_from, date_to, filiere)
        pdf_bytes = generate_motif_breakdown_pdf(data.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Vue inconnue : '{view}'. Valeurs acceptées : admin_dashboard, teacher_dashboard, student_me, motifs.",
        )

    filename = f"esi_sba_{view}_{date.today().isoformat()}.pdf"
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── US-54: Weekly report by email ─────────────────────────────────────────────
@router.post(
    "/reports/weekly",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Admin: send weekly absence report by email to supervisors (US-54)",
)
async def send_weekly_report(
    body: WeeklyReportRequest,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    # Last Monday → last Sunday
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    week_label = f"Semaine du {monday.strftime('%d/%m/%Y')} au {sunday.strftime('%d/%m/%Y')}"

    stats = await stats_service.get_admin_dashboard(db, monday, sunday, None, None, None)
    sent = await send_weekly_report_email(body.recipients, stats.model_dump(), week_label)

    if not sent:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Échec de l'envoi du rapport. Vérifiez la configuration SMTP.",
        )

    return {
        "message": f"Rapport envoyé à {len(body.recipients)} destinataire(s).",
        "week": week_label,
        "recipients": body.recipients,
    }
