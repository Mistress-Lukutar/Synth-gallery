"""File serving routes - uploads and thumbnails."""
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


def _decrypt_file_response(file_path: Path, dek: bytes, content_type: str = None) -> Response:
    """Decrypt file and return as Response.
    
    Args:
        file_path: Path to encrypted file
        dek: Data Encryption Key
        content_type: MIME type (e.g., 'image/jpeg'), auto-detected if None
    """
    with open(file_path, "rb") as f:
        encrypted_data = f.read()

    try:
        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed")

    # Default to JPEG if content_type not provided
    if not content_type:
        content_type = "image/jpeg"

    return Response(content=decrypted_data, media_type=content_type)


async def _get_file_response(filename: str, folder: str) -> Response:
    """Get file response using storage backend.
    
    For LocalStorage: returns FileResponse
    For S3: returns RedirectResponse to presigned URL
    """
    # Check if file exists
    if not storage.exists(filename, folder):
        raise HTTPException(status_code=404)
    
    # For S3 storage, redirect to presigned URL
    if not isinstance(storage, LocalStorage):
        # Generate presigned URL with 1 hour expiration
        url = storage.get_url(filename, folder, expires=3600)
        return RedirectResponse(url=url)
    
    # For local storage, return FileResponse
    file_path = storage.get_path(filename, folder)
    return FileResponse(file_path)


@router.get("/uploads/{filename}")
async def get_upload(request: Request, filename: str):
    """Serves original photo (protected by auth + folder access)."""
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
        
        # Handle safe files (end-to-end encrypted)
        if photo and photo.get("safe_id"):
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None
            
            if dek:
                try:
                    # For local storage, decrypt and return
                    if isinstance(storage, LocalStorage):
                        file_path = storage.get_path(filename, "uploads")
                        return _decrypt_file_response(file_path, dek, content_type)
                    # For S3, client needs to download and decrypt
                except HTTPException:
                    pass  # Client-encrypted file
            
            # Return file with encryption headers
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "uploads")
                return FileResponse(
                    file_path,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Photo-Id": photo_id
                    }
                )
            else:
                url = storage.get_url(filename, "uploads", expires=3600)
                return RedirectResponse(
                    url=url,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Photo-Id": photo_id
                    }
                )
        
        # Handle legacy server-side encryption
        if photo and photo["is_encrypted"]:
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None

            if not dek:
                raise HTTPException(status_code=403, detail="Encryption key not available")

            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "uploads")
                return _decrypt_file_response(file_path, dek, content_type)
            else:
                # For S3, we need to download, decrypt and return
                # This is inefficient but necessary for server-side encryption
                encrypted_data = await storage.download(filename, "uploads")
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                return Response(content=decrypted_data, media_type=content_type or "image/jpeg")

        # Regular file - use storage backend
        return await _get_file_response(filename, "uploads")
    finally:
        db.close()


@router.get("/thumbnails/{filename}")
async def get_thumbnail(request: Request, filename: str):
    """Serves thumbnail (protected by auth + folder access)."""
    user = require_user(request)

    photo_id = Path(filename).stem
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        # If thumbnail missing, try to regenerate from original
        if not storage.exists(filename, "thumbnails"):
            from ...infrastructure.services.thumbnail import regenerate_thumbnail
            if not regenerate_thumbnail(photo_id, user["id"]):
                raise HTTPException(status_code=404)

        photo = photo_repo.get_by_id(photo_id)
        
        # Handle safe files
        if photo and photo.get("safe_id"):
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None
            
            if dek:
                try:
                    if isinstance(storage, LocalStorage):
                        file_path = storage.get_path(filename, "thumbnails")
                        with open(file_path, "rb") as f:
                            encrypted_data = f.read()
                        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                        return Response(content=decrypted_data, media_type="image/jpeg")
                    else:
                        encrypted_data = await storage.download(filename, "thumbnails")
                        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                        return Response(content=decrypted_data, media_type="image/jpeg")
                except Exception:
                    pass
            
            if isinstance(storage, LocalStorage):
                file_path = storage.get_path(filename, "thumbnails")
                return FileResponse(
                    file_path,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Photo-Id": photo_id
                    }
                )
            else:
                url = storage.get_url(filename, "thumbnails", expires=3600)
                return RedirectResponse(
                    url=url,
                    headers={
                        "X-Encryption": "e2e",
                        "X-Safe-Id": photo["safe_id"],
                        "X-Photo-Id": photo_id
                    }
                )

        # Handle legacy server-side encryption
        if photo and photo["is_encrypted"]:
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

            try:
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
            except Exception:
                raise HTTPException(status_code=500, detail="Decryption failed")

            return Response(content=decrypted_data, media_type="image/jpeg")

        # Regular thumbnail - use storage backend
        return await _get_file_response(filename, "thumbnails")
    finally:
        db.close()
