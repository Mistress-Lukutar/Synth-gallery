"""File serving routes - unified access for all file types.

This module provides unified file access regardless of encryption type:
- Regular files: served directly
- Server-side encrypted: decrypted on server (legacy support)
- E2E encrypted (Safes): served as-is, client decrypts (X-Encryption: e2e header)

Migration:
- /files/{photo_id} - NEW unified endpoint (recommended)
- /uploads/{filename} - DEPRECATED, use /files/{id}
- /thumbnails/{filename} - DEPRECATED, use /files/{id}/thumbnail
"""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from ...config import UPLOADS_DIR, THUMBNAILS_DIR
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import PhotoRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from ...infrastructure.storage import get_storage, LocalStorage
from .deps import get_permission_service

router = APIRouter()

# Get storage backend
storage = get_storage()


# DEPRECATION_WARNING_HEADER = "299 - "  # Format: "299 - " + agent + " \"\" \"\""


def _decrypt_file_response(file_path: Path, dek: bytes, content_type: str = None) -> Response:
    """Decrypt server-side encrypted file and return as Response.
    
    Args:
        file_path: Path to encrypted file
        dek: Data Encryption Key (server-side DEK, NOT Safe DEK)
        content_type: MIME type (e.g., 'image/jpeg')
    """
    with open(file_path, "rb") as f:
        encrypted_data = f.read()

    try:
        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed")

    return Response(
        content=decrypted_data, 
        media_type=content_type or "image/jpeg"
    )


def _get_encryption_type(photo: dict) -> str:
    """Determine encryption type from photo metadata.
    
    Args:
        photo: Photo dict from repository
        
    Returns:
        'e2e' - End-to-end encrypted (Safe)
        'server' - Server-side encrypted (legacy)
        'none' - Not encrypted
    """
    if photo.get("safe_id"):
        return "e2e"
    elif photo.get("is_encrypted"):
        return "server"
    return "none"


async def _get_storage_response(filename: str, folder: str) -> Response:
    """Get file response using storage backend.
    
    For LocalStorage: returns FileResponse
    For S3: returns RedirectResponse to presigned URL
    """
    if not storage.exists(filename, folder):
        raise HTTPException(status_code=404)
    
    # For S3 storage, redirect to presigned URL
    if not isinstance(storage, LocalStorage):
        url = storage.get_url(filename, folder, expires=3600)
        return RedirectResponse(url=url)
    
    # For local storage, return FileResponse
    file_path = storage.get_path(filename, folder)
    return FileResponse(file_path)


# =============================================================================
# UNIFIED FILE ENDPOINTS (NEW - RECOMMENDED)
# =============================================================================

@router.get("/files/{photo_id}")
async def get_file(photo_id: str, request: Request):
    """Unified file access endpoint.
    
    Returns file with appropriate encryption headers:
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
        
        # Check access
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
    """Unified thumbnail access endpoint.
    
    Same encryption semantics as /files/{photo_id}.
    Auto-regenerates missing thumbnails.
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


# =============================================================================
# LEGACY ENDPOINTS (DEPRECATED - Maintained for backward compatibility)
# =============================================================================

@router.get("/uploads/{filename}")
async def get_upload_legacy(request: Request, filename: str):
    """DEPRECATED: Use /files/{photo_id} instead.
    
    Serves original photo with legacy URL format.
    """
    user = require_user(request)
    
    # Validate filename
    photo_id = Path(filename).stem
    if not photo_id:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    # Check file exists
    if not storage.exists(filename, "uploads"):
        raise HTTPException(status_code=404)
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        photo = photo_repo.get_by_id(photo_id)
        content_type = photo.get("content_type") if photo else "image/jpeg"
        encryption = _get_encryption_type(photo) if photo else "none"
        
        # Build response with deprecation notice
        headers = {
            "Deprecation": "Sun, 01 Jun 2026 00:00:00 GMT",
            "Link": f"</files/{photo_id}>; rel=successor-version"
        }
        
        # E2E files: serve as-is
        if encryption == "e2e":
            headers["X-Encryption"] = "e2e"
            headers["X-Safe-Id"] = photo["safe_id"]
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "uploads")
                return FileResponse(file_path, headers=headers)
            else:
                url = storage.get_url(filename, "uploads", expires=3600)
                return RedirectResponse(url=url, headers=headers)
        
        # Server-side encrypted
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
        
        # Regular files
        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(filename, "uploads")
            return FileResponse(file_path, headers=headers)
        else:
            url = storage.get_url(filename, "uploads", expires=3600)
            return RedirectResponse(url=url, headers=headers)
    
    finally:
        db.close()


@router.get("/thumbnails/{filename}")
async def get_thumbnail_legacy(request: Request, filename: str):
    """DEPRECATED: Use /files/{photo_id}/thumbnail instead.
    
    Serves thumbnail with legacy URL format.
    """
    user = require_user(request)
    
    photo_id = Path(filename).stem
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Auto-regenerate missing thumbnails
        if not storage.exists(filename, "thumbnails"):
            from ...infrastructure.services.thumbnail import regenerate_thumbnail
            if not regenerate_thumbnail(photo_id, user["id"]):
                raise HTTPException(status_code=404)
        
        photo = photo_repo.get_by_id(photo_id)
        encryption = _get_encryption_type(photo) if photo else "none"
        
        # Build response with deprecation notice
        headers = {
            "Deprecation": "Sun, 01 Jun 2026 00:00:00 GMT",
            "Link": f"</files/{photo_id}/thumbnail>; rel=successor-version"
        }
        
        # E2E files
        if encryption == "e2e":
            headers["X-Encryption"] = "e2e"
            headers["X-Safe-Id"] = photo["safe_id"]
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "thumbnails")
                return FileResponse(file_path, headers=headers)
            else:
                url = storage.get_url(filename, "thumbnails", expires=3600)
                return RedirectResponse(url=url, headers=headers)
        
        # Server-side encrypted
        if encryption == "server":
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None
            
            if not dek:
                raise HTTPException(status_code=403, detail="Encryption key not available")
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "thumbnails")
                with open(file_path, "rb") as f:
                    encrypted_data = f.read()
            else:
                encrypted_data = await storage.download(filename, "thumbnails")
            
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
            return Response(content=decrypted_data, media_type="image/jpeg")
        
        # Regular files
        if isinstance(storage, LocalStorage):
            file_path = storage.get_path(filename, "thumbnails")
            return FileResponse(file_path, headers=headers)
        else:
            url = storage.get_url(filename, "thumbnails", expires=3600)
            return RedirectResponse(url=url, headers=headers)
    
    finally:
        db.close()
