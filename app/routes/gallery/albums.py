"""Album routes - CRUD operations, reordering, cover photos."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from .deps import get_permission_service, get_album_service
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import AlbumRepository, ItemRepository

router = APIRouter()


class AlbumPhotosInput(BaseModel):
    photo_ids: list[str]


class AlbumCoverInput(BaseModel):
    photo_id: str | None = None


class AlbumCreate(BaseModel):
    name: str
    folder_id: str
    photo_ids: list[str] | None = None


@router.post("/api/albums")
def create_album_endpoint(data: AlbumCreate, request: Request):
    """Create a new album with photos."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        album_result = service.create_album(
            name=data.name,
            folder_id=data.folder_id,
            user_id=user["id"]
        )
        album_id = album_result['id']
        # Add photos if provided
        if data.photo_ids:
            service.add_items(album_id, data.photo_ids, user["id"])
        return {"status": "ok", "album": {"id": album_id}}
    finally:
        db.close()


@router.get("/api/albums/{album_id}")
def get_album_data(album_id: str, request: Request):
    """Get album data with photo list."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        album_repo = AlbumRepository(db)
        
        if not perm_service.can_access_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        album = album_repo.get_by_id(album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        items = album_repo.get_items(album_id)

        return {
            "id": album["id"],
            "name": album["name"],
            "created_at": album["created_at"],
            "cover_item_id": album.get("cover_item_id"),
            "can_edit": perm_service.can_edit_album(album_id, user["id"]),
            "items": [{"id": i["id"], "title": i.get("title", ""), "media_type": i.get("media_type", "image")} for i in items]
        }
    finally:
        db.close()


@router.get("/api/albums/{album_id}/available-photos")
def get_available_photos(album_id: str, request: Request):
    """Get photos from folder that can be added to album."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        album_repo = AlbumRepository(db)
        item_repo = ItemRepository(db)
        
        if not perm_service.can_access_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if not perm_service.can_edit_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot edit this album")

        # Get album to find its folder
        album = album_repo.get_by_id(album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        # Get standalone items in the same folder (not already in albums)
        folder_id = album.get("folder_id")
        if not folder_id:
            return {"items": []}
        
        items = item_repo.get_by_folder(folder_id, item_type='media')
        # Filter out items already in albums
        album_item_ids = {ai["item_id"] for ai in album_repo.get_items(album_id)}
        available = [i for i in items if i["id"] not in album_item_ids]
        
        return {"items": available}
    finally:
        db.close()


@router.post("/api/albums/{album_id}/photos")
def add_photos_to_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Add photos to album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        added = service.add_items(album_id, data.photo_ids, user["id"])
        return {"status": "ok", "added": added}
    finally:
        db.close()


@router.delete("/api/albums/{album_id}/photos")
def remove_photos_from_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Remove photos from album. Photos stay in folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        removed = service.remove_items(album_id, data.photo_ids, user["id"])
        return {"status": "ok", "removed": removed}
    finally:
        db.close()


@router.put("/api/albums/{album_id}/reorder")
def reorder_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Reorder photos in album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        service.reorder_items(album_id, data.photo_ids)
        return {"status": "ok"}
    finally:
        db.close()


@router.put("/api/albums/{album_id}/cover")
def set_album_cover_endpoint(album_id: str, data: AlbumCoverInput, request: Request):
    """Set album cover photo. Pass null photo_id to reset to default."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        service.set_cover(album_id, data.photo_id, user["id"])
        return {"status": "ok"}
    finally:
        db.close()


class AlbumMoveInput(BaseModel):
    folder_id: str


@router.put("/api/albums/{album_id}/move")
def move_album_endpoint(album_id: str, data: AlbumMoveInput, request: Request):
    """Move an album and all its photos to another folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_album_service(db)
        service.move_album(album_id, data.folder_id, user["id"])
        return {"status": "ok"}
    finally:
        db.close()
