"""Item routes - unified API for all content types.

Replaces the old photos.py with polymorphic item handling.
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import (
    ItemRepository, ItemMediaRepository, AlbumRepository, FolderRepository
)
from ...application.services import ItemService, AlbumService
from .deps import get_permission_service

router = APIRouter()


def get_item_service(db) -> ItemService:
    """Get configured ItemService."""
    return ItemService(
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db)
    )


def get_album_service(db) -> AlbumService:
    """Get configured AlbumService."""
    return AlbumService(
        album_repository=AlbumRepository(db),
        item_repository=ItemRepository(db),
        folder_repository=FolderRepository(db)
    )


# =============================================================================
# Item Endpoints
# =============================================================================

@router.get("/api/items")
def list_items(
    request: Request,
    folder_id: str,
    type: Optional[str] = None,
    sort: str = "created"
):
    """List items in folder.
    
    Args:
        folder_id: Folder to list
        type: Filter by type ('media', 'note') or omit for all
        sort: 'created' or 'title'
    """
    user = require_user(request)
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        
        if not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(403, "Access denied")
        
        item_service = get_item_service(db)
        items = item_service.get_items_by_folder(
            folder_id=folder_id,
            item_type=type,
            sort_by=sort
        )
        
        # Render for response
        result = []
        for item in items:
            rendered = item_service.render_for_gallery(item)
            result.append({
                "id": item["id"],
                "type": item["type"],
                "title": item.get("title", ""),
                "created_at": item["created_at"],
                **rendered
            })
        
        return {"items": result}
    finally:
        db.close()


@router.get("/api/items/{item_id}")
def get_item(item_id: str, request: Request):
    """Get single item with full details."""
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        item = item_service.get_item(item_id)
        
        if not item:
            raise HTTPException(404, "Item not found")
        
        # Check access via folder
        perm_service = get_permission_service(db)
        if not perm_service.can_access(item["folder_id"], user["id"]):
            raise HTTPException(403, "Access denied")
        
        return item
    finally:
        db.close()


class ItemMoveInput(BaseModel):
    folder_id: str


@router.put("/api/items/{item_id}/move")
def move_item(item_id: str, data: ItemMoveInput, request: Request):
    """Move item to different folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        
        # Check source access
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(item["folder_id"], user["id"]):
            raise HTTPException(403, "Cannot move from this folder")
        
        if not perm_service.can_edit(data.folder_id, user["id"]):
            raise HTTPException(403, "Cannot move to this folder")
        
        success = item_service.move_item(item_id, data.folder_id, user["id"])
        if not success:
            raise HTTPException(400, "Move failed")
        
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/api/items/{item_id}")
def delete_item(item_id: str, request: Request):
    """Delete item."""
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        
        # Check ownership
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        if item["user_id"] != user["id"]:
            raise HTTPException(403, "Not owner")
        
        success = item_service.delete_item(item_id, user["id"])
        if not success:
            raise HTTPException(400, "Delete failed")
        
        return {"status": "ok"}
    finally:
        db.close()


# =============================================================================
# Album Endpoints
# =============================================================================

class AlbumCreateInput(BaseModel):
    name: str
    folder_id: str
    item_ids: List[str] = []
    photo_ids: List[str] = []  # Legacy alias for backward compatibility


@router.post("/api/albums")
def create_album(data: AlbumCreateInput, request: Request):
    """Create new album with items."""
    user = require_user(request)
    
    # Support both new (item_ids) and legacy (photo_ids) formats
    item_ids = data.item_ids or data.photo_ids or []
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        album = album_service.create_album(
            name=data.name,
            folder_id=data.folder_id,
            user_id=user["id"],
            item_ids=item_ids
        )
        
        return {"status": "ok", "album": album}
    finally:
        db.close()


@router.get("/api/albums/{album_id}")
def get_album(album_id: str, request: Request):
    """Get album with items."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        album = album_service.get_album(album_id, user["id"])
        
        if not album:
            raise HTTPException(404, "Album not found")
        
        return album
    finally:
        db.close()


@router.put("/api/albums/{album_id}")
def update_album(album_id: str, data: dict, request: Request):
    """Update album (name, etc)."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_repo = AlbumRepository(db)
        album_service = get_album_service(db)
        
        # Check edit permission
        if not album_service._can_edit(album_id, user["id"]):
            raise HTTPException(403, "Cannot edit album")
        
        # Update allowed fields
        if "name" in data:
            album_repo.update(album_id, name=data["name"])
        
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/api/albums/{album_id}")
def delete_album(album_id: str, request: Request):
    """Delete album (items stay in folder)."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        success = album_service.delete_album(album_id, user["id"])
        if not success:
            raise HTTPException(400, "Delete failed")
        
        return {"status": "ok"}
    finally:
        db.close()


class AlbumMoveInput(BaseModel):
    folder_id: str


@router.put("/api/albums/{album_id}/move")
def move_album(album_id: str, data: AlbumMoveInput, request: Request):
    """Move album to different folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        success = album_service.move_album(
            album_id, data.folder_id, user["id"]
        )
        if not success:
            raise HTTPException(400, "Move failed")
        
        return {"status": "ok"}
    finally:
        db.close()


# =============================================================================
# Album Item Management
# =============================================================================

class AlbumItemsInput(BaseModel):
    item_ids: List[str]


@router.post("/api/albums/{album_id}/items")
def add_items_to_album(album_id: str, data: AlbumItemsInput, request: Request):
    """Add items to album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        count = album_service.add_items(album_id, data.item_ids, user["id"])
        
        return {"status": "ok", "added": count}
    finally:
        db.close()


@router.delete("/api/albums/{album_id}/items")
def remove_items_from_album(album_id: str, data: AlbumItemsInput, request: Request):
    """Remove items from album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        count = album_service.remove_items(album_id, data.item_ids, user["id"])
        
        return {"status": "ok", "removed": count}
    finally:
        db.close()


class AlbumReorderInput(BaseModel):
    item_ids: List[str] = None  # New order
    photo_ids: List[str] = None  # Legacy alias for backward compatibility


@router.put("/api/albums/{album_id}/reorder")
def reorder_album_items(album_id: str, data: AlbumReorderInput, request: Request):
    """Reorder items in album."""
    user = require_user(request)
    
    # Support both new (item_ids) and legacy (photo_ids) formats
    item_ids = data.item_ids or data.photo_ids or []
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        success = album_service.reorder_items(
            album_id, item_ids, user["id"]
        )
        if not success:
            raise HTTPException(400, "Reorder failed")
        
        return {"status": "ok"}
    finally:
        db.close()


class AlbumCoverInput(BaseModel):
    item_id: Optional[str] = None


@router.put("/api/albums/{album_id}/cover")
def set_album_cover(album_id: str, data: AlbumCoverInput, request: Request):
    """Set album cover item."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        success = album_service.set_cover(
            album_id, data.item_id, user["id"]
        )
        if not success:
            raise HTTPException(400, "Failed to set cover")
        
        return {"status": "ok"}
    finally:
        db.close()
