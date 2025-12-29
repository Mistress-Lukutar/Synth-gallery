import random
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from PIL import Image
from fastapi import FastAPI, UploadFile, Request, HTTPException, Form
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .database import (
    get_db, init_db, authenticate_user, create_session,
    get_session, delete_session, cleanup_expired_sessions,
    # Folder management
    create_folder, get_folder, update_folder, delete_folder as db_delete_folder,
    get_user_folders, get_folder_tree, get_folder_breadcrumbs,
    get_user_default_folder, set_user_default_folder, get_folder_contents,
    # Access control
    can_access_folder, can_access_photo, can_access_album
)

# Allowed media types
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm"}
ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

# Directory paths
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

# Create directories if they don't exist
UPLOADS_DIR.mkdir(exist_ok=True)
THUMBNAILS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs before the application starts accepting requests
    init_db()
    cleanup_expired_sessions()
    yield
    # Shutdown: runs when application is stopping (cleanup code goes here)


app = FastAPI(title="Photo Gallery", lifespan=lifespan)

# Static files and templates
# Note: /thumbnails is served via a route (not StaticFiles) to enforce authentication
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

# Session cookie name
SESSION_COOKIE = "synth_session"

# Paths that don't require authentication
PUBLIC_PATHS = {"/login", "/static", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication on all routes"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Check session cookie
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            session = get_session(session_id)
            if session:
                # Valid session - attach user info to request state
                request.state.user = {
                    "id": session["user_id"],
                    "username": session["username"],
                    "display_name": session["display_name"]
                }
                return await call_next(request)

        # No valid session - redirect to login
        if request.method == "GET":
            return RedirectResponse(url="/login", status_code=302)

        # For API calls, return 401
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


app.add_middleware(AuthMiddleware)


def create_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int] = (400, 400)):
    """Creates image thumbnail"""
    with Image.open(source_path) as img:
        img.thumbnail(size, Image.Resampling.LANCZOS)
        # Convert RGBA/P to RGB for JPEG (no transparency support)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(thumb_path, "JPEG", quality=85)


def create_video_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int] = (400, 400)):
    """Creates thumbnail from first frame of video"""
    cap = cv2.VideoCapture(str(source_path))
    try:
        ret, frame = cap.read()
        if not ret:
            raise ValueError("Could not read video frame")

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        img.save(thumb_path, "JPEG", quality=85)
    finally:
        cap.release()


def get_media_type(content_type: str) -> str:
    """Returns 'image' or 'video' based on content type"""
    if content_type in ALLOWED_VIDEO_TYPES:
        return "video"
    return "image"


# === Authentication Routes ===

@app.get("/login")
def login_page(request: Request, error: str = None):
    """Show login page"""
    # If already logged in, redirect to gallery
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id and get_session(session_id):
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error, "username": ""}
    )


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Process login form"""
    user = authenticate_user(username, password)

    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password", "username": username},
            status_code=401
        )

    # Create session
    session_id = create_session(user["id"])

    # Redirect to gallery with session cookie
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7  # 7 days
    )
    return response


@app.get("/logout")
def logout(request: Request):
    """Logout user"""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        delete_session(session_id)

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


def get_current_user(request: Request) -> dict | None:
    """Get current user from request state"""
    return getattr(request.state, "user", None)


# === Gallery Routes ===

@app.get("/")
def gallery(request: Request, folder_id: str = None):
    """Main page - gallery with folders, albums and photos"""
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
        WHERE f.parent_id = ? AND (f.user_id = ? OR f.access_mode = 'public')
        ORDER BY f.name
    """, (folder_id, user["id"])).fetchall()

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
            "access_mode": folder["access_mode"],
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
            "folder_tree": folder_tree
        }
    )


@app.get("/photo/{photo_id}")
def view_photo(request: Request, photo_id: str):
    """Photo view page"""
    db = get_db()
    user = get_current_user(request)

    if not user:
        return RedirectResponse(url="/login", status_code=302)

    photo = db.execute(
        "SELECT * FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Check access
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    tags = db.execute("""
                      SELECT t.id, t.tag, t.category_id, c.name as category_name, c.color
                      FROM tags t
                               LEFT JOIN tag_categories c ON t.category_id = c.id
                      WHERE t.photo_id = ?
                      """, (photo_id,)).fetchall()

    tags_data = [
        {"id": t["id"], "tag": t["tag"], "category_id": t["category_id"],
         "category": t["category_name"], "color": t["color"] or "#6b7280"}
        for t in tags
    ]

    album_info = None
    album_photos = []

    # Check if photo is part of an album
    if photo["album_id"]:
        album = db.execute(
            "SELECT * FROM albums WHERE id = ?", (photo["album_id"],)
        ).fetchone()
        album_photos = db.execute(
            "SELECT id, position FROM photos WHERE album_id = ? ORDER BY position",
            (photo["album_id"],)
        ).fetchall()
        current_index = next(
            (i for i, p in enumerate(album_photos) if p["id"] == photo_id), 0
        )
        album_info = {
            "id": album["id"],
            "name": album["name"],
            "total": len(album_photos),
            "current": current_index + 1,
            "photo_ids": [p["id"] for p in album_photos]
        }

        # Navigation within album
        prev_photo = album_photos[current_index - 1] if current_index > 0 else None
        next_photo = album_photos[current_index + 1] if current_index < len(album_photos) - 1 else None

        # If at album edges, get next/prev item outside album
        if not prev_photo:
            prev_photo = db.execute("""
                                    SELECT id, 'photo' as type
                                    FROM photos
                                    WHERE album_id IS NULL
                                      AND uploaded_at > (SELECT created_at FROM albums WHERE id = ?)
                                    ORDER BY uploaded_at ASC LIMIT 1
                                    """, (photo["album_id"],)).fetchone()
            if not prev_photo:
                prev_photo = db.execute("""
                                        SELECT a.id, 'album' as type
                                        FROM albums a
                                        WHERE a.created_at > (SELECT created_at FROM albums WHERE id = ?)
                                        ORDER BY a.created_at ASC LIMIT 1
                                        """, (photo["album_id"],)).fetchone()

        if not next_photo:
            next_photo = db.execute("""
                                    SELECT id, 'photo' as type
                                    FROM photos
                                    WHERE album_id IS NULL
                                      AND uploaded_at < (SELECT created_at FROM albums WHERE id = ?)
                                    ORDER BY uploaded_at DESC LIMIT 1
                                    """, (photo["album_id"],)).fetchone()
            if not next_photo:
                next_photo = db.execute("""
                                        SELECT a.id, 'album' as type
                                        FROM albums a
                                        WHERE a.created_at < (SELECT created_at FROM albums WHERE id = ?)
                                        ORDER BY a.created_at DESC LIMIT 1
                                        """, (photo["album_id"],)).fetchone()
    else:
        # Standalone photo navigation - need to find prev/next items (photos or albums)
        current_time = photo["uploaded_at"]

        # Find previous item (newer) - could be photo or album
        prev_photo = db.execute("""
                                SELECT id, 'photo' as type, uploaded_at as item_time
                                FROM photos
                                WHERE album_id IS NULL
                                  AND uploaded_at > ?
                                ORDER BY uploaded_at ASC LIMIT 1
                                """, (current_time,)).fetchone()

        prev_album = db.execute("""
                                SELECT id, 'album' as type, created_at as item_time
                                FROM albums
                                WHERE created_at > ?
                                ORDER BY created_at ASC LIMIT 1
                                """, (current_time,)).fetchone()

        # Pick the closest one
        if prev_photo and prev_album:
            prev_item = prev_photo if prev_photo["item_time"] < prev_album["item_time"] else prev_album
        else:
            prev_item = prev_photo or prev_album

        # Find next item (older) - could be photo or album
        next_photo = db.execute("""
                                SELECT id, 'photo' as type, uploaded_at as item_time
                                FROM photos
                                WHERE album_id IS NULL
                                  AND uploaded_at < ?
                                ORDER BY uploaded_at DESC LIMIT 1
                                """, (current_time,)).fetchone()

        next_album = db.execute("""
                                SELECT id, 'album' as type, created_at as item_time
                                FROM albums
                                WHERE created_at < ?
                                ORDER BY created_at DESC LIMIT 1
                                """, (current_time,)).fetchone()

        # Pick the closest one
        if next_photo and next_album:
            next_item = next_photo if next_photo["item_time"] > next_album["item_time"] else next_album
        else:
            next_item = next_photo or next_album

        prev_photo = prev_item
        next_photo = next_item

    prev_id = prev_photo["id"] if prev_photo else None
    next_id = next_photo["id"] if next_photo else None
    prev_type = prev_photo["type"] if prev_photo and "type" in prev_photo.keys() else "photo"
    next_type = next_photo["type"] if next_photo and "type" in next_photo.keys() else "photo"

    return templates.TemplateResponse(
        "photo.html",
        {
            "request": request,
            "photo": photo,
            "tags": tags_data,
            "prev_id": prev_id,
            "next_id": next_id,
            "prev_type": prev_type,
            "next_type": next_type,
            "album_info": album_info,
            "media_type": photo["media_type"] or "image",
            "user": user
        }
    )


@app.get("/album/{album_id}")
def view_album(request: Request, album_id: str):
    """Redirect to first photo in album"""
    db = get_db()
    user = get_current_user(request)

    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Check access
    if not can_access_album(album_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    first_photo = db.execute(
        "SELECT id FROM photos WHERE album_id = ? ORDER BY position LIMIT 1",
        (album_id,)
    ).fetchone()

    if not first_photo:
        raise HTTPException(status_code=404, detail="Album is empty or not found")

    return RedirectResponse(url=f"/photo/{first_photo['id']}", status_code=302)


@app.get("/uploads/{filename}")
def get_upload(request: Request, filename: str):
    """Serves original photo (protected by auth + folder access)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    file_path = UPLOADS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404)

    # Get photo_id from filename (remove extension)
    photo_id = Path(filename).stem
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(file_path)


@app.get("/thumbnails/{filename}")
def get_thumbnail(request: Request, filename: str):
    """Serves thumbnail (protected by auth + folder access)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    file_path = THUMBNAILS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404)

    # Get photo_id from filename (remove .jpg extension)
    photo_id = Path(filename).stem
    if not can_access_photo(photo_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(file_path)


@app.post("/upload")
async def upload_photo(request: Request, file: UploadFile = None, folder_id: str = Form(None)):
    """Upload new photo or video to specified folder"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    if not file:
        raise HTTPException(status_code=400, detail="file is required")

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder access
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    folder = get_folder(folder_id)
    if folder and folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Cannot upload to another user's folder")

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


@app.post("/upload-album")
async def upload_album(request: Request, files: list[UploadFile], folder_id: str = Form(None)):
    """Upload multiple photos/videos as an album to specified folder"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    # Verify folder access
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Cannot upload to this folder")

    folder = get_folder(folder_id)
    if folder and folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Cannot upload to another user's folder")

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
        except Exception as e:
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


# === AI Service API ===

@app.post("/api/photos/{photo_id}/tags")
def set_tags(photo_id: str, tags: list[str]):
    """Set tags for photo (called by AI service)"""
    db = get_db()

    # Check if photo exists
    photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise HTTPException(status_code=404)

    # Delete old tags and add new ones
    db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))
    for tag in tags:
        db.execute(
            "INSERT INTO tags (photo_id, tag) VALUES (?, ?)",
            (photo_id, tag.lower().strip())
        )
    db.commit()

    return {"status": "ok", "tags": tags}


@app.get("/api/photos/untagged")
def get_untagged():
    """List photos without tags (for AI service)"""
    db = get_db()
    photos = db.execute("""
                        SELECT p.*
                        FROM photos p
                                 LEFT JOIN tags t ON p.id = t.photo_id
                        WHERE t.id IS NULL
                        ORDER BY p.uploaded_at ASC LIMIT 10
                        """).fetchall()

    return [{"id": p["id"], "filename": p["filename"]} for p in photos]


# === Tag Management API ===

class TagInput(BaseModel):
    tag: str
    category_id: int


class TagPresetInput(BaseModel):
    name: str
    category_id: int


class FolderCreate(BaseModel):
    name: str
    parent_id: str | None = None
    access_mode: str = 'private'


class FolderUpdate(BaseModel):
    name: str | None = None
    access_mode: str | None = None


@app.get("/api/tag-categories")
def get_tag_categories():
    """Get all tag categories"""
    db = get_db()
    categories = db.execute("SELECT * FROM tag_categories ORDER BY id").fetchall()
    return [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in categories]


@app.get("/api/tag-presets")
def get_tag_presets(search: str = ""):
    """Get all preset tags grouped by category, optionally filtered by search"""
    db = get_db()

    if search:
        presets = db.execute("""
                             SELECT p.id, p.name, p.category_id, c.name as category_name, c.color
                             FROM tag_presets p
                                      JOIN tag_categories c ON p.category_id = c.id
                             WHERE p.name LIKE ?
                             ORDER BY c.id, p.name
                             """, (f"%{search.lower()}%",)).fetchall()
    else:
        presets = db.execute("""
                             SELECT p.id, p.name, p.category_id, c.name as category_name, c.color
                             FROM tag_presets p
                                      JOIN tag_categories c ON p.category_id = c.id
                             ORDER BY c.id, p.name
                             """).fetchall()

    # Group by category
    result = {}
    for p in presets:
        cat_id = p["category_id"]
        if cat_id not in result:
            result[cat_id] = {
                "id": cat_id,
                "name": p["category_name"],
                "color": p["color"],
                "tags": []
            }
        result[cat_id]["tags"].append({"id": p["id"], "name": p["name"]})

    return list(result.values())


@app.post("/api/tag-presets")
def add_tag_preset(preset: TagPresetInput):
    """Add a new preset tag"""
    db = get_db()

    # Check if category exists
    category = db.execute(
        "SELECT id FROM tag_categories WHERE id = ?", (preset.category_id,)
    ).fetchone()
    if not category:
        raise HTTPException(status_code=400, detail="Category not found")

    # Insert preset
    try:
        db.execute(
            "INSERT INTO tag_presets (name, category_id) VALUES (?, ?)",
            (preset.name.lower().strip(), preset.category_id)
        )
        db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Tag already exists in this category")

    return {"status": "ok", "name": preset.name}


@app.post("/api/photos/{photo_id}/tag")
def add_tag_to_photo(photo_id: str, tag_input: TagInput):
    """Add a single tag to a photo"""
    db = get_db()

    # Check if photo exists
    photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Check if tag already exists for this photo
    existing = db.execute(
        "SELECT id FROM tags WHERE photo_id = ? AND tag = ?",
        (photo_id, tag_input.tag.lower().strip())
    ).fetchone()
    if existing:
        return {"status": "exists", "message": "Tag already added"}

    # Add tag
    cursor = db.execute(
        "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
        (photo_id, tag_input.tag.lower().strip(), tag_input.category_id)
    )
    db.commit()

    # Get category info
    category = db.execute(
        "SELECT name, color FROM tag_categories WHERE id = ?",
        (tag_input.category_id,)
    ).fetchone()

    return {
        "status": "ok",
        "tag": {
            "id": cursor.lastrowid,
            "tag": tag_input.tag.lower().strip(),
            "category_id": tag_input.category_id,
            "category": category["name"] if category else None,
            "color": category["color"] if category else "#6b7280"
        }
    }


@app.delete("/api/photos/{photo_id}/tag/{tag_id}")
def remove_tag_from_photo(photo_id: str, tag_id: int):
    """Remove a tag from a photo"""
    db = get_db()

    db.execute(
        "DELETE FROM tags WHERE id = ? AND photo_id = ?",
        (tag_id, photo_id)
    )
    db.commit()

    return {"status": "ok"}


@app.post("/api/photos/{photo_id}/ai-tags")
def generate_ai_tags(photo_id: str):
    """Generate random tags from presets (simulates AI tagging)"""
    db = get_db()

    # Check if photo exists
    photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Get random presets from different categories
    presets = db.execute("""
                         SELECT p.name, p.category_id, c.color
                         FROM tag_presets p
                                  JOIN tag_categories c ON p.category_id = c.id
                         """).fetchall()

    if not presets:
        return {"status": "error", "message": "No preset tags available"}

    # Select 3-6 random tags
    selected = random.sample(list(presets), min(random.randint(3, 6), len(presets)))

    # Clear existing tags and add new ones
    db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))

    added_tags = []
    for preset in selected:
        cursor = db.execute(
            "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
            (photo_id, preset["name"], preset["category_id"])
        )
        added_tags.append({
            "id": cursor.lastrowid,
            "tag": preset["name"],
            "category_id": preset["category_id"],
            "color": preset["color"]
        })

    db.commit()

    return {"status": "ok", "tags": added_tags}


# === Gallery Search and Batch Operations ===

@app.get("/api/tags/all")
def get_all_tags():
    """Get all unique tags for autocomplete"""
    db = get_db()
    tags = db.execute("""
                      SELECT DISTINCT t.tag, t.category_id, c.color
                      FROM tags t
                               LEFT JOIN tag_categories c ON t.category_id = c.id
                      ORDER BY t.tag
                      """).fetchall()
    return [{"tag": t["tag"], "category_id": t["category_id"], "color": t["color"] or "#6b7280"} for t in tags]


@app.get("/api/photos/search")
def search_photos_by_tags(tags: str = ""):
    """Search photos and albums by tags (space-separated)"""
    db = get_db()

    if not tags.strip():
        # Return all standalone photos and albums if no tags specified
        photos = db.execute(
            "SELECT id, 'photo' as type FROM photos WHERE album_id IS NULL ORDER BY uploaded_at DESC"
        ).fetchall()
        albums = db.execute(
            "SELECT id, 'album' as type FROM albums ORDER BY created_at DESC"
        ).fetchall()
        results = [{"id": p["id"], "type": p["type"]} for p in photos]
        results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
        return results

    tag_list = [t.strip().lower() for t in tags.split() if t.strip()]
    if not tag_list:
        photos = db.execute(
            "SELECT id, 'photo' as type FROM photos WHERE album_id IS NULL ORDER BY uploaded_at DESC"
        ).fetchall()
        albums = db.execute(
            "SELECT id, 'album' as type FROM albums ORDER BY created_at DESC"
        ).fetchall()
        results = [{"id": p["id"], "type": p["type"]} for p in photos]
        results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
        return results

    # Find standalone photos that have ALL specified tags
    placeholders = ",".join("?" * len(tag_list))
    photos = db.execute(f"""
        SELECT p.id, 'photo' as type
        FROM photos p
        WHERE p.album_id IS NULL AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY p.uploaded_at DESC
    """, (*tag_list, len(tag_list))).fetchall()

    # Find albums where at least one photo has ALL specified tags
    albums = db.execute(f"""
        SELECT DISTINCT a.id, 'album' as type
        FROM albums a
        JOIN photos p ON p.album_id = a.id
        WHERE (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY a.created_at DESC
    """, (*tag_list, len(tag_list))).fetchall()

    results = [{"id": p["id"], "type": p["type"]} for p in photos]
    results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
    return results


class BatchDeleteInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


@app.post("/api/photos/batch-delete")
def batch_delete_photos(data: BatchDeleteInput):
    """Delete multiple photos and albums"""
    db = get_db()
    deleted_photos = 0
    deleted_albums = 0

    # Delete individual photos
    for photo_id in data.photo_ids:
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
    return {"status": "ok", "deleted_photos": deleted_photos, "deleted_albums": deleted_albums}


class BatchAIInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


@app.post("/api/photos/batch-ai-tags")
def batch_generate_ai_tags(data: BatchAIInput):
    """Generate AI tags for multiple photos and albums"""
    db = get_db()

    # Get all presets
    presets = db.execute("""
                         SELECT p.name, p.category_id, c.color
                         FROM tag_presets p
                                  JOIN tag_categories c ON p.category_id = c.id
                         """).fetchall()

    if not presets:
        return {"status": "error", "message": "No preset tags available"}

    # Collect all photo IDs to process (individual + from albums)
    all_photo_ids = list(data.photo_ids)
    for album_id in data.album_ids:
        album_photos = db.execute(
            "SELECT id FROM photos WHERE album_id = ?", (album_id,)
        ).fetchall()
        all_photo_ids.extend([p["id"] for p in album_photos])

    processed = 0
    for photo_id in all_photo_ids:
        photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if photo:
            # Select 3-6 random tags
            selected = random.sample(list(presets), min(random.randint(3, 6), len(presets)))

            # Clear existing tags and add new ones
            db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))

            for preset in selected:
                db.execute(
                    "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
                    (photo_id, preset["name"], preset["category_id"])
                )

            # Mark as AI processed
            db.execute("UPDATE photos SET ai_processed = 1 WHERE id = ?", (photo_id,))
            processed += 1

    db.commit()
    return {"status": "ok", "processed": processed}


# === Folder API ===

@app.get("/api/folders")
def get_folders(request: Request):
    """Get folder tree for current user"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    folders = get_folder_tree(user["id"])
    return folders


@app.post("/api/folders")
def create_new_folder(request: Request, data: FolderCreate):
    """Create a new folder"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    # If parent_id specified, verify access
    if data.parent_id:
        if not can_access_folder(data.parent_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot create folder in this location")
        parent = get_folder(data.parent_id)
        if parent and parent["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Cannot create folder in another user's folder")

    folder_id = create_folder(data.name, user["id"], data.parent_id, data.access_mode)
    folder = get_folder(folder_id)

    return {"status": "ok", "folder": dict(folder)}


@app.put("/api/folders/{folder_id}")
def update_existing_folder(request: Request, folder_id: str, data: FolderUpdate):
    """Update folder name and/or access mode"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="You don't own this folder")

    update_folder(folder_id, data.name, data.access_mode)
    updated = get_folder(folder_id)

    return {"status": "ok", "folder": dict(updated)}


@app.delete("/api/folders/{folder_id}")
def delete_folder_route(request: Request, folder_id: str):
    """Delete folder and all its contents"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="You don't own this folder")

    # Get filenames to delete
    filenames = db_delete_folder(folder_id)

    # Delete actual files
    for filename in filenames:
        file_path = UPLOADS_DIR / filename
        photo_id = Path(filename).stem
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        file_path.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)

    return {"status": "ok"}


@app.get("/api/folders/{folder_id}/contents")
def get_folder_contents_route(request: Request, folder_id: str):
    """Get contents of a specific folder"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    contents = get_folder_contents(folder_id, user["id"])
    return contents


@app.post("/api/folders/{folder_id}/set-default")
def set_default_folder(request: Request, folder_id: str):
    """Set folder as user's default folder"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="You don't own this folder")

    set_user_default_folder(user["id"], folder_id)
    return {"status": "ok"}
