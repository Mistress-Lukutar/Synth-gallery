"""Folder management routes."""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import UPLOADS_DIR, THUMBNAILS_DIR
from ..database import (
    get_db, get_folder, get_folder_tree, get_folder_contents,
    create_folder, update_folder, delete_folder as db_delete_folder,
    get_user_default_folder, set_user_default_folder,
    can_access_folder, can_edit_folder,
    add_folder_permission, remove_folder_permission, update_folder_permission,
    get_folder_permissions, search_users,
    get_folder_sort_preference, set_folder_sort_preference
)
from ..dependencies import require_user

router = APIRouter(prefix="/api/folders", tags=["folders"])


class FolderCreate(BaseModel):
    name: str
    parent_id: str | None = None


class FolderUpdate(BaseModel):
    name: str | None = None


class PermissionCreate(BaseModel):
    user_id: int
    permission: str  # 'viewer' | 'editor'


class PermissionUpdate(BaseModel):
    permission: str  # 'viewer' | 'editor'


@router.get("")
def get_folders(request: Request):
    """Get folder tree for current user."""
    user = require_user(request)
    folders = get_folder_tree(user["id"])
    return folders


@router.post("")
def create_new_folder(request: Request, data: FolderCreate):
    """Create a new folder."""
    user = require_user(request)

    # If parent_id specified, verify user owns the parent folder
    if data.parent_id:
        parent = get_folder(data.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found")
        if parent["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Cannot create folder in another user's folder")

    folder_id = create_folder(data.name, user["id"], data.parent_id)
    folder = get_folder(folder_id)

    return {"status": "ok", "folder": dict(folder)}


@router.put("/{folder_id}")
def update_existing_folder(request: Request, folder_id: str, data: FolderUpdate):
    """Update folder name."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="You don't own this folder")

    update_folder(folder_id, data.name)
    updated = get_folder(folder_id)

    return {"status": "ok", "folder": dict(updated)}


@router.delete("/{folder_id}")
def delete_folder_route(request: Request, folder_id: str):
    """Delete folder and all its contents."""
    user = require_user(request)

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


@router.get("/{folder_id}/contents")
def get_folder_contents_route(request: Request, folder_id: str):
    """Get contents of a specific folder."""
    user = require_user(request)

    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    contents = get_folder_contents(folder_id, user["id"])
    return contents


@router.post("/{folder_id}/set-default")
def set_default_folder(request: Request, folder_id: str):
    """Set folder as user's default folder."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="You don't own this folder")

    set_user_default_folder(user["id"], folder_id)
    return {"status": "ok"}


# === Folder Permissions ===

@router.get("/{folder_id}/permissions")
def get_folder_permissions_route(request: Request, folder_id: str):
    """Get all permissions for a folder (owner only)."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only folder owner can manage permissions")

    permissions = get_folder_permissions(folder_id)
    return {"permissions": permissions}


@router.post("/{folder_id}/permissions")
def add_folder_permission_route(request: Request, folder_id: str, data: PermissionCreate):
    """Add permission for a user on a folder (owner only)."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only folder owner can manage permissions")

    if data.user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot set permission for yourself")

    if data.permission not in ('viewer', 'editor'):
        raise HTTPException(status_code=400, detail="Permission must be 'viewer' or 'editor'")

    success = add_folder_permission(folder_id, data.user_id, data.permission, user["id"])
    if not success:
        raise HTTPException(status_code=400, detail="Failed to add permission")

    permissions = get_folder_permissions(folder_id)
    return {"status": "ok", "permissions": permissions}


@router.put("/{folder_id}/permissions/{target_user_id}")
def update_folder_permission_route(request: Request, folder_id: str, target_user_id: int, data: PermissionUpdate):
    """Update permission for a user on a folder (owner only)."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only folder owner can manage permissions")

    if data.permission not in ('viewer', 'editor'):
        raise HTTPException(status_code=400, detail="Permission must be 'viewer' or 'editor'")

    success = update_folder_permission(folder_id, target_user_id, data.permission)
    if not success:
        raise HTTPException(status_code=404, detail="Permission not found")

    permissions = get_folder_permissions(folder_id)
    return {"status": "ok", "permissions": permissions}


@router.delete("/{folder_id}/permissions/{target_user_id}")
def remove_folder_permission_route(request: Request, folder_id: str, target_user_id: int):
    """Remove permission for a user on a folder (owner only)."""
    user = require_user(request)

    folder = get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only folder owner can manage permissions")

    success = remove_folder_permission(folder_id, target_user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Permission not found")

    permissions = get_folder_permissions(folder_id)
    return {"status": "ok", "permissions": permissions}


# === Folder Preferences ===

class SortPreference(BaseModel):
    sort_by: str  # 'uploaded' | 'taken'


@router.put("/{folder_id}/sort")
def set_sort_preference(request: Request, folder_id: str, data: SortPreference):
    """Set sort preference for a folder (per user)."""
    user = require_user(request)

    # Check user has access to folder
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    if data.sort_by not in ('uploaded', 'taken'):
        raise HTTPException(status_code=400, detail="sort_by must be 'uploaded' or 'taken'")

    set_folder_sort_preference(user["id"], folder_id, data.sort_by)
    return {"status": "ok", "sort_by": data.sort_by}


@router.get("/{folder_id}/sort")
def get_sort_preference(request: Request, folder_id: str):
    """Get sort preference for a folder (per user)."""
    user = require_user(request)

    # Check user has access to folder
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    sort_by = get_folder_sort_preference(user["id"], folder_id)
    return {"sort_by": sort_by}


@router.put("/{folder_id}/set-default")
def set_default_folder_route(request: Request, folder_id: str):
    """Set folder as user's default folder (opens on login)."""
    user = require_user(request)

    # Check user has access to folder
    if not can_access_folder(folder_id, user["id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    from ..database import set_user_default_folder
    success = set_user_default_folder(user["id"], folder_id)

    if not success:
        raise HTTPException(status_code=400, detail="Failed to set default folder")

    return {"status": "ok"}


@router.get("/user/default")
def get_default_folder_route(request: Request):
    """Get user's default folder ID."""
    user = require_user(request)

    from ..database import get_user_default_folder
    default_folder_id = get_user_default_folder(user["id"])

    return {"default_folder_id": default_folder_id}


@router.get("/user/collapsed")
def get_collapsed_folders_route(request: Request):
    """Get list of collapsed folder IDs for current user."""
    user = require_user(request)

    from ..database import get_collapsed_folders
    collapsed = get_collapsed_folders(user["id"])

    return {"collapsed_folders": collapsed}


@router.post("/{folder_id}/toggle-collapse")
def toggle_collapse_route(request: Request, folder_id: str):
    """Toggle folder collapsed state. Returns new state."""
    user = require_user(request)

    from ..database import toggle_folder_collapsed
    is_collapsed = toggle_folder_collapsed(user["id"], folder_id)

    return {"collapsed": is_collapsed}


# User search for sharing (separate router prefix)
users_router = APIRouter(prefix="/api/users", tags=["users"])


@users_router.get("/search")
def search_users_route(request: Request, q: str = ""):
    """Search users by name (for sharing)."""
    user = require_user(request)

    if len(q) < 2:
        return {"users": []}

    users = search_users(q, exclude_user_id=user["id"], limit=10)
    return {"users": users}
