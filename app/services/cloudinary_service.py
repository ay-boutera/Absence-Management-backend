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

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

# MIME type → Cloudinary resource_type
_ALLOWED_TYPES: dict[str, str] = {
    "application/pdf": "raw",
    "image/jpeg": "image",
    "image/png": "image",
}


def configure_cloudinary() -> None:
    if cloudinary_config is None:
        raise RuntimeError("cloudinary package is not installed")
    cloudinary_config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


async def upload_justification_document(file: UploadFile) -> dict[str, str]:
    """Upload a justification document (PDF, JPG, or PNG) to Cloudinary.

    Returns: url, public_id, original_filename, resource_type
    """
    if uploader is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary integration is not available",
        )

    content_type = (file.content_type or "").lower()
    cloudinary_resource_type = _ALLOWED_TYPES.get(content_type)
    if cloudinary_resource_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF, JPG, and PNG files are accepted (max 5 MB)",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is too large. Maximum allowed size is 5 MB",
        )

    try:
        uploaded = uploader.upload(
            content,
            folder="justifications",
            resource_type=cloudinary_resource_type,
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
        "original_filename": str(file.filename or "document"),
        "resource_type": cloudinary_resource_type,
    }


# Keep the old name as an alias so nothing outside this module breaks
upload_justification_pdf = upload_justification_document


def delete_cloudinary_file(public_id: str) -> None:
    if not public_id:
        return
    if uploader is None:
        logger.error("Cloudinary deletion skipped because cloudinary package is unavailable")
        return

    # Try both resource types: existing PDFs are "raw", new images are "image"
    for resource_type in ("raw", "image"):
        try:
            result = uploader.destroy(public_id, resource_type=resource_type, invalidate=True)
            if result.get("result") == "ok":
                return
        except Exception:
            logger.debug("Cloudinary destroy failed for public_id=%s resource_type=%s", public_id, resource_type)
