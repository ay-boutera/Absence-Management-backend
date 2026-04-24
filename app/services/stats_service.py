"""
services/stats_service.py — All statistics computation logic for Sprint 6.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.enums import AbsenceMotifEnum, SessionStatusEnum, SessionType
from app.models import (
    Absence,
    AcademicStudent,
    Session,
)
from app.models.module import Module
from app.models.session import session_students
from app.models.teacher import Teacher
from app.schemas.stats import (
    AdminDashboardResponse,
    ByFiliereStat,
    ByModuleStat,
    BySessionTypeStat,
    MotifBreakdownResponse,
    MotifStat,
    PeriodComparisonResponse,
    PeriodStats,
    RealtimeStatsResponse,
    StudentModuleStat,
    StudentStatsResponse,
    TeacherDashboardResponse,
    TeacherModuleStat,
    ThresholdAlert,
    ThresholdAlertsResponse,
    WeeklyTrendPoint,
)

_MOTIF_LABELS = {
    AbsenceMotifEnum.MEDICAL: "Médical",
    AbsenceMotifEnum.ADMINISTRATIF: "Administratif",
    AbsenceMotifEnum.FAMILIAL: "Familial",
    AbsenceMotifEnum.SPORTIF_CULTUREL: "Sportif / Culturel",
    AbsenceMotifEnum.AUTRE: "Autre",
}

AT_RISK_THRESHOLD_PCT = 20.0  # default alert threshold


def _rate(absences: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(absences / total * 100, 2)


def _absence_filters(
    date_from: Optional[date],
    date_to: Optional[date],
    filiere: Optional[str],
    module_id: Optional[UUID],
    session_type: Optional[str],
) -> list:
    filters = []
    if date_from:
        filters.append(Session.date >= date_from)
    if date_to:
        filters.append(Session.date <= date_to)
    if filiere:
        filters.append(AcademicStudent.filiere == filiere)
    if module_id:
        filters.append(Session.module_id == module_id)
    if session_type:
        filters.append(Session.type == session_type)
    return filters


# ── Shared sub-query helpers ──────────────────────────────────────────────────

async def _stats_by_filiere(
    db: AsyncSession, extra_filters: list
) -> list[ByFiliereStat]:
    stmt = (
        select(
            AcademicStudent.filiere,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .join(Session, Session.id == Absence.session_id)
        .where(and_(*extra_filters) if extra_filters else True)
        .group_by(AcademicStudent.filiere)
        .order_by(AcademicStudent.filiere)
    )
    rows = (await db.execute(stmt)).all()
    return [
        ByFiliereStat(
            filiere=r.filiere or "—",
            total_records=r.total,
            absences=r.absences or 0,
            rate=_rate(r.absences or 0, r.total),
        )
        for r in rows
    ]


async def _stats_by_module(
    db: AsyncSession, extra_filters: list
) -> list[ByModuleStat]:
    stmt = (
        select(
            Module.id,
            Module.code,
            Module.nom,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*extra_filters) if extra_filters else True)
        .group_by(Module.id, Module.code, Module.nom)
        .order_by(func.sum(case((Absence.is_absent == True, 1), else_=0)).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        ByModuleStat(
            module_id=r.id,
            module_code=r.code,
            module_nom=r.nom,
            total_records=r.total,
            absences=r.absences or 0,
            rate=_rate(r.absences or 0, r.total),
        )
        for r in rows
    ]


async def _stats_by_session_type(
    db: AsyncSession, extra_filters: list
) -> list[BySessionTypeStat]:
    stmt = (
        select(
            Session.type,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*extra_filters) if extra_filters else True)
        .group_by(Session.type)
    )
    rows = (await db.execute(stmt)).all()
    return [
        BySessionTypeStat(
            session_type=str(r.type.value) if r.type else "—",
            total_records=r.total,
            absences=r.absences or 0,
            rate=_rate(r.absences or 0, r.total),
        )
        for r in rows
    ]


async def _weekly_trend(
    db: AsyncSession, extra_filters: list
) -> list[WeeklyTrendPoint]:
    yr = func.extract("year", Session.date).label("yr")
    wk = func.extract("week", Session.date).label("wk")
    stmt = (
        select(
            yr,
            wk,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*extra_filters) if extra_filters else True)
        .group_by("yr", "wk")
        .order_by("yr", "wk")
    )
    rows = (await db.execute(stmt)).all()
    return [
        WeeklyTrendPoint(
            year=int(r.yr),
            week=int(r.wk),
            absences=r.absences or 0,
            total_records=r.total,
            rate=_rate(r.absences or 0, r.total),
        )
        for r in rows
    ]


# ── US-45 / US-50: Admin global dashboard ─────────────────────────────────────

async def get_admin_dashboard(
    db: AsyncSession,
    date_from: Optional[date],
    date_to: Optional[date],
    filiere: Optional[str],
    module_id: Optional[UUID],
    session_type: Optional[str],
) -> AdminDashboardResponse:
    filters = _absence_filters(date_from, date_to, filiere, module_id, session_type)

    totals_stmt = (
        select(
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
            func.count(distinct(Absence.session_id)).label("sessions"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*filters) if filters else True)
    )
    totals = (await db.execute(totals_stmt)).one()

    total_records = totals.total or 0
    total_absences = totals.absences or 0
    total_sessions = totals.sessions or 0

    return AdminDashboardResponse(
        date_from=date_from,
        date_to=date_to,
        filiere_filter=filiere,
        module_filter=module_id,
        session_type_filter=session_type,
        total_sessions=total_sessions,
        total_records=total_records,
        total_absences=total_absences,
        overall_rate=_rate(total_absences, total_records),
        by_filiere=await _stats_by_filiere(db, filters),
        by_module=await _stats_by_module(db, filters),
        by_session_type=await _stats_by_session_type(db, filters),
        weekly_trend=await _weekly_trend(db, filters),
    )


# ── US-46: Motif breakdown ────────────────────────────────────────────────────

async def get_motif_breakdown(
    db: AsyncSession,
    date_from: Optional[date],
    date_to: Optional[date],
    filiere: Optional[str],
) -> MotifBreakdownResponse:
    filters: list = [Absence.is_absent == True, Absence.motif.isnot(None)]
    if date_from:
        filters.append(Session.date >= date_from)
    if date_to:
        filters.append(Session.date <= date_to)

    stmt = (
        select(
            Absence.motif,
            func.count(Absence.id).label("cnt"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*filters))
        .group_by(Absence.motif)
        .order_by(func.count(Absence.id).desc())
    )
    if filiere:
        stmt = stmt.where(AcademicStudent.filiere == filiere)

    rows = (await db.execute(stmt)).all()
    total = sum(r.cnt for r in rows)

    breakdown = [
        MotifStat(
            motif=r.motif.value if r.motif else "autre",
            label=_MOTIF_LABELS.get(r.motif, str(r.motif)),
            count=r.cnt,
            percentage=_rate(r.cnt, total),
        )
        for r in rows
    ]
    return MotifBreakdownResponse(total_absences_with_motif=total, breakdown=breakdown)


# ── US-47: Teacher dashboard ──────────────────────────────────────────────────

async def get_teacher_dashboard(
    db: AsyncSession,
    teacher_id: UUID,
    date_from: Optional[date],
    date_to: Optional[date],
) -> TeacherDashboardResponse:
    filters: list = [Session.teacher_id == teacher_id]
    if date_from:
        filters.append(Session.date >= date_from)
    if date_to:
        filters.append(Session.date <= date_to)

    stmt = (
        select(
            Module.id,
            Module.code,
            Module.nom,
            func.count(distinct(Session.id)).label("sessions"),
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
            func.sum(
                case((Absence.statut_justificatif == "justifiee", 1), else_=0)
            ).label("justified"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .where(and_(*filters))
        .group_by(Module.id, Module.code, Module.nom)
        .order_by(Module.code)
    )
    rows = (await db.execute(stmt)).all()

    by_module = []
    total_justified = 0
    total_absences_all = 0

    for r in rows:
        absences = r.absences or 0
        justified = r.justified or 0
        unjustified = absences - justified
        total_justified += justified
        total_absences_all += absences
        by_module.append(
            TeacherModuleStat(
                module_id=r.id,
                module_code=r.code,
                module_nom=r.nom,
                total_sessions=r.sessions,
                total_records=r.total,
                total_absences=absences,
                absence_rate=_rate(absences, r.total),
                justified_count=justified,
                unjustified_count=unjustified,
                justified_ratio=_rate(justified, absences),
            )
        )

    trend = await _weekly_trend(db, filters)
    overall_justified_ratio = _rate(total_justified, total_absences_all)

    return TeacherDashboardResponse(
        teacher_id=teacher_id,
        date_from=date_from,
        date_to=date_to,
        by_module=by_module,
        weekly_trend=trend,
        overall_justified_ratio=overall_justified_ratio,
    )


# ── US-48: Threshold alerts ────────────────────────────────────────────────────

async def get_threshold_alerts(
    db: AsyncSession,
    teacher_id: UUID,
    threshold_pct: float,
) -> ThresholdAlertsResponse:
    stmt = (
        select(
            AcademicStudent.matricule,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            Module.id,
            Module.code,
            Module.nom,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(Session.teacher_id == teacher_id)
        .group_by(
            AcademicStudent.matricule,
            AcademicStudent.nom,
            AcademicStudent.prenom,
            Module.id,
            Module.code,
            Module.nom,
        )
        .having(
            func.sum(case((Absence.is_absent == True, 1), else_=0)) * 100.0
            / func.count(Absence.id)
            >= threshold_pct
        )
        .order_by(
            (func.sum(case((Absence.is_absent == True, 1), else_=0)) * 100.0
            / func.count(Absence.id)).desc()
        )
    )
    rows = (await db.execute(stmt)).all()

    alerts = [
        ThresholdAlert(
            student_matricule=r.matricule,
            student_name=f"{r.prenom} {r.nom}",
            module_id=r.id,
            module_code=r.code,
            module_nom=r.nom,
            absence_count=r.absences or 0,
            total_sessions=r.total,
            absence_rate=_rate(r.absences or 0, r.total),
            threshold_pct=threshold_pct,
        )
        for r in rows
    ]
    return ThresholdAlertsResponse(threshold_pct=threshold_pct, alerts=alerts)


# ── US-51: Period comparison ───────────────────────────────────────────────────

async def _period_stats(
    db: AsyncSession,
    period_from: date,
    period_to: date,
) -> PeriodStats:
    filters = [Session.date >= period_from, Session.date <= period_to]
    totals_stmt = (
        select(
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(AcademicStudent, AcademicStudent.matricule == Absence.student_matricule)
        .where(and_(*filters))
    )
    totals = (await db.execute(totals_stmt)).one()
    total = totals.total or 0
    absences = totals.absences or 0

    return PeriodStats(
        period_from=period_from,
        period_to=period_to,
        total_absences=absences,
        total_records=total,
        absence_rate=_rate(absences, total),
        by_filiere=await _stats_by_filiere(db, filters),
        by_module=await _stats_by_module(db, filters),
    )


async def compare_periods(
    db: AsyncSession,
    p1_from: date,
    p1_to: date,
    p2_from: date,
    p2_to: date,
) -> PeriodComparisonResponse:
    p1 = await _period_stats(db, p1_from, p1_to)
    p2 = await _period_stats(db, p2_from, p2_to)
    return PeriodComparisonResponse(
        period1=p1,
        period2=p2,
        rate_delta=round(p2.absence_rate - p1.absence_rate, 2),
        count_delta=p2.total_absences - p1.total_absences,
    )


# ── US-52: Student self-stats ─────────────────────────────────────────────────

async def get_student_stats(
    db: AsyncSession,
    student_matricule: str,
    threshold_pct: float = AT_RISK_THRESHOLD_PCT,
) -> StudentStatsResponse:
    # Overall totals
    totals_stmt = (
        select(
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
            func.sum(case((Absence.statut_justificatif == "justifiee", 1), else_=0)).label("justified"),
        )
        .where(Absence.student_matricule == student_matricule)
    )
    totals = (await db.execute(totals_stmt)).one()
    total = totals.total or 0
    absences = totals.absences or 0
    justified = totals.justified or 0

    # Per-module
    stmt = (
        select(
            Module.id,
            Module.code,
            Module.nom,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.is_absent == True, 1), else_=0)).label("absences"),
            func.sum(case((Absence.statut_justificatif == "justifiee", 1), else_=0)).label("justified"),
        )
        .join(Session, Session.id == Absence.session_id)
        .join(Module, Module.id == Session.module_id)
        .where(Absence.student_matricule == student_matricule)
        .group_by(Module.id, Module.code, Module.nom)
        .order_by(Module.code)
    )
    rows = (await db.execute(stmt)).all()

    by_module = [
        StudentModuleStat(
            module_id=r.id,
            module_code=r.code,
            module_nom=r.nom,
            total_sessions=r.total,
            total_absences=r.absences or 0,
            absence_rate=_rate(r.absences or 0, r.total),
            justified_count=r.justified or 0,
            is_at_risk=_rate(r.absences or 0, r.total) >= threshold_pct,
        )
        for r in rows
    ]

    return StudentStatsResponse(
        student_matricule=student_matricule,
        total_sessions=total,
        total_absences=absences,
        overall_rate=_rate(absences, total),
        justified_count=justified,
        unjustified_count=absences - justified,
        by_module=by_module,
    )


# ── US-53: Realtime polling ────────────────────────────────────────────────────

async def get_realtime_stats(db: AsyncSession) -> RealtimeStatsResponse:
    today = date.today()
    now = datetime.now(timezone.utc)

    absences_today_stmt = (
        select(func.count(Absence.id))
        .join(Session, Session.id == Absence.session_id)
        .where(Session.date == today, Absence.is_absent == True)
    )
    absences_today = (await db.execute(absences_today_stmt)).scalar_one() or 0

    active_sessions_stmt = (
        select(func.count(Session.id))
        .where(
            Session.date == today,
            Session.status == SessionStatusEnum.IN_PROGRESS,
        )
    )
    active_sessions = (await db.execute(active_sessions_stmt)).scalar_one() or 0

    last_absence_stmt = (
        select(Absence.updated_at)
        .order_by(Absence.updated_at.desc())
        .limit(1)
    )
    last_absence_at = (await db.execute(last_absence_stmt)).scalar_one_or_none()

    return RealtimeStatsResponse(
        absences_today=absences_today,
        active_sessions_now=active_sessions,
        last_absence_recorded_at=last_absence_at,
        timestamp=now,
    )
