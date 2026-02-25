"""Album routes - CRUD operations, reordering, cover photos."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import PhotoRepository
from .deps import get_permission_service, get_photo_service

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
        service = get_photo_service(db)
        album = service.create_album(
            name=data.name,
            folder_id=data.folder_id,
            photo_ids=data.photo_ids or [],
            user_id=user["id"]
        )
        return {"status": "ok", "album": album}
    finally:
        db.close()


@router.get("/api/albums/{album_id}")
def get_album_data(album_id: str, request: Request):
    """Get album data with photo list."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        album = photo_repo.get_album(album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        photos = photo_repo.get_album_photos(album_id)

        return {
            "id": album["id"],
            "name": album["name"],
            "created_at": album["created_at"],
            "cover_photo_id": album.get("cover_photo_id"),
            "effective_cover_photo_id": album.get("effective_cover_photo_id"),
            "can_edit": perm_service.can_edit_album(album_id, user["id"]),
            "photos": [{"id": p["id"], "filename": p["filename"], "media_type": p["media_type"] or "image"} for p in photos]
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
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if not perm_service.can_edit_album(album_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot edit this album")

        photos = photo_repo.get_available_for_album(album_id, user["id"])
        return {"photos": photos}
    finally:
        db.close()


@router.post("/api/albums/{album_id}/photos")
def add_photos_to_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Add photos to album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_photo_service(db)
        added = service.add_photos_to_album(album_id, data.photo_ids, user["id"])
        return {"status": "ok", "added": added}
    finally:
        db.close()


@router.delete("/api/albums/{album_id}/photos")
def remove_photos_from_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Remove photos from album. Photos stay in folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_photo_service(db)
        removed = service.remove_photos_from_album(album_id, data.photo_ids, user["id"])
        return {"status": "ok", "removed": removed}
    finally:
        db.close()


@router.put("/api/albums/{album_id}/reorder")
def reorder_album_endpoint(album_id: str, data: AlbumPhotosInput, request: Request):
    """Reorder photos in album."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_photo_service(db)
        return service.reorder_album_photos(album_id, data.photo_ids, user["id"])
    finally:
        db.close()


@router.put("/api/albums/{album_id}/cover")
def set_album_cover_endpoint(album_id: str, data: AlbumCoverInput, request: Request):
    """Set album cover photo. Pass null photo_id to reset to default."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_photo_service(db)
        return service.set_album_cover(album_id, data.photo_id, user["id"])
    finally:
        db.close()


@router.put("/api/albums/{album_id}/move")
def move_album_endpoint(album_id: str, request: Request):
    """Move an album and all its photos to another folder."""
    from pydantic import BaseModel
    class MoveInput(BaseModel):
        folder_id: str
    
    user = require_user(request)
    
    db = create_connection()
    try:
        import json
        body = json.loads(request.scope.get('body', b'{}') or b'{}')
        folder_id = body.get('folder_id')
        if not folder_id:
            raise HTTPException(status_code=400, detail="folder_id required")
        
        service = get_photo_service(db)
        return service.move_album(album_id, folder_id, user["id"])
    finally:
        db.close()
