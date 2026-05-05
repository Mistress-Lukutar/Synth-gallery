"""Item routes - unified API for all content types.

Replaces the old photos.py with polymorphic item handling.
"""
import uuid
from pathlib import Path
from typing import Optional, List

from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator

from .deps import get_permission_service, get_album_service
from ...application.services import ItemService, AlbumService
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import (
    ItemRepository, ItemMediaRepository, AlbumRepository, FolderRepository
)
from ...infrastructure.services.encryption import EncryptionService, dek_cache

router = APIRouter()


def get_item_service(db) -> ItemService:
    """Get configured ItemService."""
    return ItemService(
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db)
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


@router.get("/api/items/{item_id}/metadata")
def get_item_metadata(item_id: str, request: Request):
    """Get item metadata for details panel.
    
    Returns combined data from items and item_media tables.
    """
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        
        # Get metadata
        metadata = item_service.get_item_metadata(item_id)
        if not metadata:
            raise HTTPException(404, "Item not found")
        
        # Check access via folder
        perm_service = get_permission_service(db)
        folder_id = metadata.get("folder_id")
        if folder_id and not perm_service.can_access(folder_id, user["id"]):
            raise HTTPException(403, "Access denied")
        
        # Check edit permission
        can_edit = True
        if folder_id:
            can_edit = perm_service.can_edit(folder_id, user["id"])

        # Format response
        return {
            "id": metadata["id"],
            "title": metadata.get("title"),
            "description": metadata.get("description"),
            "type": metadata.get("type"),
            "media_type": metadata.get("media_type"),
            "original_name": metadata.get("original_name"),
            "content_type": metadata.get("content_type"),
            "uploaded_at": metadata.get("uploaded_at"),
            "updated_at": metadata.get("updated_at"),
            "file_size": metadata.get("file_size"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "duration": metadata.get("duration"),
            "taken_at": metadata.get("taken_at"),
            "can_edit": can_edit,
        }
    finally:
        db.close()


class MetadataUpdateInput(BaseModel):
    """Input for metadata update."""
    title: Optional[str] = None
    description: Optional[str] = None
    taken_at: Optional[datetime] = None  # ISO format datetime, validated by Pydantic
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    
    @field_validator('taken_at', mode='before')
    @classmethod
    def parse_iso_datetime(cls, value):
        """Parse ISO 8601 datetime string to datetime object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            # Handle 'Z' suffix (UTC) by replacing with '+00:00'
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("Invalid datetime format. Use ISO 8601 format (e.g., 2024-01-01T12:00:00Z)")
        raise ValueError("taken_at must be a datetime string or None")
    
    @field_validator('width', 'height', 'duration')
    @classmethod
    def validate_positive_int(cls, v):
        """Validate that dimensions and duration are positive integers."""
        if v is None:
            return None
        if v < 0:
            raise ValueError('Value must be a positive integer')
        return v


@router.put("/api/items/{item_id}/metadata")
def update_item_metadata(item_id: str, data: MetadataUpdateInput, request: Request):
    """Update item metadata.
    
    All fields are optional. Only provided fields will be updated.
    Validates that user owns the item.
    """
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        
        # Update metadata (will raise 404 or 403 if applicable)
        # All fields are validated by Pydantic
        result = item_service.update_metadata(
            item_id=item_id,
            user_id=user["id"],
            title=data.title,
            description=data.description,
            taken_at=data.taken_at,
            width=data.width,
            height=data.height,
            duration=data.duration
        )
        
        return result
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
async def delete_item(item_id: str, request: Request):
    """Delete item."""
    user = require_user(request)

    db = create_connection()
    try:
        from .deps import get_permission_service
        perm_service = get_permission_service(db)
        if not perm_service.can_delete_item(item_id, user["id"]):
            raise HTTPException(403, "Cannot delete item")

        item_service = get_item_service(db)
        success = await item_service.delete_item(item_id, user["id"])
        if not success:
            raise HTTPException(400, "Delete failed")

        return {"status": "ok"}
    finally:
        db.close()


# =============================================================================
# Single Item Operations
# =============================================================================

class ItemCopyInput(BaseModel):
    """Input for copying a single item."""
    folder_id: str


@router.post("/api/items/{item_id}/copy")
async def copy_item(item_id: str, data: ItemCopyInput, request: Request):
    """Copy a single item to another folder."""
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_service = ItemService(
            item_repository=ItemRepository(db),
            item_media_repository=ItemMediaRepository(db)
        )
        
        if not perm_service.can_edit(data.folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot copy to this folder")
        
        if not perm_service.can_access_photo(item_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot access item")
        
        item = item_service.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        source_owner_id = item["user_id"]
        is_e2e = item.get("safe_id") is not None
        
        if not is_e2e and source_owner_id != user["id"]:
            if not dek_cache.get(source_owner_id) or not user_dek:
                raise HTTPException(status_code=403, detail="Cannot re-encrypt without DEK")
        
        new_item_id = item_service.copy_item(
            item_id=item_id,
            dest_folder_id=data.folder_id,
            user_id=user["id"],
            source_owner_id=source_owner_id
        )
        
        db.commit()
        
        return {
            "status": "ok",
            "id": new_item_id,
            "original_id": item_id
        }
    finally:
        db.close()


class ItemMoveInput(BaseModel):
    """Input for moving a single item."""
    folder_id: str


@router.put("/api/items/{item_id}/move")
def move_item(item_id: str, data: ItemMoveInput, request: Request):
    """Move a single item to another folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        perm_service = get_permission_service(db)
        
        # Check access to source item
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        if item.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="Not owner")
        
        # Check can edit destination folder
        if not perm_service.can_edit(data.folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        # Perform move
        if item_service.move_item(item_id, data.folder_id, user["id"]):
            return {"status": "ok", "id": item_id}
        else:
            raise HTTPException(status_code=400, detail="Move failed")
    finally:
        db.close()


# =============================================================================
# Batch Download
# =============================================================================

class BatchDownloadInput(BaseModel):
    photo_ids: list[str] = []  # Legacy: item IDs
    album_ids: list[str] = []


@router.post("/api/items/batch-download")
async def batch_download(data: BatchDownloadInput, request: Request):
    """Download multiple items and albums as a ZIP file."""
    from datetime import datetime
    from io import BytesIO
    import zipfile
    from fastapi.responses import StreamingResponse
    from ...config import UPLOADS_DIR
    
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        
        files_to_download = []
        date_folder = datetime.now().strftime("%Y-%m-%d")

        # Process individual items
        for item_id in data.photo_ids:
            if not perm_service.can_access_photo(item_id, user["id"]):
                continue

            # Phase 5: Get from items + item_media tables
            item = db.execute(
                """SELECT i.id, i.title, i.safe_id, i.user_id 
                   FROM items i
                   WHERE i.id = ?""",
                (item_id,)
            ).fetchone()

            if item:
                # Extension-less storage: filename = item_id
                file_path = UPLOADS_DIR / item_id
                if file_path.exists():
                    archive_path = f"{date_folder}/{item['title']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        item["safe_id"] is not None,
                        item["user_id"]
                    ))

        # Process albums
        for album_id in data.album_ids:
            if not perm_service.can_access_album(album_id, user["id"]):
                continue

            album = db.execute(
                "SELECT id, name FROM albums WHERE id = ?",
                (album_id,)
            ).fetchone()

            if not album:
                continue

            # Phase 5: Get items from album via album_items
            album_items = db.execute(
                """SELECT i.id, i.title, i.safe_id, i.user_id
                   FROM items i
                   JOIN album_items ai ON i.id = ai.item_id
                   WHERE ai.album_id = ?
                   ORDER BY ai.position""",
                (album_id,)
            ).fetchall()

            safe_album_name = "".join(c for c in album["name"] if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_album_name:
                safe_album_name = "album"

            for item in album_items:
                # Extension-less storage: filename = item_id
                file_path = UPLOADS_DIR / item["id"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{safe_album_name}/{item['title']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        item["safe_id"] is not None,
                        item["user_id"]
                    ))

        if not files_to_download:
            raise HTTPException(status_code=404, detail="No files to download")

        # Create ZIP file
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for archive_path, file_path, is_e2e, owner_id in files_to_download:
                if is_e2e:
                    # E2E files: include as-is, client decrypts
                    zf.write(file_path, archive_path)
                else:
                    # Server-side encrypted: decrypt before adding to ZIP
                    dek = user_dek if owner_id == user["id"] else dek_cache.get(owner_id)
                    if dek:
                        try:
                            encrypted_data = file_path.read_bytes()
                            plaintext = EncryptionService.decrypt_file(encrypted_data, dek)
                            zf.writestr(archive_path, plaintext)
                        except Exception:
                            continue

        zip_buffer.seek(0)
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=synth-download-{date_folder}.zip"}
        )
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
    """Delete album and all its items including files."""
    user = require_user(request)

    db = create_connection()
    try:
        from .deps import get_permission_service
        perm_service = get_permission_service(db)
        if not perm_service.can_delete_album(album_id, user["id"]):
            raise HTTPException(403, "Cannot delete album")

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


class AlbumCopyInput(BaseModel):
    folder_id: str


@router.post("/api/albums/{album_id}/copy")
def copy_album(album_id: str, data: AlbumCopyInput, request: Request):
    """Copy album and all its items to a different folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        new_album_id = album_service.copy_album(
            album_id, data.folder_id, user["id"]
        )
        
        db.commit()
        
        return {"status": "ok", "album_id": new_album_id}
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
