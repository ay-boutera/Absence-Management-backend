from __future__ import annotations

from datetime import datetime, timezone, date
from typing import Any, Optional, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UserRole
from app.config.enums import JustificationScopeType, JustificationStatus
from app.db import get_db
from app.helpers.permissions import require_role
from app.models import Absence, Session
from app.models.justification import Justification
from app.models.student import AcademicStudent, Student
from app.schemas.justification import (
    ApproveResponse,
    BulkApproveRequest,
    BulkRejectRequest,
    BulkReviewResponse,
    JustificationDocumentResponse,
    JustificationEditRequest,
    JustificationListResponse,
    JustificationResponse,
    JustificationStudentInfo,
    JustificationDocumentInfo,
    JustificationSubmitRequest,
    RejectRequest,
    RejectResponse,
)
from app.services.cloudinary_service import delete_cloudinary_file, upload_justification_document
from app.services.notification_service import create_and_push, notify_admins

router = APIRouter(tags=["justifications"])


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_response(item: Justification, student: AcademicStudent) -> JustificationResponse:
    return JustificationResponse(
        id=cast(UUID, item.id),
        student=JustificationStudentInfo(
            id=cast(UUID, student.id),
            full_name=f"{cast(str, student.nom)} {cast(str, student.prenom)}",
            email=cast(str, student.email),
        ),
        scope_type=cast(JustificationScopeType, item.scope_type),
        absence_id=cast(Optional[UUID], item.absence_id),
        session_id=cast(Optional[UUID], item.session_id),
        start_date=cast(Optional[date], item.start_date),
        end_date=cast(Optional[date], item.end_date),
        reason=cast(str, item.reason),
        document=JustificationDocumentInfo(
            name=cast(str, item.document_name),
            url=cast(str, item.document_url),
        ),
        status=cast(JustificationStatus, item.status),
        rejection_reason=cast(Optional[str], item.rejection_reason),
        reviewed_by=cast(Optional[str], item.reviewed_by),
        reviewed_at=cast(Optional[datetime], item.reviewed_at),
        created_at=cast(datetime, item.created_at),
        updated_at=cast(datetime, item.updated_at),
    )


async def _get_academic_student_from_user(db: AsyncSession, current_user: Student) -> AcademicStudent:
    academic_student = (
        await db.execute(select(AcademicStudent).where(func.lower(AcademicStudent.email) == current_user.email.lower()))
    ).scalar_one_or_none()
    if academic_student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student profile not found")
    return academic_student


async def _mark_absences_as_justified(db: AsyncSession, justification: Justification) -> None:
    """
    Set statut_justificatif='justified' on every Absence covered by an approved
    justification.  This prevents those absences from counting toward thresholds.
    """
    # Resolve the student's matricule from their AcademicStudent record
    acad = (
        await db.execute(select(AcademicStudent).where(AcademicStudent.id == justification.student_id))
    ).scalar_one_or_none()
    if acad is None:
        return
    matricule: str = cast(str, acad.matricule)

    scope = cast(JustificationScopeType, justification.scope_type)

    if scope == JustificationScopeType.ABSENCE and cast(Optional[UUID], justification.absence_id):
        await db.execute(
            update(Absence)
            .where(Absence.id == justification.absence_id)
            .values(statut_justificatif="justified")
        )

    elif scope == JustificationScopeType.SESSION and cast(Optional[UUID], justification.session_id):
        await db.execute(
            update(Absence)
            .where(
                and_(
                    Absence.session_id == justification.session_id,
                    Absence.student_matricule == matricule,
                    Absence.is_absent.is_(True),
                )
            )
            .values(statut_justificatif="justified")
        )

    elif scope == JustificationScopeType.RANGE:
        if cast(Optional[date], justification.start_date) and cast(Optional[date], justification.end_date):
            session_ids_subq = (
                select(Session.id)
                .where(
                    and_(
                        Session.date >= justification.start_date,
                        Session.date <= justification.end_date,
                    )
                )
                .scalar_subquery()
            )
            await db.execute(
                update(Absence)
                .where(
                    and_(
                        Absence.session_id.in_(session_ids_subq),
                        Absence.student_matricule == matricule,
                        Absence.is_absent.is_(True),
                    )
                )
                .values(statut_justificatif="justified")
            )


async def _get_student_user_id(db: AsyncSession, academic_student: AcademicStudent) -> UUID | None:
    """Resolve the student_users.id for a given AcademicStudent (needed for notifications)."""
    student_user = (
        await db.execute(
            select(Student).where(
                func.lower(Student.email) == academic_student.email.lower()
            )
        )
    ).scalar_one_or_none()
    return cast(UUID, student_user.id) if student_user else None


# ── POST /justifications ───────────────────────────────────────────────────────

@router.post(
    "/justifications",
    response_model=JustificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a justification",
    description="""
Student submits a justification with multipart/form-data.

Required:
- `scope_type`: `absence` | `session` | `range`
- `reason`: max 500 chars
- `document`: PDF, JPG, or PNG, max 5 MB

Conditional fields:
- `absence_id` only for `scope_type=absence`
- `session_id` only for `scope_type=session`
- `start_date` + `end_date` only for `scope_type=range`

**Side-effects:**
- Stores the document on Cloudinary.
- Sends a real-time notification to every active admin.
""",
    responses={
        201: {"description": "Justification created"},
        400: {"description": "Validation/file error"},
        404: {"description": "Referenced absence/session not found"},
        409: {"description": "Duplicate justification"},
    },
)
async def submit_justification(
    scope_type: JustificationScopeType = Form(...),
    absence_id: Optional[str] = Form(default=None),
    session_id: Optional[str] = Form(default=None),
    start_date: Optional[str] = Form(default=None),
    end_date: Optional[str] = Form(default=None),
    reason: str = Form(...),
    document: UploadFile = File(...),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    def _uuid(v: Optional[str]) -> Optional[UUID]:
        if not v:
            return None
        try:
            return UUID(v)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid UUID: {v!r}")

    def _date(v: Optional[str]) -> Optional[date]:
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid date: {v!r}")

    payload = JustificationSubmitRequest(
        scope_type=scope_type,
        absence_id=_uuid(absence_id),
        session_id=_uuid(session_id),
        start_date=_date(start_date),
        end_date=_date(end_date),
        reason=reason,
    )

    academic_student = await _get_academic_student_from_user(db, current_user)

    if payload.scope_type == JustificationScopeType.ABSENCE:
        absence = (
            await db.execute(
                select(Absence).where(
                    Absence.id == payload.absence_id,
                    Absence.student_matricule == academic_student.matricule,
                )
            )
        ).scalar_one_or_none()
        if absence is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Absence not found")

    if payload.scope_type == JustificationScopeType.SESSION:
        session = (
            await db.execute(
                select(Session).where(
                    Session.id == payload.session_id,
                    Session.group == academic_student.groupe,
                    Session.year == academic_student.niveau,
                )
            )
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if payload.scope_type == JustificationScopeType.RANGE:
        start_date_val = cast(date, payload.start_date)
        end_date_val = cast(date, payload.end_date)
        overlap = (
            await db.execute(
                select(Justification).where(
                    Justification.student_id == academic_student.id,
                    Justification.scope_type == JustificationScopeType.RANGE,
                    Justification.start_date <= end_date_val,
                    Justification.end_date >= start_date_val,
                )
            )
        ).scalar_one_or_none()
        if overlap is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A justification already exists overlapping this date range",
            )

    uploaded = await upload_justification_document(document)

    justification = Justification(
        student_id=academic_student.id,
        scope_type=payload.scope_type,
        absence_id=payload.absence_id,
        session_id=payload.session_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
        document_url=uploaded["url"],
        document_name=uploaded["original_filename"],
        cloudinary_public_id=uploaded["public_id"],
        status=JustificationStatus.PENDING,
    )
    db.add(justification)
    try:
        await db.flush()
    except IntegrityError as exc:
        delete_cloudinary_file(uploaded["public_id"])
        message = "Duplicate justification"
        error_text = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "uq_justification_student_absence" in error_text:
            message = "A justification already exists for this absence"
        elif "uq_justification_student_session" in error_text:
            message = "A justification already exists for this session"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc

    await db.refresh(justification)

    # ── Notify all active admins in real time ─────────────────────────────────
    student_name = f"{academic_student.prenom} {academic_student.nom}"
    await notify_admins(
        db,
        type="justification_submitted",
        title="Nouvelle justification soumise",
        body=f"{student_name} a soumis une nouvelle justification.",
        justification_id=cast(UUID, justification.id),
    )

    await db.commit()
    return _build_response(justification, academic_student)


# ── GET /justifications/mine ───────────────────────────────────────────────────

@router.get(
    "/justifications/mine",
    response_model=JustificationListResponse,
    summary="List my justifications",
    description="""
Returns paginated justifications for the authenticated student.

Query params:
- `page`, `page_size`
- `status`: `pending|approved|rejected`
- `sort_order`: `asc|desc` by `created_at`
""",
)
async def list_my_justifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(7, ge=1, le=100),
    status_filter: Optional[JustificationStatus] = Query(default=None, alias="status"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    academic_student = await _get_academic_student_from_user(db, current_user)

    filters = [Justification.student_id == academic_student.id]
    if status_filter:
        filters.append(Justification.status == status_filter)

    count_q = select(func.count(Justification.id)).where(and_(*filters))
    total = (await db.execute(count_q)).scalar_one()

    order_col = Justification.created_at.asc() if sort_order == "asc" else Justification.created_at.desc()
    rows = (
        await db.execute(
            select(Justification)
            .where(and_(*filters))
            .order_by(order_col)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    data = [_build_response(item, academic_student) for item in rows]
    return JustificationListResponse.from_items(total=total, page=page, page_size=page_size, data=data)


# ── GET /justifications/mine/{justification_id} ───────────────────────────────

@router.get(
    "/justifications/mine/{justification_id}",
    response_model=JustificationResponse,
    summary="Get one of my justifications",
    description="Returns one justification belonging to the authenticated student.",
    responses={404: {"description": "Justification not found"}},
)
async def get_my_justification(
    justification_id: UUID,
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    academic_student = await _get_academic_student_from_user(db, current_user)

    item = (
        await db.execute(
            select(Justification).where(
                Justification.id == justification_id,
                Justification.student_id == academic_student.id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")

    return _build_response(item, academic_student)


# ── PATCH /justifications/mine/{justification_id} ─────────────────────────────

@router.patch(
    "/justifications/mine/{justification_id}",
    response_model=JustificationResponse,
    summary="Edit a pending justification",
    description="""
Edit only pending justification fields:
- optional `reason`
- optional replacement `document` (PDF, JPG, or PNG, max 5 MB)

At least one of reason or document must be provided.
""",
    responses={
        200: {"description": "Justification updated"},
        400: {"description": "Invalid edit request / not pending / invalid file"},
        404: {"description": "Justification not found"},
    },
)
async def edit_my_justification(
    justification_id: UUID,
    reason: Optional[str] = Form(default=None),
    document: Optional[UploadFile] = File(default=None),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    if reason is not None and len(reason) > 500:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reason must not exceed 500 characters")
    if reason is not None:
        JustificationEditRequest(reason=reason)

    academic_student = await _get_academic_student_from_user(db, current_user)

    item = (
        await db.execute(
            select(Justification).where(
                Justification.id == justification_id,
                Justification.student_id == academic_student.id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")

    item_status = cast(JustificationStatus, item.status)
    if item_status != JustificationStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending justifications can be edited")

    if reason is None and document is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (reason or document) must be provided",
        )

    item_mut = cast(Any, item)

    if reason is not None:
        item_mut.reason = reason

    if document is not None:
        uploaded = await upload_justification_document(document)
        old_public_id = cast(str, item_mut.cloudinary_public_id)
        item_mut.document_url = uploaded["url"]
        item_mut.document_name = uploaded["original_filename"]
        item_mut.cloudinary_public_id = uploaded["public_id"]
        delete_cloudinary_file(old_public_id)

    item_mut.updated_at = datetime.now(timezone.utc)
    db.add(item_mut)
    await db.flush()
    await db.refresh(item)

    return _build_response(item, academic_student)


# ── DELETE /justifications/mine/{justification_id} ────────────────────────────

@router.delete(
    "/justifications/mine/{justification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a pending justification",
    description="Deletes a pending justification owned by the authenticated student.",
    responses={
        204: {"description": "Justification deleted"},
        400: {"description": "Only pending justifications can be cancelled"},
        404: {"description": "Justification not found"},
    },
)
async def delete_my_justification(
    justification_id: UUID,
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    academic_student = await _get_academic_student_from_user(db, current_user)

    item = (
        await db.execute(
            select(Justification).where(
                Justification.id == justification_id,
                Justification.student_id == academic_student.id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")

    item_status = cast(JustificationStatus, item.status)
    if item_status != JustificationStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending justifications can be cancelled")

    delete_cloudinary_file(cast(str, item.cloudinary_public_id))
    await db.delete(item)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── GET /justifications (admin) ────────────────────────────────────────────────

@router.get(
    "/justifications",
    response_model=JustificationListResponse,
    summary="List all justifications",
    description="""
Admin listing endpoint with pagination, filters, search and sorting.

Query params:
- `page`, `page_size`
- `search` (student full name / email / reason)
- `status`
- `scope_type`
- `sort_by`: `date|student|status`
- `sort_order`: `asc|desc`
""",
)
async def list_all_justifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(7, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    status_filter: Optional[JustificationStatus] = Query(default=None, alias="status"),
    scope_type: Optional[JustificationScopeType] = Query(default=None),
    sort_by: str = Query("date", pattern="^(date|student|status)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status_filter:
        filters.append(Justification.status == status_filter)
    if scope_type:
        filters.append(Justification.scope_type == scope_type)
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                AcademicStudent.nom.ilike(like),
                AcademicStudent.prenom.ilike(like),
                AcademicStudent.email.ilike(like),
                Justification.reason.ilike(like),
            )
        )

    base = select(Justification, AcademicStudent).join(AcademicStudent, Justification.student_id == AcademicStudent.id)
    count_stmt = select(func.count()).select_from(Justification).join(AcademicStudent, Justification.student_id == AcademicStudent.id)
    if filters:
        base = base.where(and_(*filters))
        count_stmt = count_stmt.where(and_(*filters))

    total = (await db.execute(count_stmt)).scalar_one()

    if sort_by == "student":
        sort_col = func.concat(AcademicStudent.nom, " ", AcademicStudent.prenom)
    elif sort_by == "status":
        sort_col = Justification.status
    else:
        sort_col = Justification.created_at

    if sort_order == "asc":
        base = base.order_by(sort_col.asc())
    else:
        base = base.order_by(sort_col.desc())

    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).all()
    data = [_build_response(item, student) for item, student in rows]
    return JustificationListResponse.from_items(total=total, page=page, page_size=page_size, data=data)


# ── PATCH /justifications/{id}/approve ────────────────────────────────────────

@router.patch(
    "/justifications/{justification_id}/approve",
    response_model=ApproveResponse,
    summary="Approve a single justification",
    description="""
Admin approves one pending justification.

**Side-effects:**
- Sends a real-time notification to the student.
- Note: absences always count toward the threshold regardless of justification status.
""",
    responses={
        200: {"description": "Approved"},
        400: {"description": "Justification already approved or rejected"},
        404: {"description": "Justification not found"},
    },
)
async def approve_justification(
    justification_id: UUID,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(select(Justification).where(Justification.id == justification_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")

    item_status = cast(JustificationStatus, item.status)
    if item_status != JustificationStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Justification already approved or rejected")

    item_mut = cast(Any, item)
    now = datetime.now(timezone.utc)
    item_mut.status = JustificationStatus.APPROVED
    item_mut.rejection_reason = None
    item_mut.reviewed_by = current_user.email
    item_mut.reviewed_at = now
    item_mut.updated_at = now
    db.add(item_mut)
    await db.flush()

    # ── Mark covered absences as justified ────────────────────────────────────
    await _mark_absences_as_justified(db, item)

    # ── Notify the student ────────────────────────────────────────────────────
    acad = (await db.execute(select(AcademicStudent).where(AcademicStudent.id == item.student_id))).scalar_one_or_none()
    if acad:
        student_user_id = await _get_student_user_id(db, acad)
        if student_user_id:
            await create_and_push(
                db,
                recipient_id=student_user_id,
                recipient_role="student",
                type="justification_approved",
                title="Justification approuvée ✓",
                body="Votre justification a été approuvée. Les absences correspondantes ne comptent plus dans votre total.",
                justification_id=cast(UUID, item.id),
            )

    await db.commit()
    return ApproveResponse(id=cast(UUID, item.id), reviewed_at=now, reviewed_by=current_user.email)


# ── PATCH /justifications/{id}/reject ─────────────────────────────────────────

@router.patch(
    "/justifications/{justification_id}/reject",
    response_model=RejectResponse,
    summary="Reject a single justification",
    description="""
Admin rejects one pending justification.

**Side-effects:**
- Sends a real-time notification to the student with the optional rejection reason.
""",
    responses={
        200: {"description": "Rejected"},
        400: {"description": "Justification already approved or rejected"},
        404: {"description": "Justification not found"},
    },
)
async def reject_justification(
    justification_id: UUID,
    payload: RejectRequest,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(select(Justification).where(Justification.id == justification_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")

    item_status = cast(JustificationStatus, item.status)
    if item_status != JustificationStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Justification already approved or rejected")

    item_mut = cast(Any, item)
    now = datetime.now(timezone.utc)
    item_mut.status = JustificationStatus.REJECTED
    item_mut.rejection_reason = payload.reason
    item_mut.reviewed_by = current_user.email
    item_mut.reviewed_at = now
    item_mut.updated_at = now
    db.add(item_mut)
    await db.flush()

    # ── Notify the student ────────────────────────────────────────────────────
    acad = (await db.execute(select(AcademicStudent).where(AcademicStudent.id == item.student_id))).scalar_one_or_none()
    if acad:
        student_user_id = await _get_student_user_id(db, acad)
        if student_user_id:
            body = "Votre justification a été refusée."
            if payload.reason:
                body += f" Motif : {payload.reason}"
            await create_and_push(
                db,
                recipient_id=student_user_id,
                recipient_role="student",
                type="justification_rejected",
                title="Justification refusée ✗",
                body=body,
                justification_id=cast(UUID, item.id),
            )

    await db.commit()
    return RejectResponse(
        id=cast(UUID, item.id),
        rejection_reason=payload.reason,
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


# ── PATCH /justifications/approve-all ─────────────────────────────────────────

@router.patch(
    "/justifications/approve-all",
    response_model=BulkReviewResponse,
    summary="Bulk approve justifications",
    description="""
Bulk-approve pending justifications.
- If `ids` provided: approve pending items in those ids.
- If `ids` empty/omitted: approve all pending justifications.

**Side-effects:** student notified for each approved justification.
""",
)
async def approve_all_justifications(
    payload: BulkApproveRequest,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    conditions = [Justification.status == JustificationStatus.PENDING]
    if payload.ids:
        conditions.append(Justification.id.in_(payload.ids))

    # Fetch all justifications that will be approved before updating
    to_approve = (
        await db.execute(select(Justification).where(and_(*conditions)))
    ).scalars().all()

    if not to_approve:
        return BulkReviewResponse(affected=0, status="approved", reviewed_at=now, reviewed_by=current_user.email)

    # Bulk-update status
    ids = [j.id for j in to_approve]
    await db.execute(
        update(Justification)
        .where(Justification.id.in_(ids))
        .values(
            status=JustificationStatus.APPROVED,
            rejection_reason=None,
            reviewed_by=current_user.email,
            reviewed_at=now,
            updated_at=now,
        )
    )

    # Mark absences + notify each student
    for j in to_approve:
        await _mark_absences_as_justified(db, j)

        acad = (await db.execute(select(AcademicStudent).where(AcademicStudent.id == j.student_id))).scalar_one_or_none()
        if acad:
            student_user_id = await _get_student_user_id(db, acad)
            if student_user_id:
                await create_and_push(
                    db,
                    recipient_id=student_user_id,
                    recipient_role="student",
                    type="justification_approved",
                    title="Justification approuvée ✓",
                    body="Votre justification a été approuvée. Les absences correspondantes ne comptent plus dans votre total.",
                    justification_id=cast(UUID, j.id),
                )

    await db.commit()
    return BulkReviewResponse(
        affected=len(to_approve),
        status="approved",
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


# ── PATCH /justifications/reject-all ──────────────────────────────────────────

@router.patch(
    "/justifications/reject-all",
    response_model=BulkReviewResponse,
    summary="Bulk reject justifications",
    description="""
Bulk-reject pending justifications.
- If `ids` provided: reject pending items in those ids.
- If `ids` empty/omitted: reject all pending justifications.
- Optional `reason` is applied to all affected rows.

**Side-effects:** same as single reject — student notified for each rejected item.
""",
)
async def reject_all_justifications(
    payload: BulkRejectRequest,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    conditions = [Justification.status == JustificationStatus.PENDING]
    if payload.ids:
        conditions.append(Justification.id.in_(payload.ids))

    to_reject = (
        await db.execute(select(Justification).where(and_(*conditions)))
    ).scalars().all()

    if not to_reject:
        return BulkReviewResponse(affected=0, status="rejected", reviewed_at=now, reviewed_by=current_user.email)

    ids = [j.id for j in to_reject]
    await db.execute(
        update(Justification)
        .where(Justification.id.in_(ids))
        .values(
            status=JustificationStatus.REJECTED,
            rejection_reason=payload.reason,
            reviewed_by=current_user.email,
            reviewed_at=now,
            updated_at=now,
        )
    )

    # Notify each student
    body_suffix = f" Motif : {payload.reason}" if payload.reason else ""
    for j in to_reject:
        acad = (await db.execute(select(AcademicStudent).where(AcademicStudent.id == j.student_id))).scalar_one_or_none()
        if acad:
            student_user_id = await _get_student_user_id(db, acad)
            if student_user_id:
                await create_and_push(
                    db,
                    recipient_id=student_user_id,
                    recipient_role="student",
                    type="justification_rejected",
                    title="Justification refusée ✗",
                    body=f"Votre justification a été refusée.{body_suffix}",
                    justification_id=cast(UUID, j.id),
                )

    await db.commit()
    return BulkReviewResponse(
        affected=len(to_reject),
        status="rejected",
        rejection_reason=payload.reason,
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


# ── GET /justifications/{id}/document ─────────────────────────────────────────

@router.get(
    "/justifications/{justification_id}/document",
    response_model=JustificationDocumentResponse,
    summary="Get justification document URL",
    description="Admin gets the stored Cloudinary document URL for a justification.",
    responses={
        200: {"description": "Document URL fetched"},
        404: {"description": "Justification or document not found"},
    },
)
async def get_justification_document(
    justification_id: UUID,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(select(Justification).where(Justification.id == justification_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justification not found")
    document_url = cast(Optional[str], item.document_url)
    if not document_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return JustificationDocumentResponse(
        document_name=cast(str, item.document_name),
        url=document_url,
    )
