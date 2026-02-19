"""File serving routes - uploads and thumbnails."""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import FileResponse

from ...config import UPLOADS_DIR, THUMBNAILS_DIR
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import PhotoRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from .deps import get_permission_service

router = APIRouter()


def _decrypt_file_response(file_path: Path, dek: bytes, filename: str) -> Response:
    """Decrypt file and return as Response."""
    with open(file_path, "rb") as f:
        encrypted_data = f.read()

    try:
        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed")

    ext = Path(filename).suffix.lower()
    content_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm"
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return Response(content=decrypted_data, media_type=content_type)


@router.get("/uploads/{filename}")
def get_upload(request: Request, filename: str):
    """Serves original photo (protected by auth + folder access)."""
    user = require_user(request)

    file_path = (UPLOADS_DIR / filename).resolve()
    if not file_path.is_relative_to(UPLOADS_DIR):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404)

    photo_id = Path(filename).stem
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        photo = photo_repo.get_by_id(photo_id)
        
        # Handle safe files (end-to-end encrypted)
        if photo and photo.get("safe_id"):
            owner_id = photo.get("user_id")
            dek = dek_cache.get(owner_id) if owner_id else None
            
            if dek:
                try:
                    return _decrypt_file_response(file_path, dek, filename)
                except HTTPException:
                    pass  # Client-encrypted file
            
            return FileResponse(
                file_path,
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

            return _decrypt_file_response(file_path, dek, filename)

        return FileResponse(file_path)
    finally:
        db.close()


@router.get("/thumbnails/{filename}")
def get_thumbnail(request: Request, filename: str):
    """Serves thumbnail (protected by auth + folder access)."""
    user = require_user(request)

    file_path = (THUMBNAILS_DIR / filename).resolve()
    if not file_path.is_relative_to(THUMBNAILS_DIR):
        raise HTTPException(status_code=400, detail="Invalid filename")

    photo_id = Path(filename).stem
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        # If thumbnail missing, try to regenerate from original
        if not file_path.exists():
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
                    with open(file_path, "rb") as f:
                        encrypted_data = f.read()
                    decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                    return Response(content=decrypted_data, media_type="image/jpeg")
                except Exception:
                    pass
            
            return FileResponse(
                file_path,
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

            with open(file_path, "rb") as f:
                encrypted_data = f.read()

            try:
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
            except Exception:
                raise HTTPException(status_code=500, detail="Decryption failed")

            return Response(content=decrypted_data, media_type="image/jpeg")

        return FileResponse(file_path)
    finally:
        db.close()
