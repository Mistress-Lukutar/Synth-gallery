"""Routes for accessing files in safes - end-to-end encrypted."""
from fastapi import APIRouter, Request, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from ..database import get_db
from ..infrastructure.repositories import SafeRepository, PhotoRepository, PermissionRepository
from ..application.services import SafeFileService
from ..dependencies import require_user
from ..config import UPLOADS_DIR, THUMBNAILS_DIR

router = APIRouter(prefix="/api/safe-files", tags=["safe-files"])


def get_safe_file_service() -> SafeFileService:
    """Get configured SafeFileService instance."""
    db = get_db()
    safe_repo = SafeRepository(db)
    photo_repo = PhotoRepository(db)
    return SafeFileService(safe_repo, photo_repo)


@router.get("/photos/{photo_id}/key")
def get_safe_photo_key(photo_id: str, request: Request):
    """Get encrypted content key for a photo in a safe.
    
    The client must have an active safe session to retrieve the key.
    """
    user = require_user(request)
    service = get_safe_file_service()
    
    db = get_db()
    try:
        perm_repo = PermissionRepository(db)
        return service.get_photo_key(
            photo_id=photo_id,
            user_id=user["id"],
            can_access_photo_fn=lambda pid, uid: perm_repo.can_access_photo(pid, uid)
        )
    finally:
        db.close()


@router.get("/photos/{photo_id}/file")
def get_safe_photo_file(photo_id: str, request: Request):
    """Get encrypted photo file from a safe.
    
    Returns the file as-is (encrypted), client must decrypt.
    """
    user = require_user(request)
    service = get_safe_file_service()
    
    db = get_db()
    try:
        perm_repo = PermissionRepository(db)
        file_path = service.get_photo_file_path(
            photo_id=photo_id,
            user_id=user["id"],
            can_access_photo_fn=lambda pid, uid: perm_repo.can_access_photo(pid, uid)
        )
        
        # Get safe_id for header
        photo_repo = PhotoRepository(db)
        safe_id = photo_repo.get_by_id(photo_id).get("safe_id", "")
        
        # Return file with header indicating it's encrypted
        return FileResponse(
            file_path,
            headers={
                "X-Encryption": "e2e",
                "X-Safe-Id": safe_id
            }
        )
    finally:
        db.close()


@router.get("/photos/{photo_id}/thumbnail")
def get_safe_photo_thumbnail(photo_id: str, request: Request):
    """Get encrypted thumbnail from a safe.
    
    Returns the thumbnail as-is (encrypted), client must decrypt.
    If thumbnail is missing and safe is unlocked, returns 202 with X-Regenerate-Thumbnail header
    to signal the client that it should regenerate and upload the thumbnail.
    """
    user = require_user(request)
    service = get_safe_file_service()
    
    db = get_db()
    try:
        perm_repo = PermissionRepository(db)
        result = service.get_photo_thumbnail_path(
            photo_id=photo_id,
            user_id=user["id"],
            can_access_photo_fn=lambda pid, uid: perm_repo.can_access_photo(pid, uid)
        )
        
        if not result["exists"]:
            # Return 202 Accepted with special header to trigger client-side regeneration
            return Response(
                status_code=202,
                headers={
                    "X-Regenerate-Thumbnail": "true",
                    "X-Photo-Id": result["photo_id"],
                    "X-Safe-Id": result["safe_id"],
                    "X-Original-Endpoint": result["original_endpoint"]
                }
            )
        
        # Thumbnail exists - return it
        return FileResponse(
            result["path"],
            headers={
                "X-Encryption": "e2e",
                "X-Safe-Id": result["safe_id"]
            }
        )
    finally:
        db.close()


@router.post("/photos/{photo_id}/thumbnail")
async def upload_safe_photo_thumbnail(photo_id: str, request: Request):
    """Upload a thumbnail for a photo in a safe.
    
    This is used for client-side thumbnail regeneration when the thumbnail
    is missing on the server but the client has the safe unlocked and can
    regenerate it from the original file.
    
    The thumbnail must be encrypted with the safe's DEK (same as the main file).
    """
    user = require_user(request)
    service = get_safe_file_service()
    
    # Get form data
    form = await request.form()
    thumbnail: UploadFile = form.get("thumbnail")
    thumb_width = int(form.get("thumb_width", 0))
    thumb_height = int(form.get("thumb_height", 0))
    
    if not thumbnail:
        raise HTTPException(status_code=400, detail="thumbnail is required")
    
    # Read thumbnail content
    thumb_content = await thumbnail.read()
    
    db = get_db()
    try:
        perm_repo = PermissionRepository(db)
        photo_repo = PhotoRepository(db)
        
        return service.upload_thumbnail(
            photo_id=photo_id,
            user_id=user["id"],
            thumbnail_content=thumb_content,
            thumb_width=thumb_width,
            thumb_height=thumb_height,
            can_access_photo_fn=lambda pid, uid: perm_repo.can_access_photo(pid, uid),
            update_dimensions_fn=lambda pid, w, h: photo_repo.update_thumbnail_dimensions(pid, w, h)
        )
    finally:
        db.close()
