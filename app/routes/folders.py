"""Folder management routes."""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..config import UPLOADS_DIR, THUMBNAILS_DIR
from ..dependencies import require_user

# Service layer imports (Issue #16)
from ..infrastructure.repositories import (
    FolderRepository, PermissionRepository, SafeRepository, UserRepository
)
from ..application.services import FolderService, PermissionService, UserSettingsService

router = APIRouter(prefix="/api/folders", tags=["folders"])


# Pydantic models for request validation
class FolderCreate(BaseModel):
    name: str
    parent_id: str | None = None
    safe_id: str | None = None


class FolderUpdate(BaseModel):
    name: str | None = None


class PermissionCreate(BaseModel):
    user_id: int
    permission: str  # 'viewer' | 'editor'


class PermissionUpdate(BaseModel):
    permission: str


class SortPreference(BaseModel):
    sort_by: str  # 'uploaded' | 'taken'


# Service factory functions
def get_folder_service() -> FolderService:
    """Create FolderService with repositories."""
    db = get_db()
    return FolderService(
        folder_repository=FolderRepository(db),
        safe_repository=SafeRepository(db)
    )


def get_user_settings_service() -> UserSettingsService:
    """Create UserSettingsService with repositories."""
    db = get_db()
    return UserSettingsService(
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db)
    )


def get_permission_service() -> PermissionService:
    """Create PermissionService with repositories."""
    db = get_db()
    return PermissionService(
        permission_repository=PermissionRepository(db),
        folder_repository=FolderRepository(db)
    )


# === Folder CRUD ===

@router.get("")
def get_folders(request: Request):
    """Get folder tree for current user."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_folder_service()
    return service.get_folder_tree(user["id"])


@router.post("")
def create_new_folder(request: Request, data: FolderCreate):
    """Create a new folder."""
    user = require_user(request)
    
    db = get_db()
    try:
        # Handle safe folder creation
        if data.safe_id:
            safe_repo = SafeRepository(db)
            safe = safe_repo.get_by_id(data.safe_id)
            if not safe:
                raise HTTPException(status_code=404, detail="Safe not found")
            if safe["user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Access denied")
            
            if not safe_repo.is_unlocked(data.safe_id, user["id"]):
                raise HTTPException(status_code=403, detail="Safe is locked. Please unlock first.")
        
        # Using service layer (Issue #16)
        service = FolderService(
            folder_repository=FolderRepository(db),
            safe_repository=SafeRepository(db)
        )
        folder = service.create_folder(
            name=data.name,
            user_id=user["id"],
            parent_id=data.parent_id,
            safe_id=data.safe_id
        )
        
        return {"status": "ok", "folder": dict(folder)}
    finally:
        db.close()


@router.put("/{folder_id}")
def update_existing_folder(request: Request, folder_id: str, data: FolderUpdate):
    """Update folder name."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_folder_service()
    folder = service.update_folder(folder_id, data.name, user["id"])
    
    return {"status": "ok", "folder": dict(folder)}


@router.delete("/{folder_id}")
def delete_folder_route(request: Request, folder_id: str):
    """Delete folder and all its contents."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_folder_service()
    filenames = service.delete_folder(folder_id, user["id"])
    
    # Delete actual files
    for filename in filenames:
        file_path = UPLOADS_DIR / filename
        photo_id = Path(filename).stem
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        file_path.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
    
    return {"status": "ok"}


@router.get("/{folder_id}/contents")
def get_folder_contents_route(request: Request, folder_id: str):
    """Get contents of a specific folder."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_folder_service()
    contents = service.get_folder_contents(folder_id, user["id"])
    return contents


@router.post("/{folder_id}/set-default")
def set_default_folder(request: Request, folder_id: str):
    """Set folder as user's default folder."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_user_settings_service()
    service.set_default_folder(user["id"], folder_id)
    return {"status": "ok"}


# === Folder Permissions (using PermissionService) ===

@router.get("/{folder_id}/permissions")
def get_folder_permissions_route(request: Request, folder_id: str):
    """Get all permissions for a folder (owner only)."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    service = get_permission_service()
    permissions = service.get_folder_permissions(folder_id, user["id"])
    
    return {"permissions": permissions}


@router.post("/{folder_id}/permissions")
def add_folder_permission_route(request: Request, folder_id: str, data: PermissionCreate):
    """Add permission for a user on a folder (owner only)."""
    user = require_user(request)
    
    db = get_db()
    try:
        # Using service layer (Issue #16)
        service = PermissionService(
            permission_repository=PermissionRepository(db),
            folder_repository=FolderRepository(db)
        )
        success = service.grant_permission(
            folder_id=folder_id,
            user_id=data.user_id,
            permission=data.permission,
            granted_by=user["id"]
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="Failed to add permission")
        
        permissions = service.perm_repo.list_for_folder(folder_id)
        return {"status": "ok", "permissions": permissions}
    finally:
        db.close()


@router.put("/{folder_id}/permissions/{target_user_id}")
def update_folder_permission_route(
    request: Request,
    folder_id: str,
    target_user_id: int,
    data: PermissionUpdate
):
    """Update permission for a user on a folder (owner only)."""
    user = require_user(request)
    
    db = get_db()
    try:
        # Using service layer (Issue #16)
        service = PermissionService(
            permission_repository=PermissionRepository(db),
            folder_repository=FolderRepository(db)
        )
        success = service.update_permission(
            folder_id=folder_id,
            user_id=target_user_id,
            new_permission=data.permission,
            updated_by=user["id"]
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Permission not found")
        
        permissions = service.perm_repo.list_for_folder(folder_id)
        return {"status": "ok", "permissions": permissions}
    finally:
        db.close()


@router.delete("/{folder_id}/permissions/{target_user_id}")
def remove_folder_permission_route(
    request: Request,
    folder_id: str,
    target_user_id: int
):
    """Remove permission for a user on a folder (owner only)."""
    user = require_user(request)
    
    db = get_db()
    try:
        # Using service layer (Issue #16)
        service = PermissionService(
            permission_repository=PermissionRepository(db),
            folder_repository=FolderRepository(db)
        )
        success = service.revoke_permission(
            folder_id=folder_id,
            user_id=target_user_id,
            revoked_by=user["id"]
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Permission not found")
        
        permissions = service.perm_repo.list_for_folder(folder_id)
        return {"status": "ok", "permissions": permissions}
    finally:
        db.close()


# === Folder Preferences ===

@router.put("/{folder_id}/sort")
def set_sort_preference(request: Request, folder_id: str, data: SortPreference):
    """Set sort preference for a folder (per user)."""
    user = require_user(request)
    
    # Using service layer for access check (Issue #16)
    service = get_permission_service()
    if not service.can_access(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if data.sort_by not in ('uploaded', 'taken'):
        raise HTTPException(status_code=400, detail="sort_by must be 'uploaded' or 'taken'")
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    settings_service.set_sort_preference(user["id"], folder_id, data.sort_by)
    return {"status": "ok", "sort_by": data.sort_by}


@router.get("/{folder_id}/sort")
def get_sort_preference(request: Request, folder_id: str):
    """Get sort preference for a folder (per user)."""
    user = require_user(request)
    
    # Using service layer for access check (Issue #16)
    service = get_permission_service()
    if not service.can_access(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    sort_by = settings_service.get_sort_preference(user["id"], folder_id)
    return {"sort_by": sort_by}


@router.put("/{folder_id}/set-default")
def set_default_folder_route(request: Request, folder_id: str):
    """Set folder as user's default folder (opens on login)."""
    user = require_user(request)
    
    # Using service layer for access check (Issue #16)
    service = get_permission_service()
    if not service.can_access(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    settings_service.set_default_folder(user["id"], folder_id)
    
    return {"status": "ok"}


@router.get("/user/default")
def get_default_folder_route(request: Request):
    """Get user's default folder ID."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    default_folder_id = settings_service.get_default_folder(user["id"])
    return {"default_folder_id": default_folder_id}


@router.get("/user/collapsed")
def get_collapsed_folders_route(request: Request):
    """Get list of collapsed folder IDs for current user."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    collapsed = settings_service.get_collapsed_folders(user["id"])
    return {"collapsed_folders": collapsed}


@router.post("/{folder_id}/toggle-collapse")
def toggle_collapse_route(request: Request, folder_id: str):
    """Toggle folder collapsed state. Returns new state."""
    user = require_user(request)
    
    # Using service layer (Issue #16)
    settings_service = get_user_settings_service()
    is_collapsed = settings_service.toggle_collapsed_folder(user["id"], folder_id)
    return {"collapsed": is_collapsed}


# User search for sharing (separate router prefix)
users_router = APIRouter(prefix="/api/users", tags=["users"])


@users_router.get("/search")
def search_users_route(request: Request, q: str = ""):
    """Search users by name (for sharing)."""
    user = require_user(request)
    
    if len(q) < 2:
        return {"users": []}
    
    db = get_db()
    try:
        user_repo = UserRepository(db)
        users = user_repo.search(q, exclude_user_id=user["id"], limit=10)
        return {"users": users}
    finally:
        db.close()
