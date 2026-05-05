"""File serving routes - unified access for all file types.

This module provides unified file access regardless of encryption type:
- Regular files: served directly
- Server-side encrypted: decrypted on server
- E2E encrypted (Safes): served as-is, client decrypts (X-Encryption: e2e header)
"""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import ItemRepository, ItemMediaRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from ...infrastructure.storage import get_storage, LocalStorage
from .deps import get_permission_service
from ...logging_config import get_logger

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


def _decrypt_file_response(file_path: Path, dek: bytes, content_type: str = None) -> Response:
    """Decrypt server-side encrypted file and return as Response.
    
    Falls back to serving raw bytes if the file appears to be an old
    plaintext upload (backward compatibility during migration).
    """
    with open(file_path, "rb") as f:
        data = f.read()

    try:
        decrypted_data = EncryptionService.decrypt_file(data, dek)
        return Response(content=decrypted_data, media_type=content_type or "image/jpeg")
    except Exception:
        if _is_plaintext_media(data):
            logger.warning(
                "Serving plaintext file (not encrypted): %s. Run encrypt_existing_uploads.py",
                file_path.name
            )
            return Response(content=data, media_type=content_type or "image/jpeg")
        raise HTTPException(status_code=500, detail="Decryption failed")


def _get_encryption_type(photo: dict) -> str:
    """Determine encryption type from photo metadata."""
    if photo.get("safe_id"):
        return "e2e"
    return "server"


async def _get_storage_response(filename: str, folder: str) -> Response:
    """Get file response using storage backend."""
    if not storage.exists(filename, folder):
        raise HTTPException(status_code=404)
    
    if not isinstance(storage, LocalStorage):
        url = storage.get_url(filename, folder, expires=3600)
        return RedirectResponse(url=url)
    
    file_path = storage.get_path(filename, folder)
    return FileResponse(file_path)


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
            "safe_id": item.get("safe_id"),
            "user_id": item.get("user_id"),
            "folder_id": item.get("folder_id"),
            "content_type": media.get("content_type", "image/jpeg") if media else "image/jpeg",
        }
    return None


@router.get("/files/{photo_id}")
async def get_file(photo_id: str, request: Request):
    """File access endpoint.
    
    Returns file with encryption headers:
    - X-Encryption: none|server|e2e
    - X-Safe-Id: {id} (only for e2e files)
    
    Note: Server never decrypts E2E files (true end-to-end encryption).
    Client must decrypt using Safe DEK from SafeCrypto.
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
        encryption = _get_encryption_type(photo)
        
        # E2E files: serve as-is, client decrypts
        if encryption == "e2e":
            if not storage.exists(filename, "uploads"):
                raise HTTPException(status_code=404)
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "uploads")
                return FileResponse(
                    file_path,
                    media_type=content_type,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                    }
                )
            else:
                url = storage.get_url(filename, "uploads", expires=3600)
                return RedirectResponse(
                    url=url,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                    }
                )
        
        # Server-side encrypted: decrypt on server
        if encryption == "server":
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None
            
            if not dek:
                raise HTTPException(status_code=403, detail="Encryption key not available")
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "uploads")
                return _decrypt_file_response(file_path, dek, content_type)
            else:
                data = await storage.download(filename, "uploads")
                try:
                    decrypted_data = EncryptionService.decrypt_file(data, dek)
                except Exception:
                    if _is_plaintext_media(data):
                        logger.warning("Serving plaintext file from S3: %s", filename)
                        decrypted_data = data
                    else:
                        raise HTTPException(status_code=500, detail="Decryption failed")
                return Response(content=decrypted_data, media_type=content_type)
        
        # Regular files: serve directly
        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(filename, "uploads")
            return FileResponse(file_path, media_type=content_type)
        else:
            url = storage.get_url(filename, "uploads", expires=3600)
            return RedirectResponse(url=url)
    
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
        
        photo = file_record
        
        # Auto-regenerate missing thumbnails
        if not storage.exists(photo_id, "thumbnails"):
            from ...infrastructure.services.thumbnail import regenerate_thumbnail
            if not regenerate_thumbnail(photo_id, user["id"]):
                raise HTTPException(status_code=404, detail="Thumbnail unavailable")
        
        encryption = _get_encryption_type(photo)
        content_type = photo.get("content_type", "image/jpeg")
        
        # E2E files: serve as-is
        if encryption == "e2e":
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(photo_id, "thumbnails")
                return FileResponse(
                    file_path,
                    media_type=content_type,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"]
                    }
                )
            else:
                url = storage.get_url(photo_id, "thumbnails", expires=3600)
                return RedirectResponse(
                    url=url,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"]
                    }
                )
        
        # Server-side encrypted: decrypt on server
        if encryption == "server":
            owner_id = photo.get("user_id")
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
            return Response(content=decrypted_data, media_type=content_type)
        
        # Regular files
        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(photo_id, "thumbnails")
            return FileResponse(file_path, media_type=content_type)
        else:
            url = storage.get_url(photo_id, "thumbnails", expires=3600)
            return RedirectResponse(url=url)
    
    finally:
        db.close()
