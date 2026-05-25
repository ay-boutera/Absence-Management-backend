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
from app.services.cloudinary_service import delete_cloudinary_file, upload_justification_pdf

router = APIRouter(tags=["justifications"])


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
- `document`: PDF, max 5 MB

Conditional fields:
- `absence_id` only for `scope_type=absence`
- `session_id` only for `scope_type=session`
- `start_date` + `end_date` only for `scope_type=range`
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
    absence_id: Optional[UUID] = Form(default=None),
    session_id: Optional[UUID] = Form(default=None),
    start_date: Optional[date] = Form(default=None),
    end_date: Optional[date] = Form(default=None),
    reason: str = Form(...),
    document: UploadFile = File(...),
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    payload = JustificationSubmitRequest(
        scope_type=scope_type,
        absence_id=absence_id,
        session_id=session_id,
        start_date=start_date,
        end_date=end_date,
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

    uploaded = await upload_justification_pdf(document)

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
    return _build_response(justification, academic_student)


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


@router.patch(
    "/justifications/mine/{justification_id}",
    response_model=JustificationResponse,
    summary="Edit a pending justification",
    description="""
Edit only pending justification fields:
- optional `reason`
- optional replacement `document` (PDF max 5 MB)

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
        uploaded = await upload_justification_pdf(document)
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


@router.patch(
    "/justifications/approve-all",
    response_model=BulkReviewResponse,
    summary="Bulk approve justifications",
    description="""
Bulk-approve pending justifications.
- If `ids` provided: approve pending items in those ids.
- If `ids` empty/omitted: approve all pending justifications.
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

    stmt = (
        update(Justification)
        .where(and_(*conditions))
        .values(
            status=JustificationStatus.APPROVED,
            rejection_reason=None,
            reviewed_by=current_user.email,
            reviewed_at=now,
            updated_at=now,
        )
    )
    result = await db.execute(stmt)

    return BulkReviewResponse(
        affected=result.rowcount or 0,
        status="approved",
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


@router.patch(
    "/justifications/reject-all",
    response_model=BulkReviewResponse,
    summary="Bulk reject justifications",
    description="""
Bulk-reject pending justifications.
- If `ids` provided: reject pending items in those ids.
- If `ids` empty/omitted: reject all pending justifications.
- Optional `reason` is applied to all affected rows.
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

    stmt = (
        update(Justification)
        .where(and_(*conditions))
        .values(
            status=JustificationStatus.REJECTED,
            rejection_reason=payload.reason,
            reviewed_by=current_user.email,
            reviewed_at=now,
            updated_at=now,
        )
    )
    result = await db.execute(stmt)

    return BulkReviewResponse(
        affected=result.rowcount or 0,
        status="rejected",
        rejection_reason=payload.reason,
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


@router.patch(
    "/justifications/{justification_id}/approve",
    response_model=ApproveResponse,
    summary="Approve a single justification",
    description="Admin approves one pending justification.",
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

    return ApproveResponse(id=cast(UUID, item.id), reviewed_at=now, reviewed_by=current_user.email)


@router.patch(
    "/justifications/{justification_id}/reject",
    response_model=RejectResponse,
    summary="Reject a single justification",
    description="Admin rejects one pending justification with optional rejection reason.",
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

    return RejectResponse(
        id=cast(UUID, item.id),
        rejection_reason=payload.reason,
        reviewed_at=now,
        reviewed_by=current_user.email,
    )


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
