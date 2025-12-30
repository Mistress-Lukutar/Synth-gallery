"""Gallery routes - main page, uploads, photo/album views."""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import BASE_DIR, UPLOADS_DIR, THUMBNAILS_DIR, ALLOWED_MEDIA_TYPES
from ..database import (
    get_db, get_folder, get_folder_tree, get_folder_breadcrumbs,
    get_user_default_folder, can_access_folder, can_access_photo,
    can_access_album, can_edit_folder, can_delete_photo, can_delete_album
)
from ..dependencies import get_current_user, require_user, get_csrf_token
from ..services.media import create_thumbnail, create_video_thumbnail, get_media_type

router = APIRouter()
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@router.get("/")
def gallery(request: Request, folder_id: str = None):
    """Main page - gallery with folders, albums and photos."""
    db = get_db()
    user = get_current_user(request)

    if not user:
        return RedirectResponse(url="/login", status_code=302)

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
            breadcrumbs = get_folder_breadcrumbs(folder_id)
    else:
        # Redirect to default folder
        default_folder_id = get_user_default_folder(user["id"])
        return RedirectResponse(url=f"/?folder_id={default_folder_id}", status_code=302)

    # Get subfolders of current folder with photo count
    subfolders = db.execute("""
        SELECT f.*,
               (SELECT COUNT(*) FROM photos p WHERE p.folder_id = f.id) as photo_count
        FROM folders f
        WHERE f.parent_id = ? AND (
            f.user_id = ?
            OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
        )
        ORDER BY f.name
    """, (folder_id, user["id"], user["id"])).fetchall()

    # Get albums in current folder
    albums = db.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
               (SELECT id FROM photos WHERE album_id = a.id ORDER BY position LIMIT 1) as cover_photo_id
        FROM albums a
        WHERE a.folder_id = ?
        ORDER BY a.created_at DESC
    """, (folder_id,)).fetchall()

    # Get standalone photos in current folder
    photos = db.execute("""
        SELECT * FROM photos
        WHERE folder_id = ? AND album_id IS NULL
        ORDER BY uploaded_at DESC
    """, (folder_id,)).fetchall()

    # Create a combined list with type markers for ordering
    items = []

    # Add subfolders first
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

    for album in albums:
        items.append({
            "type": "album",
            "id": album["id"],
            "name": album["name"],
            "created_at": album["created_at"],
            "photo_count": album["photo_count"],
            "cover_photo_id": album["cover_photo_id"]
        })

    for photo in photos:
        items.append({
            "type": "photo",
            "id": photo["id"],
            "filename": photo["filename"],
            "original_name": photo["original_name"],
            "uploaded_at": photo["uploaded_at"],
            "media_type": photo["media_type"] or "image"
        })

    return templates.TemplateResponse(
        "gallery.html",
        {
            "request": request,
            "items": items,
            "photos": photos,
            "albums": albums,
            "user": user,
            "current_folder": current_folder,
            "folder_id": folder_id,
            "breadcrumbs": breadcrumbs,
            "folder_tree": folder_tree,
            "csrf_token": get_csrf_token(request)
        }
    )


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

    return FileResponse(file_path)


@router.get("/thumbnails/{filename}")
def get_thumbnail(request: Request, filename: str):
    """Serves thumbnail (protected by auth + folder access)."""
    user = require_user(request)

    # Validate path to prevent directory traversal
    file_path = (THUMBNAILS_DIR / filename).resolve()
    if not file_path.is_relative_to(THUMBNAILS_DIR):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404)

    # Get photo_id from filename (remove .jpg extension)
    photo_id = Path(filename).stem
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(file_path)


@router.post("/upload")
async def upload_photo(request: Request, file: UploadFile = None, folder_id: str = Form(None)):
    """Upload new photo or video to specified folder."""
    user = require_user(request)

    if not file:
        raise HTTPException(status_code=400, detail="file is required")

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder edit access (owner or editor)
    if not can_edit_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    # Check file type
    if file.content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="Images and videos only (jpg, png, gif, webp, mp4, webm)")

    media_type = get_media_type(file.content_type)

    # Generate unique name
    photo_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix.lower() or (".mp4" if media_type == "video" else ".jpg")
    filename = f"{photo_id}{ext}"

    # Save original
    file_path = UPLOADS_DIR / filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Create thumbnail
    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
    try:
        if media_type == "video":
            create_video_thumbnail(file_path, thumb_path)
        else:
            create_thumbnail(file_path, thumb_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Processing error: {e}")

    # Save to database with folder and user
    db = get_db()
    db.execute(
        "INSERT INTO photos (id, filename, original_name, media_type, folder_id, user_id) VALUES (?, ?, ?, ?, ?, ?)",
        (photo_id, filename, file.filename, media_type, folder_id, user["id"])
    )
    db.commit()

    return {"id": photo_id, "filename": filename, "media_type": media_type}


@router.post("/upload-album")
async def upload_album(request: Request, files: list[UploadFile], folder_id: str = Form(None)):
    """Upload multiple photos/videos as an album to specified folder."""
    user = require_user(request)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder edit access (owner or editor)
    if not can_edit_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Album requires at least 2 items")

    # Create album with folder and user
    album_id = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO albums (id, folder_id, user_id) VALUES (?, ?, ?)", (album_id, folder_id, user["id"]))

    uploaded_photos = []
    for position, file in enumerate(files):
        if file.content_type not in ALLOWED_MEDIA_TYPES:
            continue

        media_type = get_media_type(file.content_type)
        photo_id = str(uuid.uuid4())
        ext = Path(file.filename).suffix.lower() or (".mp4" if media_type == "video" else ".jpg")
        filename = f"{photo_id}{ext}"

        # Save original
        file_path = UPLOADS_DIR / filename
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Create thumbnail
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        try:
            if media_type == "video":
                create_video_thumbnail(file_path, thumb_path)
            else:
                create_thumbnail(file_path, thumb_path)
        except Exception:
            file_path.unlink(missing_ok=True)
            continue

        # Save to database with album, folder and user reference
        db.execute(
            "INSERT INTO photos (id, filename, original_name, album_id, position, media_type, folder_id, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (photo_id, filename, file.filename, album_id, position, media_type, folder_id, user["id"])
        )
        uploaded_photos.append({"id": photo_id, "filename": filename, "media_type": media_type})

    db.commit()

    if not uploaded_photos:
        # No media was successfully uploaded, delete the album
        db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        db.commit()
        raise HTTPException(status_code=400, detail="No valid media files were uploaded")

    return {"album_id": album_id, "photos": uploaded_photos}


@router.get("/api/photos/{photo_id}")
def get_photo_data(photo_id: str, request: Request):
    """Get photo data for lightbox viewer."""
    user = require_user(request)

    db = get_db()
    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Check access
    if not can_access_photo(photo_id, user["id"]):
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
                "photo_ids": photo_ids
            }

    return {
        "id": photo["id"],
        "filename": photo["filename"],
        "original_name": photo["original_name"],
        "media_type": photo["media_type"] or "image",
        "uploaded_at": photo["uploaded_at"],
        "tags": [{"id": t["id"], "tag": t["tag"], "color": t["color"] or "#6b7280"} for t in tags],
        "album": album_info
    }


@router.get("/api/albums/{album_id}")
def get_album_data(album_id: str, request: Request):
    """Get album data with photo list."""
    user = require_user(request)

    db = get_db()
    album = db.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Check access
    if not can_access_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    # Get photos in album
    photos = db.execute("""
        SELECT id, filename, original_name, media_type
        FROM photos
        WHERE album_id = ?
        ORDER BY position, id
    """, (album_id,)).fetchall()

    return {
        "id": album["id"],
        "name": album["name"],
        "created_at": album["created_at"],
        "photos": [{"id": p["id"], "filename": p["filename"], "media_type": p["media_type"] or "image"} for p in photos]
    }


from pydantic import BaseModel


class BatchDeleteInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


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
