"""Main gallery routes - page view and folder content API."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ...application.services import UserSettingsService
from ...config import ROOT_PATH, BASE_DIR
from ...database import create_connection
from ...dependencies import get_current_user
from ...infrastructure.repositories import (
    FolderRepository, PermissionRepository, SafeRepository, UserRepository
)
from ...infrastructure.services.encryption import dek_cache
from .deps import get_folder_service, get_permission_service

router = APIRouter()

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH


class UserSettingsRepository:
    """Repository for user settings operations."""
    
    def __init__(self, db):
        self._conn = db
    
    def get_default_folder(self, user_id: int) -> str | None:
        """Get user's default folder ID."""
        cursor = self._conn.execute(
            "SELECT default_folder_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["default_folder_id"] if row else None
    
    def get_sort_preference(self, user_id: int, folder_id: str) -> str:
        """Get user's sort preference for a folder."""
        cursor = self._conn.execute(
            "SELECT sort_by FROM user_folder_preferences WHERE user_id = ? AND folder_id = ?",
            (user_id, folder_id)
        )
        row = cursor.fetchone()
        return row["sort_by"] if row else "uploaded"
    
    def get_encryption_keys(self, user_id: int) -> dict | None:
        """Get encryption metadata for user."""
        cursor = self._conn.execute("""
            SELECT encrypted_dek, dek_salt, encryption_version
            FROM user_settings WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        
        if row and row["encrypted_dek"]:
            return {
                "encrypted_dek": row["encrypted_dek"],
                "dek_salt": row["dek_salt"],
                "encryption_version": row["encryption_version"]
            }
        return None


@router.get("/")
def gallery(request: Request, folder_id: str = None):
    """Main page - SPA shell. Data loaded via API."""
    user = get_current_user(request)

    if not user:
        return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        folder_service = get_folder_service(db)
        perm_service = get_permission_service(db)
        user_settings_repo = UserSettingsRepository(db)
        safe_repo = SafeRepository(db)

        enc_keys = user_settings_repo.get_encryption_keys(user["id"])
        if enc_keys and not dek_cache.get(user["id"]):
            return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

        folder_tree = folder_service.get_folder_tree(user["id"])

        # Determine initial folder to load
        initial_folder_id = folder_id
        
        # Check permission if folder_id provided
        if initial_folder_id and not perm_service.can_access(initial_folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not initial_folder_id:
            default_folder_id = user_settings_repo.get_default_folder(user["id"])
            if default_folder_id:
                folder = folder_repo.get_by_id(default_folder_id)
                if folder and perm_service.can_access(default_folder_id, user["id"]):
                    initial_folder_id = default_folder_id
            
            if not initial_folder_id:
                user_settings_service = UserSettingsService(
                    folder_repository=folder_repo,
                    permission_repository=perm_service.perm_repo,
                    user_repository=UserRepository(db)
                )
                initial_folder_id = user_settings_service.create_default_folder(user["id"])

        # Build safe_folders for sidebar
        safe_folders = {}
        for folder in folder_tree:
            if folder.get("safe_id"):
                safe = safe_repo.get_by_id(folder["safe_id"])
                safe_folders[folder["id"]] = {
                    "safe_name": safe["name"] if safe else "Unknown Safe",
                    "is_unlocked": safe_repo.is_unlocked(folder["safe_id"], user["id"])
                }

        return templates.TemplateResponse("gallery.html", {
            "request": request,
            "user": user,
            "folder_tree": folder_tree,
            "safe_folders": safe_folders,
            "dek_in_cache": dek_cache.get(user["id"]) is not None,
            "initial_folder_id": initial_folder_id,
        })
    finally:
        db.close()


@router.get("/api/folders/{folder_id}/content")
def get_folder_content_api(folder_id: str, request: Request, sort: str = None):
    """Get folder contents as JSON (for SPA navigation)."""
    from ...dependencies import require_user
    user = require_user(request)

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        folder_service = get_folder_service(db)
        perm_service = get_permission_service(db)
        user_settings_repo = UserSettingsRepository(db)

        if not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if sort is None or sort not in ("uploaded", "taken"):
            sort = user_settings_repo.get_sort_preference(user["id"], folder_id)
        

        folder_contents = folder_service.get_folder_contents(folder_id, user["id"])
        
        # Build flat items list for SPA (unified structure)
        items = []
        
        # Add photo_count to subfolders
        for folder in folder_contents["subfolders"]:
            folder["photo_count"] = folder_repo.get_photo_count(folder["id"])
            items.append({
                "type": "folder",
                "id": folder["id"],
                "name": folder["name"],
                "photo_count": folder["photo_count"],
                "user_id": folder.get("user_id"),
            })
        
        for album in folder_contents["albums"]:
            cover_photo_id = album.get("cover_photo_id") or album.get("effective_cover_photo_id")
            items.append({
                "type": "album",
                "id": album["id"],
                "name": album["name"],
                "photo_count": album.get("photo_count", 0),
                "cover_photo_id": cover_photo_id,
                "cover_thumb_width": album.get("cover_thumb_width"),
                "cover_thumb_height": album.get("cover_thumb_height"),
                "safe_id": album.get("safe_id"),
                # Add dates for frontend sorting
                "uploaded_at": album.get("max_uploaded_at"),
                "taken_at": album.get("max_taken_at"),
            })
        
        for photo in folder_contents["photos"]:
            items.append({
                "type": "photo",
                "id": photo["id"],
                "filename": photo["filename"],
                "original_name": photo["original_name"],
                "media_type": photo.get("media_type", "image"),
                "thumb_width": photo.get("thumb_width"),
                "thumb_height": photo.get("thumb_height"),
                "safe_id": photo.get("safe_id"),
                # Add dates for frontend sorting
                "uploaded_at": photo.get("uploaded_at"),
                "taken_at": photo.get("taken_at"),
            })
        
        # Get current folder info
        current_folder = folder_repo.get_by_id(folder_id)
        current_folder = dict(current_folder) if current_folder else None
        if current_folder:
            current_folder["permission"] = perm_service.get_user_permission(folder_id, user["id"])
        breadcrumbs = folder_service.get_breadcrumbs(folder_id)

        return {
            "folder": current_folder,
            "breadcrumbs": breadcrumbs if folder_id else [],
            "subfolders": folder_contents["subfolders"],
            "items": items,
            "sort": sort,
        }
    finally:
        db.close()


class SortPreferenceInput(BaseModel):
    sort_by: str


@router.put("/api/folders/{folder_id}/sort")
async def set_folder_sort_preference(folder_id: str, data: SortPreferenceInput, request: Request):
    """Save user's sort preference for a folder."""
    from ...dependencies import require_user
    
    user = require_user(request)
    

    
    if data.sort_by not in ('uploaded', 'taken'):
        raise HTTPException(status_code=400, detail="Invalid sort option")
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        
        if not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Save preference
        db.execute(
            """INSERT OR REPLACE INTO user_folder_preferences (user_id, folder_id, sort_by)
               VALUES (?, ?, ?)""",
            (user["id"], folder_id, data.sort_by)
        )
        db.commit()

        
        return {"status": "ok", "sort_by": data.sort_by}
    finally:
        db.close()


@router.get("/api/user/default-folder")
def get_default_folder_api(request: Request):
    """Get or create user's default folder."""
    from ...dependencies import require_user
    user = require_user(request)

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        perm_service = get_permission_service(db)
        user_settings_repo = UserSettingsRepository(db)

        folder_id = user_settings_repo.get_default_folder(user["id"])

        if folder_id:
            folder = folder_repo.get_by_id(folder_id)
            if folder and perm_service.can_access(folder_id, user["id"]):
                return {"folder_id": folder_id}

        user_settings_service = UserSettingsService(
            folder_repository=folder_repo,
            permission_repository=perm_service.perm_repo,
            user_repository=UserRepository(db)
        )
        folder_id = user_settings_service.create_default_folder(user["id"])
        return {"folder_id": folder_id}
    finally:
        db.close()
