"""Upload routes - single photo, album, and bulk upload."""
import json

from fastapi import APIRouter, Request, UploadFile, HTTPException, Form

from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import PhotoRepository, SafeRepository
from ...infrastructure.services.encryption import dek_cache
from .deps import get_permission_service, get_upload_service

router = APIRouter()


@router.post("/upload")
async def upload_photo(
    request: Request, 
    file: UploadFile = None, 
    folder_id: str = Form(None),
    encrypted_ck: str = Form(None),
    thumbnail: UploadFile = None,
    thumb_width: int = Form(0),
    thumb_height: int = Form(0)
):
    """Upload new photo or video to specified folder."""
    user = require_user(request)

    if not file:
        raise HTTPException(status_code=400, detail="file is required")

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        safe_repo = SafeRepository(db)
        
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot upload to this folder")

        folder_safe_id = safe_repo.get_safe_id_for_folder(folder_id)
        is_safe = False
        if folder_safe_id:
            if not safe_repo.is_unlocked(folder_safe_id, user["id"]):
                raise HTTPException(status_code=403, detail="Safe is locked. Please unlock first.")
            is_safe = True
            
            if not encrypted_ck:
                raise HTTPException(status_code=400, detail="Client-side encryption required for safe uploads.")

        dek = dek_cache.get(user["id"])

        service = get_upload_service(db)
        result = await service.upload_single(
            file=file,
            folder_id=folder_id,
            user_id=user["id"],
            user_dek=dek,
            is_safe=is_safe,
            client_thumbnail=thumbnail,
            thumb_dimensions=(thumb_width, thumb_height)
        )
        
        if folder_safe_id:
            photo_repo = PhotoRepository(db)
            photo_repo.update(result["id"], safe_id=folder_safe_id)
        
        return result
    finally:
        db.close()


@router.post("/upload-album")
async def upload_album(request: Request, files: list[UploadFile], folder_id: str = Form(None)):
    """Upload multiple photos/videos as an album to specified folder."""
    user = require_user(request)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        safe_repo = SafeRepository(db)
        
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot upload to this folder")

        folder_safe_id = safe_repo.get_safe_id_for_folder(folder_id)
        if folder_safe_id:
            raise HTTPException(status_code=400, detail="Albums are not supported in safes.")

        dek = dek_cache.get(user["id"])

        service = get_upload_service(db)
        form_data = await request.form()
        album_name = form_data.get("album_name", "Untitled Album")
        
        result = await service.upload_album(
            files=files,
            folder_id=folder_id,
            user_id=user["id"],
            user_dek=dek,
            album_name=album_name
        )
        
        return result
    finally:
        db.close()


@router.post("/upload-bulk")
async def upload_bulk(
    request: Request,
    files: list[UploadFile],
    paths: str = Form(...),
    folder_id: str = Form(...)
):
    """Upload folder structure with files."""
    user = require_user(request)

    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required")

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        safe_repo = SafeRepository(db)
        
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot upload to this folder")

        folder_safe_id = safe_repo.get_safe_id_for_folder(folder_id)
        if folder_safe_id:
            raise HTTPException(status_code=400, detail="Bulk upload is not supported in safes.")

        try:
            file_paths = json.loads(paths)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid paths format")

        dek = dek_cache.get(user["id"])

        service = get_upload_service(db)
        result = await service.upload_bulk(
            files=files,
            paths=file_paths,
            folder_id=folder_id,
            user_id=user["id"],
            user_dek=dek
        )
        
        return result
    finally:
        db.close()
