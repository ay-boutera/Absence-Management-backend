from __future__ import annotations

import io
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Literal

try:
    from cloudinary import config as cloudinary_config  # type: ignore[import-not-found]
    from cloudinary import uploader, utils as cloudinary_utils  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    cloudinary_config = None  # type: ignore[assignment]
    uploader = None  # type: ignore[assignment]
    cloudinary_utils = None  # type: ignore[assignment]

import httpx
from fastapi import HTTPException, UploadFile, status

from app.config import settings

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

_MIME_TO_RESOURCE: dict[str, str] = {
    "application/pdf": "raw",
    "image/jpeg": "image",
    "image/png": "image",
}

_EXT_TO_RESOURCE: dict[str, str] = {
    ".pdf": "raw",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
}

_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
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


def _parse_cloudinary_url(url: str) -> tuple[str, Literal["upload", "private"]]:
    """Infer (resource_type, access_type) from a stored Cloudinary URL."""
    resource_type = "raw" if ("/raw/" in url) else "image"
    access_type: Literal["upload", "private"] = "private" if "/private/" in url else "upload"
    return resource_type, access_type


async def upload_justification_document(file: UploadFile) -> dict[str, str]:
    """Upload a justification document (PDF, JPG, or PNG) to Cloudinary as private.

    Returns: url, public_id, original_filename, resource_type
    """
    if uploader is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary integration is not available",
        )

    content_type = (file.content_type or "").lower().strip()
    cloudinary_resource_type = _MIME_TO_RESOURCE.get(content_type)

    original_name = file.filename or "document"
    ext = Path(original_name).suffix.lower()

    if cloudinary_resource_type is None:
        cloudinary_resource_type = _EXT_TO_RESOURCE.get(ext)

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

    stem = Path(original_name).stem or "document"
    unique_suffix = uuid.uuid4().hex[:8]

    if cloudinary_resource_type == "raw":
        public_id = f"justifications/{stem}_{unique_suffix}{ext}"
    else:
        public_id = f"justifications/{stem}_{unique_suffix}"

    try:
        uploaded = uploader.upload(
            content,
            resource_type=cloudinary_resource_type,
            # Upload as private: CDN delivery is blocked on this account,
            # so we use the Cloudinary management API (private_download_url)
            # to serve files instead of direct CDN URLs.
            type="private",
            public_id=public_id,
            overwrite=False,
        )
    except Exception as exc:
        logger.exception(
            "Cloudinary upload failed: filename=%s resource_type=%s public_id=%s",
            original_name, cloudinary_resource_type, public_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload justification document",
        ) from exc

    url = str(uploaded.get("secure_url") or uploaded.get("url") or "")
    if not url:
        logger.error("Cloudinary returned no URL for public_id=%s", public_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary upload succeeded but returned no URL",
        )

    return {
        "url": url,
        "public_id": str(uploaded.get("public_id") or ""),
        "original_filename": original_name,
        "resource_type": cloudinary_resource_type,
    }


upload_justification_pdf = upload_justification_document


async def stream_document(public_id: str, document_url: str, document_name: str) -> tuple[bytes, str, str]:
    """Fetch document bytes from Cloudinary using the management API (bypasses CDN ACL).

    Returns (file_bytes, content_type, filename).

    Handles both legacy type='upload' resources (via archive/ZIP proxy) and
    new type='private' resources (via private_download_url).
    """
    if cloudinary_utils is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary integration is not available",
        )

    resource_type, access_type = _parse_cloudinary_url(document_url)
    ext = Path(public_id).suffix.lower()
    content_type = _EXT_TO_MIME.get(ext, "application/octet-stream")
    filename = document_name or Path(public_id).name

    if access_type == "private":
        # New-style: private_download_url goes through api.cloudinary.com — no CDN needed.
        dl_url = cloudinary_utils.private_download_url(
            public_id, "", resource_type=resource_type, type="private", attachment=True,
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(dl_url)
        if resp.status_code != 200:
            logger.error("Cloudinary private download failed: status=%d public_id=%s", resp.status_code, public_id)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch document from storage")
        return resp.content, content_type, filename

    # Legacy type='upload': CDN returns 401 due to account ACL restriction.
    # Use generate_archive which goes through api.cloudinary.com and returns a ZIP.
    archive_url = cloudinary_utils.download_archive_url(
        resource_type=resource_type,
        type="upload",
        public_ids=[public_id],
        flatten_folders=True,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(archive_url)
    if resp.status_code != 200:
        logger.error("Cloudinary archive download failed: status=%d public_id=%s", resp.status_code, public_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch document from storage")

    zip_bytes = io.BytesIO(resp.content)
    try:
        with zipfile.ZipFile(zip_bytes) as zf:
            names = zf.namelist()
            if not names:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Empty archive returned from storage")
            file_bytes = zf.read(names[0])
    except zipfile.BadZipFile as exc:
        logger.error("Bad ZIP returned for public_id=%s", public_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Corrupted archive from storage") from exc

    return file_bytes, content_type, filename


def delete_cloudinary_file(public_id: str) -> None:
    if not public_id:
        return
    if uploader is None:
        logger.error("Cloudinary deletion skipped because cloudinary package is unavailable")
        return

    # Try private first (new uploads), then upload (legacy), across both resource types
    for access_type in ("private", "upload"):
        for resource_type in ("raw", "image"):
            try:
                result = uploader.destroy(public_id, resource_type=resource_type, type=access_type, invalidate=True)
                if result.get("result") == "ok":
                    return
            except Exception:
                logger.debug(
                    "Cloudinary destroy failed: public_id=%s resource_type=%s type=%s",
                    public_id, resource_type, access_type,
                )
