"""Envelope Encryption API routes.

These endpoints support client-side encryption by managing:
- Content Keys (CK) for photos
- Folder keys for shared folders
- User public keys for key exchange
"""
import json
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from ..database import (
    get_db,
    create_photo_key, get_photo_key, set_photo_shared_key, 
    get_photo_shared_key, remove_photo_shared_key, get_photo_shared_users,
    set_photo_storage_mode, get_photo_storage_mode,
    create_folder_key, get_folder_key, update_folder_key,
    set_user_public_key, get_user_public_key,
    get_photos_needing_migration, get_migration_status,
    can_access_photo, can_delete_photo, can_access_folder, can_edit_folder,
    get_photo_by_id, get_folder
)
from ..services.envelope_encryption import EnvelopeEncryptionService
from ..dependencies import get_csrf_token, require_user, get_current_user

router = APIRouter(prefix="/api/envelope", tags=["envelope"])

# =============================================================================
# Request/Response Models
# =============================================================================

class PublicKeyUpload(BaseModel):
    public_key: str  # base64 encoded


class PhotoKeyUpload(BaseModel):
    encrypted_ck: str  # base64 encoded
    thumbnail_encrypted_ck: str | None = None  # base64 encoded


class SharePhotoKey(BaseModel):
    user_id: int
    encrypted_ck_for_user: str  # base64 encoded - CK encrypted for target user


class FolderKeyCreate(BaseModel):
    encrypted_folder_dek: str  # base64 encoded JSON: {user_id: encrypted_dek}


class FolderKeyShare(BaseModel):
    user_id: int
    encrypted_folder_dek_for_user: str  # base64 encoded


class MigrationBatch(BaseModel):
    photo_keys: list[dict]  # [{photo_id, encrypted_ck, thumbnail_encrypted_ck}, ...]


# =============================================================================
# User Public Key Management
# =============================================================================

@router.get("/my-public-key")
def get_my_public_key(request: Request):
    """Get current user's public key."""
    user = require_user(request)
    public_key = get_user_public_key(user["id"])
    
    if not public_key:
        return JSONResponse(
            {"has_key": False, "message": "No public key set. Upload one to enable sharing."}
        )
    
    import base64
    return {
        "has_key": True,
        "public_key": base64.b64encode(public_key).decode(),
        "key_version": 1
    }


@router.post("/my-public-key")
def upload_public_key(request: Request, data: PublicKeyUpload):
    """Upload or update user's public key for shared key exchange."""
    user = require_user(request)
    
    import base64
    try:
        public_key = base64.b64decode(data.public_key)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding")
    
    if len(public_key) < 32:  # Minimum reasonable key size
        raise HTTPException(status_code=400, detail="Public key too short")
    
    success = set_user_public_key(user["id"], public_key)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to store public key")
    
    return {"success": True, "message": "Public key updated"}


@router.get("/my-encrypted-dek")
def get_my_encrypted_dek(request: Request):
    """Get user's encrypted DEK and salt for client-side decryption.
    
    This endpoint returns the encrypted DEK that the client must decrypt
    using the user's password (PBKDF2).
    """
    user = require_user(request)
    
    from ..database import get_user_encryption_keys
    import base64
    
    enc_keys = get_user_encryption_keys(user["id"])
    
    if not enc_keys:
        # New user - no encryption keys yet
        return {
            "has_encryption": False,
            "needs_setup": True,
            "message": "Encryption not initialized. Initialize to create new keys."
        }
    
    return {
        "has_encryption": True,
        "encrypted_dek": base64.b64encode(enc_keys["encrypted_dek"]).decode(),
        "salt": base64.b64encode(enc_keys["dek_salt"]).decode(),
        "version": enc_keys.get("encryption_version", 1)
    }


@router.get("/users/{user_id}/public-key")
def get_user_public_key_endpoint(user_id: int, request: Request):
    """Get another user's public key (for sharing)."""
    # Require authentication but not necessarily access to specific resources
    require_user(request)
    
    public_key = get_user_public_key(user_id)
    if not public_key:
        raise HTTPException(status_code=404, detail="User has no public key")
    
    import base64
    return {
        "user_id": user_id,
        "public_key": base64.b64encode(public_key).decode()
    }


# =============================================================================
# Photo Content Key (CK) Management
# =============================================================================

@router.get("/photos/{photo_id}/key")
def get_photo_key_endpoint(photo_id: str, request: Request):
    """Get encrypted content key for a photo.
    
    Returns the appropriate encrypted CK based on access:
    - If owner: encrypted with owner's DEK
    - If shared access: encrypted with recipient's key
    """
    user = require_user(request)
    
    # Check access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    storage_mode = photo.get("storage_mode") or "legacy"
    
    # For legacy photos, return special response
    if storage_mode == "legacy":
        return {
            "storage_mode": "legacy",
            "is_encrypted": photo.get("is_encrypted", False),
            "message": "Photo uses legacy encryption. Migrate to envelope encryption."
        }
    
    # Get envelope key
    key_data = get_photo_key(photo_id)
    if not key_data:
        raise HTTPException(status_code=404, detail="Encryption key not found")
    
    import base64
    is_owner = photo.get("user_id") == user["id"]
    
    if is_owner:
        encrypted_ck = key_data["encrypted_ck"]
        thumb_ck = key_data.get("thumbnail_encrypted_ck")
        return {
            "storage_mode": "envelope",
            "is_owner": True,
            "encrypted_ck": base64.b64encode(encrypted_ck).decode(),
            "thumbnail_encrypted_ck": base64.b64encode(thumb_ck).decode() if thumb_ck else None
        }
    else:
        # Check for shared key
        shared_ck = get_photo_shared_key(photo_id, user["id"])
        if shared_ck:
            return {
                "storage_mode": "envelope",
                "is_owner": False,
                "encrypted_ck": base64.b64encode(shared_ck).decode(),
                "thumbnail_encrypted_ck": None
            }
        else:
            raise HTTPException(status_code=403, detail="No shared key for this user")


@router.post("/photos/{photo_id}/key")
def upload_photo_key(photo_id: str, data: PhotoKeyUpload, request: Request):
    """Upload encrypted content key for a new photo.
    
    This is called after upload when using envelope encryption.
    """
    user = require_user(request)
    
    # Verify ownership
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    if photo.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can set encryption key")
    
    import base64
    try:
        encrypted_ck = base64.b64decode(data.encrypted_ck)
        thumbnail_encrypted_ck = base64.b64decode(data.thumbnail_encrypted_ck) if data.thumbnail_encrypted_ck else None
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding")
    
    # Store the key
    success = create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to store key")
    
    # Update storage mode
    set_photo_storage_mode(photo_id, "envelope")
    
    return {"success": True, "message": "Encryption key stored"}
    
    import base64
    try:
        encrypted_ck = base64.b64decode(data.encrypted_ck)
        thumbnail_encrypted_ck = base64.b64decode(data.thumbnail_encrypted_ck) if data.thumbnail_encrypted_ck else None
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding")
    
    # Store the key
    success = create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to store key")
    
    # Update storage mode
    set_photo_storage_mode(photo_id, "envelope")
    
    return {"success": True, "message": "Encryption key stored"}


@router.post("/photos/{photo_id}/share")
def share_photo_key(photo_id: str, data: SharePhotoKey, request: Request):
    """Share a photo with another user by providing them encrypted CK."""
    user = require_user(request)
    
    # Verify ownership
    photo = get_photo_by_id(photo_id)
    if not photo or photo.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can share")
    
    # Check storage mode
    if get_photo_storage_mode(photo_id) != "envelope":
        raise HTTPException(status_code=400, detail="Photo must use envelope encryption")
    
    import base64
    try:
        encrypted_ck_for_user = base64.b64decode(data.encrypted_ck_for_user)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding")
    
    # Store shared key
    success = set_photo_shared_key(photo_id, data.user_id, encrypted_ck_for_user)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to store shared key")
    
    return {"success": True, "message": f"Photo shared with user {data.user_id}"}


@router.delete("/photos/{photo_id}/share/{target_user_id}")
def revoke_photo_share(photo_id: str, target_user_id: int, request: Request):
    """Revoke shared access from a user."""
    user = require_user(request)
    
    # Verify ownership
    photo = get_photo_by_id(photo_id)
    if not photo or photo.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can revoke access")
    
    success = remove_photo_shared_key(photo_id, target_user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to revoke access")
    
    return {"success": True, "message": f"Access revoked for user {target_user_id}"}


@router.get("/photos/{photo_id}/shares")
def list_photo_shares(photo_id: str, request: Request):
    """List users who have shared access to this photo."""
    user = require_user(request)
    
    # Verify ownership
    photo = get_photo_by_id(photo_id)
    if not photo or photo.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can view shares")
    
    shared_users = get_photo_shared_users(photo_id)
    
    # Get user details
    from ..database import get_user_by_id
    shares = []
    for uid in shared_users:
        u = get_user_by_id(uid)
        if u:
            shares.append({
                "user_id": uid,
                "username": u["username"],
                "display_name": u["display_name"]
            })
    
    return {"photo_id": photo_id, "shares": shares}


# =============================================================================
# Folder Key Management for Shared Folders
# =============================================================================

@router.get("/folders/{folder_id}/key")
def get_folder_key_endpoint(folder_id: str, request: Request):
    """Get encrypted folder DEK for current user."""
    user = require_user(request)
    
    # Check access
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    folder_key = get_folder_key(folder_id)
    if not folder_key:
        raise HTTPException(status_code=404, detail="Folder key not found")
    
    import base64
    encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
    user_key_hex = encrypted_map.get(str(user["id"]))
    
    if not user_key_hex:
        raise HTTPException(status_code=403, detail="No key for this user")
    
    return {
        "folder_id": folder_id,
        "encrypted_folder_dek": user_key_hex,
        "is_owner": folder_key["created_by"] == user["id"]
    }


@router.post("/folders/{folder_id}/key")
def create_folder_key_endpoint(folder_id: str, data: FolderKeyCreate, request: Request):
    """Create folder key (called by owner when enabling folder encryption)."""
    user = require_user(request)
    
    # Verify ownership
    folder = get_folder(folder_id)
    if not folder or folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can create folder key")
    
    # Check if already exists
    existing = get_folder_key(folder_id)
    if existing:
        raise HTTPException(status_code=400, detail="Folder key already exists")
    
    import base64
    try:
        # Validate JSON structure
        dek_map = json.loads(base64.b64decode(data.encrypted_folder_dek))
        if str(user["id"]) not in dek_map:
            raise HTTPException(status_code=400, detail="Must include owner's encrypted DEK")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in encrypted_folder_dek")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid data")
    
    success = create_folder_key(folder_id, user["id"], data.encrypted_folder_dek)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to create folder key")
    
    return {"success": True, "message": "Folder key created"}


@router.post("/folders/{folder_id}/share-key")
def share_folder_key(folder_id: str, data: FolderKeyShare, request: Request):
    """Share folder DEK with a user."""
    user = require_user(request)
    
    # Verify ownership
    folder = get_folder(folder_id)
    if not folder or folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can share folder key")
    
    folder_key = get_folder_key(folder_id)
    if not folder_key:
        raise HTTPException(status_code=404, detail="Folder key not found")
    
    # Update encrypted map
    import base64
    try:
        encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
        encrypted_map[str(data.user_id)] = base64.b64decode(data.encrypted_folder_dek_for_user).hex()
        
        success = update_folder_key(folder_id, json.dumps(encrypted_map))
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update folder key")
        
        return {"success": True, "message": f"Folder key shared with user {data.user_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid data: {str(e)}")


# =============================================================================
# Migration Endpoints
# =============================================================================

@router.get("/migration/status")
def get_user_migration_status(request: Request):
    """Get migration status for current user."""
    user = require_user(request)
    status = get_migration_status(user["id"])
    return status


@router.get("/migration/pending-photos")
def get_pending_migration_photos(request: Request):
    """Get list of photos needing migration."""
    user = require_user(request)
    photos = get_photos_needing_migration(user["id"])
    return {
        "count": len(photos),
        "photos": [{"id": p["id"], "filename": p["filename"], "original_name": p["original_name"]} for p in photos]
    }


@router.post("/migration/batch")
def batch_migrate_photos(data: MigrationBatch, request: Request):
    """Batch migrate photos to envelope encryption.
    
    This is called by the client after it has:
    1. Downloaded legacy-encrypted photos
    2. Decrypted them (server provided DEK temporarily)
    3. Generated new CKs
    4. Re-encrypted photos
    5. Encrypted CKs with client's DEK
    """
    user = require_user(request)
    
    results = []
    import base64
    
    for item in data.photo_keys:
        photo_id = item.get("photo_id")
        encrypted_ck_b64 = item.get("encrypted_ck")
        thumbnail_encrypted_ck_b64 = item.get("thumbnail_encrypted_ck")
        
        if not photo_id or not encrypted_ck_b64:
            results.append({"photo_id": photo_id, "success": False, "error": "Missing data"})
            continue
        
        # Verify ownership
        photo = get_photo_by_id(photo_id)
        if not photo or photo.get("user_id") != user["id"]:
            results.append({"photo_id": photo_id, "success": False, "error": "Not owner"})
            continue
        
        try:
            encrypted_ck = base64.b64decode(encrypted_ck_b64)
            thumbnail_encrypted_ck = base64.b64decode(thumbnail_encrypted_ck_b64) if thumbnail_encrypted_ck_b64 else None
            
            # Store new key
            success = create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
            if success:
                set_photo_storage_mode(photo_id, "envelope")
                results.append({"photo_id": photo_id, "success": True})
            else:
                results.append({"photo_id": photo_id, "success": False, "error": "Database error"})
        except Exception as e:
            results.append({"photo_id": photo_id, "success": False, "error": str(e)})
    
    success_count = sum(1 for r in results if r["success"])
    return {
        "total": len(data.photo_keys),
        "successful": success_count,
        "failed": len(data.photo_keys) - success_count,
        "results": results
    }
