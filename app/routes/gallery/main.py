"""Main gallery routes - page view and folder content API."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ...application.services import UserSettingsService
from ...config import ROOT_PATH, BASE_DIR
from ...database import create_connection
from ...dependencies import get_current_user, get_csrf_token
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
def gallery(request: Request, folder_id: str = None, sort: str = None):
    """Main page - gallery with folders, albums and photos."""
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

        if folder_id:
            if not perm_service.can_access(folder_id, user["id"]):
                raise HTTPException(status_code=403, detail="Access denied")

            current_folder = folder_repo.get_by_id(folder_id)
            if current_folder:
                current_folder = dict(current_folder)
                current_folder["permission"] = perm_service.get_user_permission(folder_id, user["id"])
                breadcrumbs = folder_service.get_breadcrumbs(folder_id)
        else:
            default_folder_id = user_settings_repo.get_default_folder(user["id"])
            if default_folder_id:
                folder = folder_repo.get_by_id(default_folder_id)
                if folder and perm_service.can_access(default_folder_id, user["id"]):
                    return RedirectResponse(url=f"{ROOT_PATH}/?folder_id={default_folder_id}", status_code=302)
            
            from ...application.services import UserSettingsService
            user_settings_service = UserSettingsService(
                folder_repository=folder_repo,
                permission_repository=perm_service.perm_repo,
                user_repository=UserRepository(db)
            )
            default_folder_id = user_settings_service.create_default_folder(user["id"])
            return RedirectResponse(url=f"{ROOT_PATH}/?folder_id={default_folder_id}", status_code=302)

        if sort is None or sort not in ("uploaded", "taken"):
            sort = user_settings_repo.get_sort_preference(user["id"], folder_id)

        folder_contents = folder_service.get_folder_contents(folder_id, user["id"])

        safe_folders = {
            folder["id"]: {
                "safe_name": safe_repo.get_by_id(folder["safe_id"])["name"] if safe_repo.get_by_id(folder["safe_id"]) else "Unknown Safe",
                "is_unlocked": safe_repo.is_unlocked(folder["safe_id"], user["id"]) if folder.get("safe_id") else False
            }
            for folder in folder_tree
            if folder.get("safe_id")
        }

        return templates.TemplateResponse("gallery.html", {
            "request": request,
            "user": user,
            "folder_tree": folder_tree,
            "current_folder": current_folder,
            "breadcrumbs": breadcrumbs if folder_id else [],
            "subfolders": folder_contents["subfolders"],
            "albums": folder_contents["albums"],
            "photos": folder_contents["photos"],
            "sort": sort,
            "safe_folders": safe_folders,
            "dek_in_cache": dek_cache.get(user["id"]) is not None,
        })
    finally:
        db.close()


@router.get("/api/folders/{folder_id}/content")
def get_folder_content_api(folder_id: str, request: Request, sort: str = None):
    """Get folder contents as JSON (for HTMX dynamic loading)."""
    from ...dependencies import require_user
    user = require_user(request)

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        folder_service = get_folder_service(db)
        perm_service = get_permission_service(db)
        user_settings_repo = UserSettingsRepository(db)
        safe_repo = SafeRepository(db)

        if not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if sort is None or sort not in ("uploaded", "taken"):
            sort = user_settings_repo.get_sort_preference(user["id"], folder_id)

        folder_contents = folder_service.get_folder_contents(folder_id, user["id"])
        
        # Get folder tree for sidebar  
        folder_tree = folder_service.get_folder_tree(user["id"])
        
        # Get current folder info
        current_folder = folder_repo.get_by_id(folder_id)
        current_folder = dict(current_folder) if current_folder else None
        if current_folder:
            current_folder["permission"] = perm_service.get_user_permission(folder_id, user["id"])
        breadcrumbs = folder_service.get_breadcrumbs(folder_id)
        
        # Get safe folders info
        safe_folders = {}
        for folder in folder_tree:
            if folder.get("safe_id"):
                safe = safe_repo.get_by_id(folder["safe_id"])
                safe_folders[folder["id"]] = {
                    "safe_name": safe["name"] if safe else "Unknown Safe",
                    "is_unlocked": safe_repo.is_unlocked(folder["safe_id"], user["id"])
                }

        # API endpoint always returns JSON
        return {
            "subfolders": folder_contents["subfolders"],
            "albums": folder_contents["albums"],
            "photos": folder_contents["photos"],
            "sort": sort,
            "current_folder": current_folder,
        }
    finally:
        db.close()


@router.get("/folders/{folder_id}/view")
def view_folder_contents(folder_id: str, request: Request, sort: str = None):
    """Get folder contents as HTML page (for HTMX dynamic loading)."""
    from ...dependencies import require_user
    user = require_user(request)

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        folder_service = get_folder_service(db)
        perm_service = get_permission_service(db)
        user_settings_repo = UserSettingsRepository(db)
        safe_repo = SafeRepository(db)

        if not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if sort is None or sort not in ("uploaded", "taken"):
            sort = user_settings_repo.get_sort_preference(user["id"], folder_id)

        folder_contents = folder_service.get_folder_contents(folder_id, user["id"])

        # Get folder tree for sidebar  
        folder_tree = folder_service.get_folder_tree(user["id"])
        
        # Get current folder info
        current_folder = folder_repo.get_by_id(folder_id)
        current_folder = dict(current_folder) if current_folder else None
        if current_folder:
            current_folder["permission"] = perm_service.get_user_permission(folder_id, user["id"])
        breadcrumbs = folder_service.get_breadcrumbs(folder_id)
        
        # Get safe folders info
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
            "current_folder": current_folder,
            "breadcrumbs": breadcrumbs if folder_id else [],
            "subfolders": folder_contents["subfolders"],
            "albums": folder_contents["albums"],
            "photos": folder_contents["photos"],
            "sort": sort,
            "safe_folders": safe_folders,
            "dek_in_cache": dek_cache.get(user["id"]) is not None,
        })
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

        from ...application.services import UserSettingsService
        user_settings_service = UserSettingsService(
            folder_repository=folder_repo,
            permission_repository=perm_service.perm_repo,
            user_repository=UserRepository(db)
        )
        folder_id = user_settings_service.create_default_folder(user["id"])
        return {"folder_id": folder_id}
    finally:
        db.close()
