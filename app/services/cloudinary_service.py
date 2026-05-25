from __future__ import annotations

import logging

try:
    from cloudinary import config as cloudinary_config  # type: ignore[import-not-found]
    from cloudinary import uploader  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - handled at runtime if dependency missing
    cloudinary_config = None  # type: ignore[assignment]
    uploader = None  # type: ignore[assignment]
from fastapi import HTTPException, UploadFile, status

from app.config import settings

logger = logging.getLogger(__name__)

MAX_PDF_SIZE_BYTES = 5 * 1024 * 1024


def configure_cloudinary() -> None:
    if cloudinary_config is None:
        raise RuntimeError("cloudinary package is not installed")
    cloudinary_config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


async def upload_justification_pdf(file: UploadFile) -> dict[str, str]:
    if uploader is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary integration is not available",
        )
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted (max 5 MB)",
        )

    content = await file.read()
    if len(content) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted (max 5 MB)",
        )

    try:
        uploaded = uploader.upload(
            content,
            folder="justifications",
            resource_type="raw",
            public_id=None,
            overwrite=False,
            use_filename=True,
            unique_filename=True,
            filename=file.filename,
        )
    except Exception as exc:
        logger.exception("Cloudinary upload failed for justification document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload justification document",
        ) from exc

    return {
        "url": str(uploaded.get("secure_url") or uploaded.get("url") or ""),
        "public_id": str(uploaded.get("public_id") or ""),
        "original_filename": str(file.filename or "document.pdf"),
    }


def delete_cloudinary_file(public_id: str) -> None:
    if not public_id:
        return
    if uploader is None:
        logger.error("Cloudinary deletion skipped because cloudinary package is unavailable")
        return

    try:
        uploader.destroy(public_id, resource_type="raw", invalidate=True)
    except Exception:
        logger.exception("Cloudinary deletion failed for public_id=%s", public_id)
