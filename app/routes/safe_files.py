"""Routes for accessing files in safes - end-to-end encrypted."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, Response

from ..database import (
    get_safe, is_safe_unlocked_for_user, get_safe_session,
    get_photo_by_id, can_access_photo, get_folder_safe_id,
    get_db, update_photo_thumbnail_dimensions
)
from ..dependencies import require_user
from ..config import UPLOADS_DIR, THUMBNAILS_DIR

router = APIRouter(prefix="/api/safe-files", tags=["safe-files"])


@router.get("/photos/{photo_id}/key")
def get_safe_photo_key(photo_id: str, request: Request):
    """Get encrypted content key for a photo in a safe.
    
    The client must have an active safe session to retrieve the key.
    """
    user = require_user(request)
    
    # Check photo access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if photo is in a safe
    if not photo.get("safe_id"):
        raise HTTPException(status_code=400, detail="Photo is not in a safe")
    
    safe_id = photo["safe_id"]
    safe = get_safe(safe_id)
    
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if safe is unlocked (has active session)
    if not is_safe_unlocked_for_user(safe_id, user["id"]):
        raise HTTPException(status_code=403, detail="Safe is locked")
    
    # Get the session
    db = get_db()
    session = db.execute("""
        SELECT * FROM safe_sessions 
        WHERE safe_id = ? AND user_id = ? AND expires_at > datetime('now')
        ORDER BY created_at DESC
        LIMIT 1
    """, (safe_id, user["id"])).fetchone()
    
    if not session:
        raise HTTPException(status_code=403, detail="Safe session expired")
    
    import base64
    
    # Return the encrypted content key and session data
    # Note: In a full implementation, we'd have a separate content key for each file
    # For now, the client uses the safe DEK directly for files in the safe
    return {
        "photo_id": photo_id,
        "safe_id": safe_id,
        "session_id": session["id"],
        "encrypted_dek": base64.b64encode(session["encrypted_dek"]).decode(),
        "storage_mode": "safe_e2e"
    }


@router.get("/photos/{photo_id}/file")
def get_safe_photo_file(photo_id: str, request: Request):
    """Get encrypted photo file from a safe.
    
    Returns the file as-is (encrypted), client must decrypt.
    """
    user = require_user(request)
    
    # Check photo access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if photo is in a safe
    if not photo.get("safe_id"):
        # Not in safe - redirect to regular endpoint
        raise HTTPException(status_code=400, detail="Use regular /uploads endpoint")
    
    safe_id = photo["safe_id"]
    safe = get_safe(safe_id)
    
    if not safe or safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Return encrypted file as-is
    from pathlib import Path
    file_path = UPLOADS_DIR / photo["filename"]
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return file with header indicating it's encrypted
    return FileResponse(
        file_path,
        headers={
            "X-Encryption": "e2e",
            "X-Safe-Id": safe_id
        }
    )


@router.get("/photos/{photo_id}/thumbnail")
def get_safe_photo_thumbnail(photo_id: str, request: Request):
    """Get encrypted thumbnail from a safe.
    
    Returns the thumbnail as-is (encrypted), client must decrypt.
    If thumbnail is missing and safe is unlocked, returns 202 with X-Regenerate-Thumbnail header
    to signal the client that it should regenerate and upload the thumbnail.
    """
    user = require_user(request)
    
    # Check photo access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if photo is in a safe
    if not photo.get("safe_id"):
        raise HTTPException(status_code=400, detail="Use regular /thumbnails endpoint")
    
    safe_id = photo["safe_id"]
    safe = get_safe(safe_id)
    
    if not safe or safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if thumbnail exists
    from pathlib import Path
    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
    
    if not thumb_path.exists():
        # Thumbnail missing - check if safe is unlocked (client can regenerate)
        # Check if safe is unlocked for this user
        safe_unlocked = is_safe_unlocked_for_user(safe_id, user["id"])
        
        if safe_unlocked:
            # Safe is unlocked, client can regenerate the thumbnail
            # Return 202 Accepted with special header to trigger client-side regeneration
            return Response(
                status_code=202,
                headers={
                    "X-Regenerate-Thumbnail": "true",
                    "X-Photo-Id": photo_id,
                    "X-Safe-Id": safe_id,
                    "X-Original-Endpoint": f"/api/safe-files/photos/{photo_id}/file"
                }
            )
        else:
            # Safe is locked, client cannot regenerate
            raise HTTPException(status_code=404, detail="Thumbnail not found and safe is locked")
    
    # Thumbnail exists - return it
    return FileResponse(
        thumb_path,
        headers={
            "X-Encryption": "e2e",
            "X-Safe-Id": safe_id
        }
    )


@router.post("/photos/{photo_id}/thumbnail")
async def upload_safe_photo_thumbnail(photo_id: str, request: Request):
    """Upload a thumbnail for a photo in a safe.
    
    This is used for client-side thumbnail regeneration when the thumbnail
    is missing on the server but the client has the safe unlocked and can
    regenerate it from the original file.
    
    The thumbnail must be encrypted with the safe's DEK (same as the main file).
    """
    from fastapi import UploadFile
    
    user = require_user(request)
    
    # Check photo access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if photo is in a safe
    if not photo.get("safe_id"):
        raise HTTPException(status_code=400, detail="Photo is not in a safe")
    
    safe_id = photo["safe_id"]
    safe = get_safe(safe_id)
    
    if not safe or safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if safe is unlocked
    if not is_safe_unlocked_for_user(safe_id, user["id"]):
        raise HTTPException(status_code=403, detail="Safe is locked. Please unlock first.")
    
    # Get the uploaded thumbnail
    content_type = request.headers.get("content-type", "")
    
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="Expected multipart/form-data")
    
    # Parse form data manually
    from starlette.requests import Request as StarletteRequest
    
    # Get form data
    form = await request.form()
    thumbnail: UploadFile = form.get("thumbnail")
    thumb_width = int(form.get("thumb_width", 0))
    thumb_height = int(form.get("thumb_height", 0))
    
    if not thumbnail:
        raise HTTPException(status_code=400, detail="thumbnail is required")
    
    # Save the encrypted thumbnail
    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
    thumb_content = await thumbnail.read()
    
    with open(thumb_path, "wb") as f:
        f.write(thumb_content)
    
    # Update thumbnail dimensions in database
    update_photo_thumbnail_dimensions(photo_id, thumb_width, thumb_height)
    
    return {
        "success": True,
        "message": "Thumbnail uploaded successfully",
        "photo_id": photo_id
    }
