"""Routes for accessing files in safes - end-to-end encrypted."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, Response

from ..database import (
    get_safe, is_safe_unlocked_for_user, get_safe_session,
    get_photo_by_id, can_access_photo, get_folder_safe_id,
    get_db
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
    
    # Return encrypted thumbnail as-is
    from pathlib import Path
    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
    
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    return FileResponse(
        thumb_path,
        headers={
            "X-Encryption": "e2e",
            "X-Safe-Id": safe_id
        }
    )
