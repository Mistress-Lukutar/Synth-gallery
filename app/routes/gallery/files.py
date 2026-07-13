'''
File:   files.py
Brief:  File serving routes for gallery media.
Author: Mistress-Lukutar
Date:   2026-07-13
Version: v1.0.0
'''
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from app.database import create_connection
from app.dependencies import require_user
from app.infrastructure.repositories import ItemRepository, ItemMediaRepository
from app.infrastructure.services.encryption import EncryptionService, dek_cache
from app.infrastructure.services.jxl_fallback_service import JxlFallbackService
from app.infrastructure.storage import get_storage, LocalStorage
from app.routes.gallery.deps import get_permission_service
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)

# Get storage backend
storage = get_storage()


def _is_plaintext_media(content: bytes) -> bool:
    """Check if content looks like a plaintext media file by magic bytes."""
    if len(content) < 12:
        return False
    if content.startswith(b"\xff\xd8"):
        return True
    if content.startswith(b"\x89PNG"):
        return True
    if content[:4] in (b"GIF8", b"GIF9"):
        return True
    if content[8:12] == b"WEBP":
        return True
    if content[4:8] in (b"ftyp", b"moov"):
        return True
    return False


def _client_accepts_jxl(request: Request) -> bool:
    """Return True if the request explicitly accepts image/jxl."""
    accept = request.headers.get("Accept", "")
    return "image/jxl" in accept


def _force_jpeg_fallback(request: Request) -> bool:
    """Return True when the format=jpeg query parameter is present."""
    return request.query_params.get("format") == "jpeg"


async def _serve_jxl_or_fallback(
    request: Request,
    photo_id: str,
    jxl_bytes: bytes,
    dek: bytes,
) -> Response:
    """Serve JXL directly or generate a JPEG fallback when needed.

    Uses the Accept header and the ``format=jpeg`` query parameter to decide
    which representation to return. Generated fallbacks are encrypted and
    cached with the same DEK as the original.
    """
    if _force_jpeg_fallback(request) or not _client_accepts_jxl(request):
        fallback_service = JxlFallbackService()
        jpeg_bytes = await fallback_service.get_fallback(photo_id, jxl_bytes, dek)
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    return Response(content=jxl_bytes, media_type="image/jxl")


def _get_file_record(item_id: str, item_repo: ItemRepository, item_media_repo=None):
    """Get file record from items table."""
    item = item_repo.get_by_id(item_id)
    if item and item.get("type") == "media":
        # Get media details if available
        media = item_media_repo.get_by_item_id(item_id) if item_media_repo else None
        # Convert item format to photo-like dict for backward compat
        # Storage uses item_id as filename
        return {
            "id": item["id"],
            "filename": item_id,  # Storage uses item_id as filename
            "title": item.get("title", item_id),
            "user_id": item.get("user_id"),
            "folder_id": item.get("folder_id"),
            "content_type": media.get("content_type", "image/jpeg") if media else "image/jpeg",
        }
    return None


@router.get("/files/{photo_id}")
async def get_file(photo_id: str, request: Request):
    """File access endpoint.

    Server-side encrypted files are decrypted on-the-fly using the owner's DEK.
    Plaintext legacy files are served directly with a warning.
    """
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_repo = ItemRepository(db)
        item_media_repo = ItemMediaRepository(db)

        # Get file record from items table
        file_record = _get_file_record(photo_id, item_repo, item_media_repo)
        if not file_record:
            raise HTTPException(status_code=404, detail="Item not found")

        # Check permissions using folder_id
        folder_id = file_record.get("folder_id")
        if folder_id and not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        photo = file_record
        filename = photo.get("filename", photo_id)
        content_type = photo.get("content_type") or "image/jpeg"

        owner_id = photo.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None

        if not dek:
            raise HTTPException(status_code=403, detail="Encryption key not available")

        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(filename, "uploads")
            with open(file_path, "rb") as f:
                data = f.read()
        else:
            data = await storage.download(filename, "uploads")

        try:
            decrypted_data = EncryptionService.decrypt_file(data, dek)
        except Exception:
            if _is_plaintext_media(data):
                logger.warning("Serving plaintext file: %s", filename)
                decrypted_data = data
            else:
                raise HTTPException(status_code=500, detail="Decryption failed")

        if content_type == "image/jxl":
            return await _serve_jxl_or_fallback(request, photo_id, decrypted_data, dek)

        return Response(content=decrypted_data, media_type=content_type)
    finally:
        db.close()


@router.get("/files/{photo_id}/thumbnail")
async def get_file_thumbnail(photo_id: str, request: Request):
    """Thumbnail access endpoint."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_repo = ItemRepository(db)
        item_media_repo = ItemMediaRepository(db)

        # Get file record from items table
        file_record = _get_file_record(photo_id, item_repo, item_media_repo)
        if not file_record:
            raise HTTPException(status_code=404, detail="Item not found")

        # Check permissions using folder_id
        folder_id = file_record.get("folder_id")
        if folder_id and not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        # Auto-regenerate missing thumbnails
        if not storage.exists(photo_id, "thumbnails"):
            from app.infrastructure.services.thumbnail import regenerate_thumbnail
            if not regenerate_thumbnail(photo_id, user["id"]):
                raise HTTPException(status_code=404, detail="Thumbnail unavailable")

        # Thumbnails are always generated as JPEG, regardless of the original
        # content type (e.g. image/jxl). Serving the original MIME here breaks
        # preview loading in browsers because the bytes are JPEG.
        thumbnail_content_type = "image/jpeg"

        owner_id = file_record.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None

        if not dek:
            raise HTTPException(status_code=403, detail="Encryption key not available")

        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(photo_id, "thumbnails")
            with open(file_path, "rb") as f:
                data = f.read()
        else:
            data = await storage.download(photo_id, "thumbnails")

        try:
            decrypted_data = EncryptionService.decrypt_file(data, dek)
        except Exception:
            if _is_plaintext_media(data):
                logger.warning("Serving plaintext thumbnail: %s", photo_id)
                decrypted_data = data
            else:
                raise HTTPException(status_code=500, detail="Decryption failed")
        return Response(content=decrypted_data, media_type=thumbnail_content_type)
    finally:
        db.close()
