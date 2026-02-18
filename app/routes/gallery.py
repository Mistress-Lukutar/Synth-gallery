"""Gallery routes - main page, uploads, photo/album views."""
import shutil
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..config import UPLOADS_DIR, THUMBNAILS_DIR, ALLOWED_MEDIA_TYPES, ROOT_PATH, BASE_DIR
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH
from ..database import (
    get_db, get_folder, get_folder_tree, get_folder_breadcrumbs,
    get_user_default_folder, can_access_folder, can_access_photo,
    can_access_album, can_edit_folder, can_delete_photo, can_delete_album,
    get_folder_sort_preference, can_edit_album, set_album_cover,
    add_photos_to_album, remove_photos_from_album, reorder_album_photos,
    get_available_photos_for_album, get_album_photos, get_album,
    move_photo_to_folder, move_album_to_folder,
    move_photos_to_folder, move_albums_to_folder,
    get_photo_by_id, mark_photo_encrypted, get_user_encryption_keys,
    update_photo_thumbnail_dimensions, get_user_permission,
    get_folder_safe_id, is_safe_unlocked_for_user
)
from ..dependencies import get_current_user, require_user, get_csrf_token
from ..services.media import (
    create_thumbnail, create_video_thumbnail, get_media_type,
    create_thumbnail_bytes, create_video_thumbnail_bytes
)
from ..services.metadata import extract_taken_date
from ..services.encryption import EncryptionService, dek_cache

# Service layer imports (Issue #16)
from ..infrastructure.repositories import PhotoRepository
from ..application.services import UploadService

router = APIRouter()


# Service factory function
def get_upload_service() -> UploadService:
    """Create UploadService with repositories."""
    db = get_db()
    from ..config import UPLOADS_DIR, THUMBNAILS_DIR
    return UploadService(
        photo_repository=PhotoRepository(db),
        uploads_dir=UPLOADS_DIR,
        thumbnails_dir=THUMBNAILS_DIR
    )


@router.get("/")
def gallery(request: Request, folder_id: str = None, sort: str = None):
    """Main page - gallery with folders, albums and photos.

    Args:
        sort: Sort order for photos - "uploaded" (upload date) or "taken" (capture date)
              If not provided, uses saved user preference for this folder.
    """
    db = get_db()
    user = get_current_user(request)

    if not user:
        return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

    # Check if user has encryption but DEK is not in cache
    # This happens after server restart or when DEK cache expires
    enc_keys = get_user_encryption_keys(user["id"])
    if enc_keys and not dek_cache.get(user["id"]):
        # Redirect to login to re-enter password for DEK decryption
        return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

    # Get folder tree for sidebar
    folder_tree = get_folder_tree(user["id"])

    # If no folder specified, use user's default folder
    current_folder = None
    breadcrumbs = []

    if folder_id:
        # Check access to requested folder
        if not can_access_folder(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        current_folder = get_folder(folder_id)
        if current_folder:
            current_folder = dict(current_folder)
            # Add user's permission level for this folder
            current_folder["permission"] = get_user_permission(folder_id, user["id"])
            breadcrumbs = get_folder_breadcrumbs(folder_id)
    else:
        # Redirect to default folder
        default_folder_id = get_user_default_folder(user["id"])
        return RedirectResponse(url=f"{ROOT_PATH}/?folder_id={default_folder_id}", status_code=302)

    # Get sort preference: use URL param if provided, otherwise use saved preference
    if sort is None or sort not in ("uploaded", "taken"):
        sort = get_folder_sort_preference(user["id"], folder_id)

    # Get subfolders of current folder with photo count (including subfolders recursively)
    subfolders = db.execute("""
        SELECT f.*,
               (
                   SELECT COUNT(*) FROM photos p
                   WHERE p.folder_id IN (
                       WITH RECURSIVE subfolder_tree AS (
                           SELECT id FROM folders WHERE id = f.id
                           UNION ALL
                           SELECT child.id FROM folders child
                           JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                       )
                       SELECT id FROM subfolder_tree
                   )
               ) as photo_count
        FROM folders f
        WHERE f.parent_id = ? AND (
            f.user_id = ?
            OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
        )
        ORDER BY f.name
    """, (folder_id, user["id"], user["id"])).fetchall()

    # Get albums in current folder with appropriate sort order
    # For "taken" sort: use latest taken_at from photos in album
    # Cover photo: use explicit cover_photo_id if set, otherwise first photo by position
    if sort == "taken":
        albums = db.execute("""
            SELECT a.*,
                   (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
                   COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   ) as effective_cover_photo_id,
                   (SELECT MAX(COALESCE(taken_at, uploaded_at)) FROM photos WHERE album_id = a.id) as latest_date,
                   (SELECT thumb_width FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_width,
                   (SELECT thumb_height FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_height
            FROM albums a
            WHERE a.folder_id = ?
            ORDER BY latest_date DESC
        """, (folder_id,)).fetchall()
    else:
        albums = db.execute("""
            SELECT a.*,
                   (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
                   COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   ) as effective_cover_photo_id,
                   a.created_at as latest_date,
                   (SELECT thumb_width FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_width,
                   (SELECT thumb_height FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_height
            FROM albums a
            WHERE a.folder_id = ?
            ORDER BY a.created_at DESC
        """, (folder_id,)).fetchall()

    # Get standalone photos in current folder with appropriate sort order
    # For "taken" sort: use taken_at if available, fall back to uploaded_at
    if sort == "taken":
        photos = db.execute("""
            SELECT *, COALESCE(taken_at, uploaded_at) as sort_date FROM photos
            WHERE folder_id = ? AND album_id IS NULL
            ORDER BY sort_date DESC
        """, (folder_id,)).fetchall()
    else:
        photos = db.execute("""
            SELECT *, uploaded_at as sort_date FROM photos
            WHERE folder_id = ? AND album_id IS NULL
            ORDER BY uploaded_at DESC
        """, (folder_id,)).fetchall()

    # Create a combined list with type markers for ordering
    items = []

    # Add subfolders first (always at top, sorted by name)
    for folder in subfolders:
        items.append({
            "type": "folder",
            "id": folder["id"],
            "name": folder["name"],
            "created_at": folder["created_at"],
            "user_id": folder["user_id"],
            "is_own": folder["user_id"] == user["id"],
            "photo_count": folder["photo_count"]
        })

    # Collect albums and photos with sort_date for mixed sorting
    media_items = []

    for album in albums:
        media_items.append({
            "type": "album",
            "id": album["id"],
            "name": album["name"],
            "created_at": album["created_at"],
            "photo_count": album["photo_count"],
            "cover_photo_id": album["effective_cover_photo_id"],
            "sort_date": album["latest_date"],
            "thumb_width": album["cover_thumb_width"],
            "thumb_height": album["cover_thumb_height"]
        })

    for photo in photos:
        media_items.append({
            "type": "photo",
            "id": photo["id"],
            "filename": photo["filename"],
            "original_name": photo["original_name"],
            "uploaded_at": photo["uploaded_at"],
            "taken_at": photo["taken_at"],
            "media_type": photo["media_type"] or "image",
            "sort_date": photo["sort_date"],
            "thumb_width": photo["thumb_width"],
            "thumb_height": photo["thumb_height"],
            "safe_id": photo["safe_id"]
        })

    # Sort media items by sort_date (descending), None values go to end
    media_items.sort(key=lambda x: x["sort_date"] or "", reverse=True)
    items.extend(media_items)

    # Prepare subfolders list for separate display
    subfolders_list = [{
        "id": folder["id"],
        "name": folder["name"],
        "photo_count": folder["photo_count"],
        "user_id": folder["user_id"],
        "is_own": folder["user_id"] == user["id"]
    } for folder in subfolders]

    return templates.TemplateResponse(
        request,
        "gallery.html",
        {
            "items": items,
            "subfolders": subfolders_list,
            "photos": photos,
            "albums": albums,
            "user": user,
            "current_folder": current_folder,
            "folder_id": folder_id,
            "breadcrumbs": breadcrumbs,
            "folder_tree": folder_tree,
            "csrf_token": get_csrf_token(request),
            "sort": sort,
            "base_url": ROOT_PATH
        }
    )


@router.get("/api/folders/{folder_id}/content")
def get_folder_content_api(folder_id: str, request: Request, sort: str = None):
    """Get folder content as JSON for SPA navigation."""
    user = require_user(request)
    
    # Check access
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    db = get_db()
    
    # Get current folder info
    current_folder = get_folder(folder_id)
    if not current_folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    current_folder = dict(current_folder)
    current_folder["permission"] = get_user_permission(folder_id, user["id"])
    breadcrumbs = get_folder_breadcrumbs(folder_id)
    
    # Get sort preference: use URL param if provided, otherwise use saved preference
    if sort not in ("uploaded", "taken"):
        sort = get_folder_sort_preference(user["id"], folder_id)
    
    # Get subfolders
    subfolders = db.execute("""
        SELECT f.*,
               (
                   SELECT COUNT(*) FROM photos p
                   WHERE p.folder_id IN (
                       WITH RECURSIVE subfolder_tree AS (
                           SELECT id FROM folders WHERE id = f.id
                           UNION ALL
                           SELECT child.id FROM folders child
                           JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                       )
                       SELECT id FROM subfolder_tree
                   )
               ) as photo_count
        FROM folders f
        WHERE f.parent_id = ? AND (
            f.user_id = ?
            OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
        )
        ORDER BY f.name
    """, (folder_id, user["id"], user["id"])).fetchall()
    
    # Get albums with cover photo dimensions for masonry layout
    if sort == "taken":
        albums = db.execute("""
            SELECT a.*,
                   (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
                   COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   ) as effective_cover_photo_id,
                   (SELECT MAX(COALESCE(taken_at, uploaded_at)) FROM photos WHERE album_id = a.id) as latest_date,
                   (SELECT thumb_width FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_width,
                   (SELECT thumb_height FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_height
            FROM albums a
            WHERE a.folder_id = ?
            ORDER BY latest_date DESC
        """, (folder_id,)).fetchall()
    else:
        albums = db.execute("""
            SELECT a.*,
                   (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
                   COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   ) as effective_cover_photo_id,
                   a.created_at as latest_date,
                   (SELECT thumb_width FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_width,
                   (SELECT thumb_height FROM photos WHERE id = COALESCE(a.cover_photo_id,
                       (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
                   )) as cover_thumb_height
            FROM albums a
            WHERE a.folder_id = ?
            ORDER BY a.created_at DESC
        """, (folder_id,)).fetchall()
    
    # Get photos
    if sort == "taken":
        photos = db.execute("""
            SELECT p.*, u.username as uploaded_by
            FROM photos p
            JOIN users u ON p.user_id = u.id
            WHERE p.folder_id = ? AND p.album_id IS NULL
            ORDER BY COALESCE(p.taken_at, p.uploaded_at) DESC
        """, (folder_id,)).fetchall()
    else:
        photos = db.execute("""
            SELECT p.*, u.username as uploaded_by
            FROM photos p
            JOIN users u ON p.user_id = u.id
            WHERE p.folder_id = ? AND p.album_id IS NULL
            ORDER BY p.uploaded_at DESC
        """, (folder_id,)).fetchall()
    
    # Convert to dicts
    subfolders_list = [dict(f) for f in subfolders]
    albums_list = []
    for a in albums:
        album = dict(a)
        album["effective_cover_photo_id"] = a["effective_cover_photo_id"]
        # Add sort_date for mixed sorting with photos
        album["sort_date"] = a["latest_date"]
        albums_list.append(album)
    photos_list = []
    for p in photos:
        photo = dict(p)
        # Add sort_date for mixed sorting with albums
        if sort == "taken":
            photo["sort_date"] = p["taken_at"] or p["uploaded_at"]
        else:
            photo["sort_date"] = p["uploaded_at"]
        photos_list.append(photo)
    
    # Create mixed items list sorted by sort_date (like server-side rendering)
    # Albums and photos are mixed together and sorted by date
    mixed_items = []
    for album in albums_list:
        mixed_items.append({
            "type": "album",
            "id": album["id"],
            "sort_date": album["sort_date"],
            "data": album
        })
    for photo in photos_list:
        mixed_items.append({
            "type": "photo", 
            "id": photo["id"],
            "sort_date": photo["sort_date"],
            "data": photo
        })
    # Sort by sort_date descending (newest first)
    mixed_items.sort(key=lambda x: x["sort_date"] or "", reverse=True)
    
    return {
        "folder": {
            "id": current_folder["id"],
            "name": current_folder["name"],
            "parent_id": current_folder["parent_id"],
            "permission": current_folder["permission"],
            "user_id": current_folder["user_id"],
            "safe_id": current_folder.get("safe_id")
        },
        "breadcrumbs": [{"id": b["id"], "name": b["name"]} for b in breadcrumbs],
        "subfolders": subfolders_list,
        "albums": albums_list,
        "photos": photos_list,
        "items": mixed_items,  # Mixed and sorted items for SPA rendering
        "sort": sort
    }


@router.get("/api/user/default-folder")
def get_default_folder_api(request: Request):
    """Get user's default folder ID."""
    user = require_user(request)
    folder_id = get_user_default_folder(user["id"])
    return {"folder_id": folder_id}


@router.get("/uploads/{filename}")
def get_upload(request: Request, filename: str):
    """Serves original photo (protected by auth + folder access)."""
    user = require_user(request)

    # Validate path to prevent directory traversal
    file_path = (UPLOADS_DIR / filename).resolve()
    if not file_path.is_relative_to(UPLOADS_DIR):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404)

    # Get photo_id from filename (remove extension)
    photo_id = Path(filename).stem
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if file is in a safe (end-to-end encrypted)
    photo = get_photo_by_id(photo_id)
    if photo and photo.get("safe_id"):
        # For safe files, check if it's legacy server-encrypted or new client-encrypted
        # Legacy files were encrypted with server's DEK before client-side encryption was implemented
        owner_id = photo.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None
        
        if dek:
            # Try server-side decryption first (for legacy files)
            try:
                with open(file_path, "rb") as f:
                    encrypted_data = f.read()
                
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                
                # Success! This is a legacy server-encrypted file
                ext = Path(filename).suffix.lower()
                content_types = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                    ".mp4": "video/mp4", ".webm": "video/webm"
                }
                content_type = content_types.get(ext, "application/octet-stream")
                
                return Response(content=decrypted_data, media_type=content_type)
            except Exception:
                # Decryption failed - this is a new client-encrypted file
                pass
        
        # Return encrypted file with E2E header for client-side decryption
        return FileResponse(
            file_path,
            headers={
                "X-Encryption": "e2e",
                "X-Safe-Id": photo["safe_id"],
                "X-Photo-Id": photo_id
            }
        )
    
    # Check if file is encrypted (legacy server-side encryption)
    if photo and photo["is_encrypted"]:
        # Always use owner's DEK - file is encrypted with owner's key
        owner_id = photo.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None

        if not dek:
            raise HTTPException(status_code=403, detail="Encryption key not available. Owner must be online.")

        # Read and decrypt
        with open(file_path, "rb") as f:
            encrypted_data = f.read()

        try:
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
        except Exception:
            raise HTTPException(status_code=500, detail="Decryption failed")

        # Determine content type from extension
        ext = Path(filename).suffix.lower()
        content_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
            ".mp4": "video/mp4", ".webm": "video/webm"
        }
        content_type = content_types.get(ext, "application/octet-stream")

        return Response(content=decrypted_data, media_type=content_type)

    return FileResponse(file_path)


@router.get("/thumbnails/{filename}")
def get_thumbnail(request: Request, filename: str):
    """Serves thumbnail (protected by auth + folder access).

    If thumbnail is missing but original file exists, regenerates it on-the-fly.
    """
    user = require_user(request)

    # Validate path to prevent directory traversal
    file_path = (THUMBNAILS_DIR / filename).resolve()
    if not file_path.is_relative_to(THUMBNAILS_DIR):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Get photo_id from filename (remove .jpg extension)
    photo_id = Path(filename).stem
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    # If thumbnail missing, try to regenerate from original
    if not file_path.exists():
        from ..services.thumbnail import regenerate_thumbnail
        if not regenerate_thumbnail(photo_id, user["id"]):
            raise HTTPException(status_code=404)

    # Check if file is in a safe (end-to-end encrypted)
    photo = get_photo_by_id(photo_id)
    if photo and photo.get("safe_id"):
        # For safe files, check if it's legacy server-encrypted or new client-encrypted
        owner_id = photo.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None
        
        if dek:
            # Try server-side decryption first (for legacy files)
            try:
                with open(file_path, "rb") as f:
                    encrypted_data = f.read()
                
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
                
                # Success! This is a legacy server-encrypted thumbnail
                return Response(content=decrypted_data, media_type="image/jpeg")
            except Exception:
                # Decryption failed - this is a new client-encrypted file
                pass
        
        # Return encrypted thumbnail with E2E header for client-side decryption
        return FileResponse(
            file_path,
            headers={
                "X-Encryption": "e2e",
                "X-Safe-Id": photo["safe_id"],
                "X-Photo-Id": photo_id
            }
        )

    # Check if file is encrypted (legacy server-side encryption)
    if photo and photo["is_encrypted"]:
        # Always use owner's DEK - file is encrypted with owner's key
        owner_id = photo.get("user_id")
        dek = dek_cache.get(owner_id) if owner_id else None

        if not dek:
            raise HTTPException(status_code=403, detail="Encryption key not available. Owner must be online.")

        # Read and decrypt
        with open(file_path, "rb") as f:
            encrypted_data = f.read()

        try:
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
        except Exception:
            raise HTTPException(status_code=500, detail="Decryption failed")

        return Response(content=decrypted_data, media_type="image/jpeg")

    return FileResponse(file_path)


@router.post("/upload")
async def upload_photo(
    request: Request, 
    file: UploadFile = None, 
    folder_id: str = Form(None),
    encrypted_ck: str = Form(None),  # For client-side encrypted uploads (safes/envelope)
    thumbnail: UploadFile = None,  # Client-side encrypted thumbnail (for safes)
    thumb_width: int = Form(0),  # Thumbnail width from client
    thumb_height: int = Form(0)  # Thumbnail height from client
):
    """Upload new photo or video to specified folder."""
    user = require_user(request)

    if not file:
        raise HTTPException(status_code=400, detail="file is required")

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder edit access (owner or editor)
    if not can_edit_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    # Check if folder is in a safe
    folder_safe_id = get_folder_safe_id(folder_id)
    is_safe = False
    if folder_safe_id:
        # Verify safe is unlocked
        if not is_safe_unlocked_for_user(folder_safe_id, user["id"]):
            raise HTTPException(status_code=403, detail="Safe is locked. Please unlock first.")
        is_safe = True
        
        # For safe uploads, client must encrypt the file
        if not encrypted_ck:
            raise HTTPException(status_code=400, detail="Client-side encryption required for safe uploads. Please ensure the safe is unlocked in your browser.")

    # Get user's DEK for encryption (for regular folders)
    dek = dek_cache.get(user["id"])

    # Use UploadService (Issue #16)
    service = get_upload_service()
    result = await service.upload_single(
        file=file,
        folder_id=folder_id,
        user_id=user["id"],
        user_dek=dek,
        is_safe=is_safe,
        client_thumbnail=thumbnail,
        thumb_dimensions=(thumb_width, thumb_height)
    )
    
    # Update safe_id for safe uploads
    if folder_safe_id:
        db = get_db()
        db.execute(
            "UPDATE photos SET safe_id = ? WHERE id = ?",
            (folder_safe_id, result["id"])
        )
        db.commit()
    
    return result


@router.post("/upload-album")
async def upload_album(request: Request, files: list[UploadFile], folder_id: str = Form(None)):
    """Upload multiple photos/videos as an album to specified folder."""
    user = require_user(request)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder edit access (owner or editor)
    if not can_edit_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    # Check if folder is in a safe - albums not supported in safes yet
    folder_safe_id = get_folder_safe_id(folder_id)
    if folder_safe_id:
        raise HTTPException(status_code=400, detail="Albums are not supported in safes. Please upload files individually.")

    # Get user's DEK for encryption
    dek = dek_cache.get(user["id"])

    # Use UploadService (Issue #16)
    service = get_upload_service()
    result = await service.upload_album(
        files=files,
        folder_id=folder_id,
        user_id=user["id"],
        user_dek=dek
    )
    
    return result


@router.post("/upload-bulk")
async def upload_bulk(
    request: Request,
    files: list[UploadFile],
    paths: str = Form(...),
    folder_id: str = Form(...)
):
    """Upload folder structure with files.

    Files at root level become individual photos.
    Files in subfolders become albums (one album per subfolder).

    Args:
        files: List of uploaded files
        paths: JSON array of relative paths corresponding to each file
        folder_id: Target folder ID
    """
    import json
    import tempfile

    user = require_user(request)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder edit access (owner or editor)
    if not can_edit_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    # Check if folder is in a safe - bulk upload not supported in safes yet
    folder_safe_id = get_folder_safe_id(folder_id)
    if folder_safe_id:
        raise HTTPException(status_code=400, detail="Bulk upload is not supported in safes. Please upload files individually.")

    # Parse paths JSON
    try:
        file_paths = json.loads(paths)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid paths format")

    if len(files) != len(file_paths):
        raise HTTPException(status_code=400, detail="Files and paths count mismatch")

    # Get user's DEK for encryption
    dek = dek_cache.get(user["id"])
    is_encrypted = dek is not None

    # Group files by their parent folder
    # Root level files go to '__root__'
    # Subfolder files go to their subfolder name
    from collections import defaultdict
    groups = defaultdict(list)

    for file, path in zip(files, file_paths):
        parts = path.split('/')
        if len(parts) == 1:
            # Root level file
            groups['__root__'].append((file, path))
        elif len(parts) == 2:
            # First level subfolder
            album_name = parts[0]
            groups[album_name].append((file, path))
        # else: nested deeper - skip

    db = get_db()
    summary = {
        "total_files": len(files),
        "individual_photos": 0,
        "albums_created": 0,
        "photos_in_albums": 0,
        "failed": 0,
        "skipped_nested": len(files) - sum(len(g) for g in groups.values())
    }
    albums_created = []

    # Helper function to process a single file with encryption
    async def process_file(file, original_name, album_id=None, position=0):
        if file.content_type not in ALLOWED_MEDIA_TYPES:
            return None

        media_type = get_media_type(file.content_type)
        photo_id = str(uuid.uuid4())
        ext = Path(original_name).suffix.lower() or (".mp4" if media_type == "video" else ".jpg")
        filename = f"{photo_id}{ext}"

        # Read file content
        file_content = await file.read()

        # Extract metadata from temp file
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = Path(tmp.name)

        try:
            taken_at = extract_taken_date(tmp_path)
            if taken_at is None:
                taken_at = datetime.now()
        finally:
            tmp_path.unlink(missing_ok=True)

        # Create thumbnail
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        try:
            if media_type == "video":
                thumb_bytes, thumb_w, thumb_h = create_video_thumbnail_bytes(file_content)
            else:
                thumb_bytes, thumb_w, thumb_h = create_thumbnail_bytes(file_content)
        except Exception:
            return None

        # Save files (encrypted if DEK available)
        file_path = UPLOADS_DIR / filename
        if is_encrypted:
            encrypted_content = EncryptionService.encrypt_file(file_content, dek)
            encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, dek)
            with open(file_path, "wb") as f:
                f.write(encrypted_content)
            with open(thumb_path, "wb") as f:
                f.write(encrypted_thumb)
        else:
            with open(file_path, "wb") as f:
                f.write(file_content)
            with open(thumb_path, "wb") as f:
                f.write(thumb_bytes)

        # Save to database
        if album_id:
            db.execute(
                "INSERT INTO photos (id, filename, original_name, album_id, position, media_type, folder_id, user_id, taken_at, is_encrypted, thumb_width, thumb_height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (photo_id, filename, original_name, album_id, position, media_type, folder_id, user["id"], taken_at, 1 if is_encrypted else 0, thumb_w, thumb_h)
            )
        else:
            db.execute(
                "INSERT INTO photos (id, filename, original_name, media_type, folder_id, user_id, taken_at, is_encrypted, thumb_width, thumb_height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (photo_id, filename, original_name, media_type, folder_id, user["id"], taken_at, 1 if is_encrypted else 0, thumb_w, thumb_h)
            )

        return photo_id

    # Process root level files as individual photos
    for file, path in groups.pop('__root__', []):
        original_name = Path(file.filename).name
        result = await process_file(file, original_name)
        if result:
            summary["individual_photos"] += 1
        else:
            summary["failed"] += 1

    # Process each subfolder as an album
    for album_name, album_files in groups.items():
        album_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO albums (id, name, folder_id, user_id) VALUES (?, ?, ?, ?)",
            (album_id, album_name, folder_id, user["id"])
        )

        photos_uploaded = 0
        for position, (file, path) in enumerate(album_files):
            original_name = Path(file.filename).name
            result = await process_file(file, original_name, album_id, position)
            if result:
                photos_uploaded += 1
                summary["photos_in_albums"] += 1
            else:
                summary["failed"] += 1

        if photos_uploaded > 0:
            summary["albums_created"] += 1
            albums_created.append({
                "id": album_id,
                "name": album_name,
                "photo_count": photos_uploaded
            })
        else:
            # No photos uploaded, delete empty album
            db.execute("DELETE FROM albums WHERE id = ?", (album_id,))

    db.commit()

    return {
        "status": "ok",
        "summary": summary,
        "albums": albums_created
    }


@router.get("/api/photos/{photo_id}")
def get_photo_data(photo_id: str, request: Request):
    """Get photo data for lightbox viewer."""
    user = require_user(request)
    
    print(f"[DEBUG] get_photo_data: photo_id={photo_id}, user_id={user['id']}")

    db = get_db()
    try:
        photo = db.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
    except Exception as e:
        print(f"[DEBUG] SQL error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    if not photo:
        print(f"[DEBUG] Photo not found: {photo_id}")
        raise HTTPException(status_code=404, detail="Photo not found")
    
    print(f"[DEBUG] Photo found: {photo['id']}, folder_id={photo['folder_id']}, safe_id={photo['safe_id']}")

    # Check access
    try:
        has_access = can_access_photo(photo_id, user["id"])
        print(f"[DEBUG] Access check: {has_access}")
    except Exception as e:
        print(f"[DEBUG] Access check error: {e}")
        raise HTTPException(status_code=500, detail=f"Access check error: {e}")
    
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get tags
    tags = db.execute("""
        SELECT t.id, t.tag, t.category_id, c.color
        FROM tags t
        LEFT JOIN tag_categories c ON t.category_id = c.id
        WHERE t.photo_id = ?
    """, (photo_id,)).fetchall()

    # Get album info if photo is in album
    album_info = None
    if photo["album_id"]:
        album = db.execute("SELECT * FROM albums WHERE id = ?", (photo["album_id"],)).fetchone()
        if album:
            album_photos = db.execute(
                "SELECT id FROM photos WHERE album_id = ? ORDER BY position, id",
                (photo["album_id"],)
            ).fetchall()
            photo_ids = [p["id"] for p in album_photos]
            current_index = photo_ids.index(photo_id) if photo_id in photo_ids else 0
            album_info = {
                "id": album["id"],
                "name": album["name"],
                "total": len(photo_ids),
                "current": current_index + 1,
                "photo_ids": photo_ids,
                "can_edit": can_edit_album(photo["album_id"], user["id"])
            }

    return {
        "id": photo["id"],
        "filename": photo["filename"],
        "original_name": photo["original_name"],
        "media_type": photo["media_type"] or "image",
        "uploaded_at": photo["uploaded_at"],
        "taken_at": photo["taken_at"],
        "tags": [{"id": t["id"], "tag": t["tag"], "color": t["color"] or "#6b7280"} for t in tags],
        "album": album_info,
        "safe_id": photo["safe_id"]
    }


@router.put("/api/photos/{photo_id}/dimensions")
async def update_dimensions(photo_id: str, request: Request):
    """Update thumbnail dimensions for a photo (used for legacy photos without dimensions)."""
    user = require_user(request)

    # Check access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    # Parse request body
    try:
        body = await request.json()
        width = int(body.get("width", 0))
        height = int(body.get("height", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid request body")

    # Validate dimensions
    if width < 1 or height < 1 or width > 1000 or height > 1000:
        raise HTTPException(status_code=400, detail="Invalid dimensions")

    update_photo_thumbnail_dimensions(photo_id, width, height)
    return {"status": "ok"}


@router.get("/api/albums/{album_id}")
def get_album_data(album_id: str, request: Request):
    """Get album data with photo list."""
    user = require_user(request)

    # Check access
    if not can_access_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    album = get_album(album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Get photos in album
    photos = get_album_photos(album_id)

    return {
        "id": album["id"],
        "name": album["name"],
        "created_at": album["created_at"],
        "cover_photo_id": album.get("cover_photo_id"),
        "effective_cover_photo_id": album.get("effective_cover_photo_id"),
        "can_edit": can_edit_album(album_id, user["id"]),
        "photos": [{"id": p["id"], "filename": p["filename"], "media_type": p["media_type"] or "image"} for p in photos]
    }


from pydantic import BaseModel


class BatchDeleteInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


class AlbumPhotosInput(BaseModel):
    photo_ids: list[str]


class AlbumCoverInput(BaseModel):
    photo_id: str | None = None


@router.post("/api/photos/batch-delete")
def batch_delete_photos(data: BatchDeleteInput, request: Request):
    """Delete multiple photos and albums."""
    user = require_user(request)

    db = get_db()
    deleted_photos = 0
    deleted_albums = 0
    skipped_photos = 0
    skipped_albums = 0

    # Delete individual photos
    for photo_id in data.photo_ids:
        # Check permission to delete
        if not can_delete_photo(photo_id, user["id"]):
            skipped_photos += 1
            continue

        photo = db.execute("SELECT filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if photo:
            file_path = UPLOADS_DIR / photo["filename"]
            thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
            file_path.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
            db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            deleted_photos += 1

    # Delete albums and their photos
    for album_id in data.album_ids:
        # Check permission to delete album
        if not can_delete_album(album_id, user["id"]):
            skipped_albums += 1
            continue

        photos = db.execute("SELECT id, filename FROM photos WHERE album_id = ?", (album_id,)).fetchall()
        for photo in photos:
            file_path = UPLOADS_DIR / photo["filename"]
            thumb_path = THUMBNAILS_DIR / f"{photo['id']}.jpg"
            file_path.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
        db.execute("DELETE FROM photos WHERE album_id = ?", (album_id,))
        db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        deleted_albums += 1

    db.commit()
    return {
        "status": "ok",
        "deleted_photos": deleted_photos,
        "deleted_albums": deleted_albums,
        "skipped_photos": skipped_photos,
        "skipped_albums": skipped_albums
    }


# === Album Management Endpoints ===

@router.get("/api/albums/{album_id}/available-photos")
def get_available_photos(album_id: str, request: Request):
    """Get photos from folder that can be added to album."""
    user = require_user(request)

    if not can_access_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    if not can_edit_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot edit this album")

    photos = get_available_photos_for_album(album_id)
    return {"photos": photos}


@router.post("/api/albums/{album_id}/photos")
def add_photos_to_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Add photos to album."""
    user = require_user(request)

    if not can_edit_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot edit this album")

    if not data.photo_ids:
        raise HTTPException(status_code=400, detail="No photos specified")

    added = add_photos_to_album(album_id, data.photo_ids)
    return {"status": "ok", "added": added}


@router.delete("/api/albums/{album_id}/photos")
def remove_photos_from_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Remove photos from album. Photos stay in folder."""
    user = require_user(request)

    if not can_edit_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot edit this album")

    if not data.photo_ids:
        raise HTTPException(status_code=400, detail="No photos specified")

    removed = remove_photos_from_album(album_id, data.photo_ids)
    return {"status": "ok", "removed": removed}


@router.put("/api/albums/{album_id}/reorder")
def reorder_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Reorder photos in album."""
    user = require_user(request)

    if not can_edit_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot edit this album")

    if not data.photo_ids:
        raise HTTPException(status_code=400, detail="No photos specified")

    success = reorder_album_photos(album_id, data.photo_ids)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid photo IDs")

    return {"status": "ok"}


@router.put("/api/albums/{album_id}/cover")
def set_album_cover_endpoint(album_id: str, data: AlbumCoverInput, request: Request):
    """Set album cover photo. Pass null photo_id to reset to default."""
    user = require_user(request)

    if not can_edit_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot edit this album")

    success = set_album_cover(album_id, data.photo_id)
    if not success and data.photo_id:
        raise HTTPException(status_code=400, detail="Photo not in album")

    return {"status": "ok"}


# === Move Operations ===

class MoveInput(BaseModel):
    folder_id: str


class BatchMoveInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []
    folder_id: str


@router.put("/api/photos/{photo_id}/move")
def move_photo_endpoint(photo_id: str, data: MoveInput, request: Request):
    """Move a standalone photo to another folder.

    Only works for photos not in albums.
    Requires edit permission on destination folder.
    """
    user = require_user(request)

    db = get_db()

    # Get photo info
    photo = db.execute(
        "SELECT folder_id, album_id, user_id FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if photo["album_id"]:
        raise HTTPException(status_code=400, detail="Cannot move photo in album. Move the album instead.")

    # Check source folder permission - need edit/delete rights to move
    if not can_delete_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="No permission to move this photo")

    # Check destination folder edit permission
    if not can_edit_folder(data.folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot move to this folder")

    # Check if moving to same folder
    if photo["folder_id"] == data.folder_id:
        return {"status": "ok", "message": "Photo already in this folder"}

    # Move photo
    success = move_photo_to_folder(photo_id, data.folder_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to move photo")

    return {"status": "ok"}


@router.put("/api/albums/{album_id}/move")
def move_album_endpoint(album_id: str, data: MoveInput, request: Request):
    """Move an album and all its photos to another folder.

    Requires edit permission on destination folder.
    """
    user = require_user(request)

    db = get_db()

    # Get album info
    album = db.execute(
        "SELECT folder_id, user_id FROM albums WHERE id = ?",
        (album_id,)
    ).fetchone()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Check source album permission - need edit/delete rights to move
    if not can_delete_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="No permission to move this album")

    # Check destination folder edit permission
    if not can_edit_folder(data.folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot move to this folder")

    # Check if moving to same folder
    if album["folder_id"] == data.folder_id:
        return {"status": "ok", "message": "Album already in this folder"}

    # Move album
    success = move_album_to_folder(album_id, data.folder_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to move album")

    return {"status": "ok"}


@router.put("/api/items/move")
def batch_move_items(data: BatchMoveInput, request: Request):
    """Move multiple photos and albums to another folder.

    Only moves standalone photos (not in albums).
    """
    user = require_user(request)

    # Check destination folder edit permission
    if not can_edit_folder(data.folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot move to this folder")

    db = get_db()
    moved_photos = 0
    moved_albums = 0
    skipped_photos = 0
    skipped_albums = 0

    # Move photos
    for photo_id in data.photo_ids:
        photo = db.execute(
            "SELECT folder_id, album_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()

        if not photo:
            skipped_photos += 1
            continue

        if photo["album_id"]:
            skipped_photos += 1
            continue

        # Check edit/delete permission on source - need rights to move
        if not can_delete_photo(photo_id, user["id"]):
            skipped_photos += 1
            continue

        # Skip if already in target folder
        if photo["folder_id"] == data.folder_id:
            continue

        if move_photo_to_folder(photo_id, data.folder_id):
            moved_photos += 1
        else:
            skipped_photos += 1

    # Move albums
    for album_id in data.album_ids:
        album = db.execute(
            "SELECT folder_id FROM albums WHERE id = ?",
            (album_id,)
        ).fetchone()

        if not album:
            skipped_albums += 1
            continue

        # Check edit/delete permission on source album - need rights to move
        if not can_delete_album(album_id, user["id"]):
            skipped_albums += 1
            continue

        # Skip if already in target folder
        if album["folder_id"] == data.folder_id:
            continue

        if move_album_to_folder(album_id, data.folder_id):
            moved_albums += 1
        else:
            skipped_albums += 1

    return {
        "status": "ok",
        "moved_photos": moved_photos,
        "moved_albums": moved_albums,
        "skipped_photos": skipped_photos,
        "skipped_albums": skipped_albums
    }


def _copy_and_reencrypt_file(
    old_path: Path,
    new_path: Path,
    is_encrypted: bool,
    source_owner_id: int,
    dest_owner_id: int
) -> bool:
    """Copy file, re-encrypting if needed when owner changes.

    Returns True if successful, False if failed (e.g., DEK not available).
    """
    if not old_path.exists():
        return False

    # If not encrypted or same owner - just copy
    if not is_encrypted or source_owner_id == dest_owner_id:
        shutil.copy2(old_path, new_path)
        return True

    # Need to re-encrypt: decrypt with source DEK, encrypt with dest DEK
    source_dek = dek_cache.get(source_owner_id)
    dest_dek = dek_cache.get(dest_owner_id)

    if not source_dek or not dest_dek:
        # Cannot re-encrypt without both DEKs
        return False

    # Read and decrypt
    encrypted_data = old_path.read_bytes()
    try:
        plaintext = EncryptionService.decrypt_file(encrypted_data, source_dek)
    except Exception:
        return False

    # Re-encrypt with destination owner's key
    new_encrypted = EncryptionService.encrypt_file(plaintext, dest_dek)
    new_path.write_bytes(new_encrypted)
    return True


@router.post("/api/items/copy")
def batch_copy_items(data: BatchMoveInput, request: Request):
    """Copy multiple photos and albums to another folder.

    Creates copies of photos with new IDs and filenames.
    Only copies standalone photos (not in albums).
    Albums are copied with all their photos.
    If copying encrypted files from another user, re-encrypts with your key.
    """
    user = require_user(request)

    # Check destination folder edit permission
    if not can_edit_folder(data.folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot copy to this folder")

    db = get_db()
    copied_photos = 0
    copied_albums = 0
    skipped_photos = 0
    skipped_albums = 0

    # Get current user's DEK for encryption
    user_dek = dek_cache.get(user["id"])

    # Copy photos
    for photo_id in data.photo_ids:
        photo = db.execute(
            """SELECT filename, original_name, media_type, album_id, taken_at,
                      is_encrypted, thumb_width, thumb_height, user_id
               FROM photos WHERE id = ?""",
            (photo_id,)
        ).fetchone()

        if not photo:
            skipped_photos += 1
            continue

        # Skip photos in albums - they will be copied with the album
        if photo["album_id"]:
            skipped_photos += 1
            continue

        # Check access to source
        if not can_access_photo(photo_id, user["id"]):
            skipped_photos += 1
            continue

        source_owner_id = photo["user_id"]
        is_encrypted = photo["is_encrypted"]

        # If encrypted and different owner, check if we can re-encrypt
        if is_encrypted and source_owner_id != user["id"]:
            source_dek = dek_cache.get(source_owner_id) if source_owner_id else None
            if not source_dek or not user_dek:
                # Cannot copy encrypted file without DEKs
                skipped_photos += 1
                continue

        # Create new photo copy
        new_photo_id = str(uuid.uuid4())
        old_filename = photo["filename"]
        ext = Path(old_filename).suffix
        new_filename = f"{new_photo_id}{ext}"

        # Copy files with re-encryption if needed
        old_upload = UPLOADS_DIR / old_filename
        new_upload = UPLOADS_DIR / new_filename
        old_thumb = THUMBNAILS_DIR / f"{Path(old_filename).stem}.jpg"
        new_thumb = THUMBNAILS_DIR / f"{new_photo_id}.jpg"

        try:
            # Copy main file (re-encrypt if needed)
            if not _copy_and_reencrypt_file(
                old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
            ):
                skipped_photos += 1
                continue

            # Copy thumbnail (also encrypted if main file is)
            if old_thumb.exists():
                _copy_and_reencrypt_file(
                    old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                )

            # Insert new photo record (now owned by current user, still encrypted)
            db.execute(
                """INSERT INTO photos (id, filename, original_name, media_type, folder_id, user_id,
                                       taken_at, is_encrypted, thumb_width, thumb_height)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_photo_id, new_filename, photo["original_name"], photo["media_type"],
                 data.folder_id, user["id"], photo["taken_at"], is_encrypted,
                 photo["thumb_width"], photo["thumb_height"])
            )

            # Copy tags
            tags = db.execute(
                "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                (photo_id,)
            ).fetchall()
            for tag in tags:
                db.execute(
                    "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                    (new_photo_id, tag["tag"], tag["category_id"], tag["confidence"])
                )

            copied_photos += 1
        except Exception:
            # Clean up on failure
            if new_upload.exists():
                new_upload.unlink()
            if new_thumb.exists():
                new_thumb.unlink()
            skipped_photos += 1
            continue

    # Copy albums
    for album_id in data.album_ids:
        album = db.execute(
            "SELECT name, cover_photo_id, user_id FROM albums WHERE id = ?",
            (album_id,)
        ).fetchone()

        if not album:
            skipped_albums += 1
            continue

        # Check access to source album
        if not can_access_album(album_id, user["id"]):
            skipped_albums += 1
            continue

        # Get album photos with user_id
        album_photos = db.execute(
            """SELECT id, filename, original_name, media_type, position, taken_at,
                      is_encrypted, thumb_width, thumb_height, user_id
               FROM photos WHERE album_id = ? ORDER BY position""",
            (album_id,)
        ).fetchall()

        if not album_photos:
            skipped_albums += 1
            continue

        # Check if any encrypted photos need re-encryption and DEK is missing
        album_source_owner = album["user_id"]
        has_encrypted = any(p["is_encrypted"] for p in album_photos)
        if has_encrypted and album_source_owner != user["id"]:
            source_dek = dek_cache.get(album_source_owner) if album_source_owner else None
            if not source_dek or not user_dek:
                # Cannot copy album with encrypted files without DEKs
                skipped_albums += 1
                continue

        # Create new album
        new_album_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO albums (id, name, folder_id, user_id) VALUES (?, ?, ?, ?)",
            (new_album_id, album["name"], data.folder_id, user["id"])
        )

        # Copy album photos
        new_cover_id = None
        for photo in album_photos:
            new_photo_id = str(uuid.uuid4())
            old_filename = photo["filename"]
            ext = Path(old_filename).suffix
            new_filename = f"{new_photo_id}{ext}"

            old_upload = UPLOADS_DIR / old_filename
            new_upload = UPLOADS_DIR / new_filename
            old_thumb = THUMBNAILS_DIR / f"{Path(old_filename).stem}.jpg"
            new_thumb = THUMBNAILS_DIR / f"{new_photo_id}.jpg"

            source_owner_id = photo["user_id"]
            is_encrypted = photo["is_encrypted"]

            try:
                # Copy main file (re-encrypt if needed)
                if not _copy_and_reencrypt_file(
                    old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
                ):
                    continue

                # Copy thumbnail
                if old_thumb.exists():
                    _copy_and_reencrypt_file(
                        old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                    )

                db.execute(
                    """INSERT INTO photos (id, filename, original_name, album_id, position,
                                           media_type, folder_id, user_id, taken_at, is_encrypted,
                                           thumb_width, thumb_height)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (new_photo_id, new_filename, photo["original_name"], new_album_id,
                     photo["position"], photo["media_type"], data.folder_id, user["id"],
                     photo["taken_at"], is_encrypted, photo["thumb_width"],
                     photo["thumb_height"])
                )

                # Copy tags
                tags = db.execute(
                    "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                    (photo["id"],)
                ).fetchall()
                for tag in tags:
                    db.execute(
                        "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                        (new_photo_id, tag["tag"], tag["category_id"], tag["confidence"])
                    )

                # Track new cover photo
                if photo["id"] == album["cover_photo_id"]:
                    new_cover_id = new_photo_id

            except Exception:
                continue

        # Set album cover
        if new_cover_id:
            db.execute(
                "UPDATE albums SET cover_photo_id = ? WHERE id = ?",
                (new_cover_id, new_album_id)
            )

        copied_albums += 1

    db.commit()

    return {
        "status": "ok",
        "copied_photos": copied_photos,
        "copied_albums": copied_albums,
        "skipped_photos": skipped_photos,
        "skipped_albums": skipped_albums
    }


# === Download Operations ===

class BatchDownloadInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


@router.post("/api/photos/batch-download")
async def batch_download(data: BatchDownloadInput, request: Request):
    """Download multiple photos and albums as a ZIP file.

    If only one photo is selected, downloads it directly.
    If multiple photos/albums are selected, creates a ZIP with:
    - Root folder named with current date (YYYY-MM-DD)
    - Individual photos in root
    - Albums in subfolders named by album name
    """
    user = require_user(request)
    db = get_db()

    # Collect all files to download
    files_to_download = []  # List of (archive_path, file_path, is_encrypted, owner_id)

    # Get current date for folder name
    date_folder = datetime.now().strftime("%Y-%m-%d")

    # Process individual photos
    for photo_id in data.photo_ids:
        if not can_access_photo(photo_id, user["id"]):
            continue

        photo = db.execute(
            "SELECT id, filename, original_name, is_encrypted, user_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()

        if photo:
            file_path = UPLOADS_DIR / photo["filename"]
            if file_path.exists():
                archive_path = f"{date_folder}/{photo['original_name']}"
                files_to_download.append((
                    archive_path,
                    file_path,
                    photo["is_encrypted"],
                    photo["user_id"]
                ))

    # Process albums
    for album_id in data.album_ids:
        if not can_access_album(album_id, user["id"]):
            continue

        album = db.execute(
            "SELECT id, name FROM albums WHERE id = ?",
            (album_id,)
        ).fetchone()

        if album:
            album_name = album["name"] or f"album_{album_id[:8]}"
            # Sanitize album name for filesystem
            album_name = "".join(c for c in album_name if c.isalnum() or c in (' ', '-', '_')).strip()
            if not album_name:
                album_name = f"album_{album_id[:8]}"

            photos = db.execute(
                "SELECT id, filename, original_name, is_encrypted, user_id FROM photos WHERE album_id = ? ORDER BY position, id",
                (album_id,)
            ).fetchall()

            for photo in photos:
                file_path = UPLOADS_DIR / photo["filename"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{album_name}/{photo['original_name']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        photo["is_encrypted"],
                        photo["user_id"]
                    ))

    if not files_to_download:
        raise HTTPException(status_code=404, detail="No files to download")

    # If only one file, download directly
    if len(files_to_download) == 1:
        archive_path, file_path, is_encrypted, owner_id = files_to_download[0]
        original_name = Path(archive_path).name

        if is_encrypted:
            dek = dek_cache.get(owner_id)
            if not dek:
                raise HTTPException(status_code=403, detail="Encryption key not available")

            with open(file_path, "rb") as f:
                encrypted_data = f.read()

            try:
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)
            except Exception:
                raise HTTPException(status_code=500, detail="Decryption failed")

            # Determine content type
            ext = file_path.suffix.lower()
            content_types = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                ".mp4": "video/mp4", ".webm": "video/webm"
            }
            content_type = content_types.get(ext, "application/octet-stream")

            return Response(
                content=decrypted_data,
                media_type=content_type,
                headers={"Content-Disposition": f'attachment; filename="{original_name}"'}
            )
        else:
            return FileResponse(
                file_path,
                filename=original_name,
                media_type="application/octet-stream"
            )

    # Multiple files - create ZIP (no compression for speed)
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_STORED) as zf:
        # Track used names to avoid duplicates
        used_names = {}

        for archive_path, file_path, is_encrypted, owner_id in files_to_download:
            # Handle duplicate filenames
            if archive_path in used_names:
                used_names[archive_path] += 1
                path_parts = archive_path.rsplit('.', 1)
                if len(path_parts) == 2:
                    archive_path = f"{path_parts[0]}_{used_names[archive_path]}.{path_parts[1]}"
                else:
                    archive_path = f"{archive_path}_{used_names[archive_path]}"
            else:
                used_names[archive_path] = 0

            if is_encrypted:
                dek = dek_cache.get(owner_id)
                if not dek:
                    continue  # Skip files we can't decrypt

                with open(file_path, "rb") as f:
                    encrypted_data = f.read()

                try:
                    file_data = EncryptionService.decrypt_file(encrypted_data, dek)
                except Exception:
                    continue  # Skip files that fail to decrypt
            else:
                with open(file_path, "rb") as f:
                    file_data = f.read()

            zf.writestr(archive_path, file_data)

    # Get ZIP size and reset buffer
    zip_size = zip_buffer.tell()
    zip_buffer.seek(0)

    # Generate ZIP filename
    zip_filename = f"synth_gallery_{date_folder}.zip"

    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
            "Content-Length": str(zip_size)
        }
    )
