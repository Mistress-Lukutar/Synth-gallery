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
from ...infrastructure.repositories import PhotoRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from ...infrastructure.storage import get_storage, LocalStorage
from .deps import get_permission_service

router = APIRouter()

# Get storage backend
storage = get_storage()


def _decrypt_file_response(file_path: Path, dek: bytes, content_type: str = None) -> Response:
    """Decrypt server-side encrypted file and return as Response."""
    with open(file_path, "rb") as f:
        encrypted_data = f.read()

    try:
        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed")

    return Response(content=decrypted_data, media_type=content_type or "image/jpeg")


def _get_encryption_type(photo: dict) -> str:
    """Determine encryption type from photo metadata."""
    if photo.get("safe_id"):
        return "e2e"
    elif photo.get("is_encrypted"):
        return "server"
    return "none"


async def _get_storage_response(filename: str, folder: str) -> Response:
    """Get file response using storage backend."""
    if not storage.exists(filename, folder):
        raise HTTPException(status_code=404)
    
    if not isinstance(storage, LocalStorage):
        url = storage.get_url(filename, folder, expires=3600)
        return RedirectResponse(url=url)
    
    file_path = storage.get_path(filename, folder)
    return FileResponse(file_path)


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
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        photo = photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
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
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Content-Type": content_type
                    }
                )
            else:
                url = storage.get_url(filename, "uploads", expires=3600)
                return RedirectResponse(
                    url=url,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Content-Type": content_type
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
                encrypted_data = await storage.download(filename, "uploads")
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                return Response(content=decrypted_data, media_type=content_type)
        
        # Regular files: serve directly
        return await _get_storage_response(filename, "uploads")
    
    finally:
        db.close()


@router.get("/files/{photo_id}/thumbnail")
async def get_file_thumbnail(photo_id: str, request: Request):
    """Thumbnail access endpoint."""
    user = require_user(request)
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        photo = photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Auto-regenerate missing thumbnails
        if not storage.exists(photo_id, "thumbnails"):
            from ...infrastructure.services.thumbnail import regenerate_thumbnail
            if not regenerate_thumbnail(photo_id, user["id"]):
                raise HTTPException(status_code=404, detail="Thumbnail unavailable")
        
        encryption = _get_encryption_type(photo)
        
        # E2E files: serve as-is
        if encryption == "e2e":
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(photo_id, "thumbnails")
                return FileResponse(
                    file_path,
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
                    encrypted_data = f.read()
            else:
                encrypted_data = await storage.download(photo_id, "thumbnails")
            
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
            return Response(content=decrypted_data, media_type="image/jpeg")
        
        # Regular files
        return await _get_storage_response(photo_id, "thumbnails")
    
    finally:
        db.close()
