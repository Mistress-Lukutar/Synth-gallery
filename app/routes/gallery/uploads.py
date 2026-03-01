"""Upload routes - unified upload handling for all media types.

Uses ItemService to create polymorphic items instead of photos directly.
"""
import os
import uuid
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from typing import Optional

from ...config import UPLOADS_DIR, ALLOWED_MEDIA_TYPES
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import ItemRepository, ItemMediaRepository
from ...infrastructure.storage import get_storage
from ...infrastructure.services.media import create_thumbnail_bytes, create_video_thumbnail_bytes
from ...application.services import ItemService

router = APIRouter()


def get_item_service(db) -> ItemService:
    """Get configured ItemService."""
    return ItemService(
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db)
    )


async def _process_upload(
    file: UploadFile,
    folder_id: str,
    safe_id: Optional[str],
    user: dict,
    is_encrypted: bool = False,
    client_encryption_metadata: Optional[str] = None
) -> dict:
    """Process single file upload.
    
    Creates Item + ItemMedia instead of direct Photo record.
    """
    db = create_connection()
    try:
        item_service = get_item_service(db)
        storage = get_storage()
        
        # Validate file
        if not file.filename:
            raise HTTPException(400, "No filename")
        
        # Validate content type (skip for E2E encrypted - client handles validation)
        if not is_encrypted and file.content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(400, f"Invalid file type: {file.content_type}")
        
        # Generate IDs
        item_id = str(uuid.uuid4())
        filename = f"{item_id}_{file.filename}"
        
        # Read content
        content = await file.read()
        size = len(content)
        
        if size == 0:
            raise HTTPException(400, "Empty file")
        
        # Determine media type
        media_type = item_service.detect_media_type(file.filename, content)
        
        # Generate thumbnail
        thumb_w, thumb_h = 0, 0
        thumb_bytes = None
        if media_type == 'image':
            try:
                thumb_bytes, thumb_w, thumb_h = create_thumbnail_bytes(content)
            except Exception:
                pass
        elif media_type == 'video':
            try:
                thumb_bytes, thumb_w, thumb_h = create_video_thumbnail_bytes(content)
            except Exception:
                pass
        
        # Upload to storage
        upload_path = f"uploads/{filename}"
        await storage.upload(item_id, content, folder="uploads")
        
        # Upload thumbnail if generated
        if thumb_bytes:
            await storage.upload(item_id, thumb_bytes, folder="thumbnails")
        
        # Create Item + ItemMedia (sync version for non-async context)
        item = item_service.create_media_item_sync(
            item_id=item_id,
            file_data={
                "filename": file.filename,
                "content_type": file.content_type or "application/octet-stream",
                "size": size,
                "uploaded_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "user_id": user["id"],
                "is_encrypted": is_encrypted,
            },
            media_data={
                "media_type": media_type,
                "storage_path": upload_path,
                "is_encrypted": is_encrypted,
                "client_encryption_metadata": client_encryption_metadata,
                "thumb_width": thumb_w,
                "thumb_height": thumb_h,
            },
            folder_id=folder_id,
            safe_id=safe_id,
            user_id=user["id"]
        )
        
        # Also create legacy photo record for backward compatibility (Phase 3 migration)
        try:
            from ...infrastructure.repositories import PhotoRepository
            photo_repo = PhotoRepository(db)
            photo_repo.create(
                filename=item_id,
                original_name=file.filename,
                folder_id=folder_id,
                user_id=user["id"],
                media_type=media_type,
                is_encrypted=is_encrypted,
                safe_id=safe_id,
                thumb_width=thumb_w,
                thumb_height=thumb_h
            )
        except Exception as e:
            # Log but don't fail if legacy creation fails
            print(f"[upload] Legacy photo creation failed: {e}")
        
        return item
    finally:
        db.close()


@router.post("/api/uploads")
@router.post("/upload")  # Legacy endpoint for backward compatibility
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str = Form(...),
    safe_id: Optional[str] = Form(None),
    is_encrypted: bool = Form(False),
    encryption_metadata: Optional[str] = Form(None)
):
    """Upload single file.
    
    Creates Item record with type='media' and associated ItemMedia.
    Legacy endpoint returns backward-compatible response format.
    """
    user = require_user(request)
    
    # Check permissions
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(403, "Cannot upload to this folder")
    finally:
        db.close()
    
    item = await _process_upload(
        file=file,
        folder_id=folder_id,
        safe_id=safe_id,
        user=user,
        is_encrypted=is_encrypted,
        client_encryption_metadata=encryption_metadata
    )
    
    # Legacy response format for backward compatibility
    # Note: filename returns item_id for extension-less storage compatibility
    return {
        "id": item["id"],
        "filename": item["id"],  # Extension-less storage: filename is UUID
        "original_name": item.get("title", ""),  # Original filename
        "media_type": item.get("media_type", "image"),
        "status": "ok",
        "item": item  # New format also included
    }


@router.post("/api/uploads/batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    safe_id: Optional[str] = Form(None),
    is_encrypted: bool = Form(False),
    encryption_metadata: Optional[str] = Form(None)
):
    """Upload multiple files.
    
    Creates Item records for each file.
    """
    user = require_user(request)
    
    # Check permissions once
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(403, "Cannot upload to this folder")
    finally:
        db.close()
    
    results = []
    errors = []
    
    for idx, file in enumerate(files):
        try:
            item = await _process_upload(
                file=file,
                folder_id=folder_id,
                safe_id=safe_id,
                user=user,
                is_encrypted=is_encrypted,
                client_encryption_metadata=encryption_metadata if idx == 0 else None
            )
            results.append(item)
        except Exception as e:
            errors.append({
                "filename": file.filename,
                "error": str(e)
            })
    
    return {
        "status": "ok" if not errors else "partial",
        "items": results,
        "errors": errors,
        "total": len(files),
        "successful": len(results),
        "failed": len(errors)
    }


@router.post("/api/uploads/chunk")
async def upload_chunk(
    request: Request,
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    folder_id: str = Form(...),
    filename: str = Form(...),
    safe_id: Optional[str] = Form(None),
    is_encrypted: bool = Form(False)
):
    """Upload file chunk for resumable uploads.
    
    Stores chunks in temporary location, assembles on last chunk.
    """
    user = require_user(request)
    
    # Check permissions
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(403, "Cannot upload to this folder")
    finally:
        db.close()
    
    # Store chunk
    chunk_dir = os.path.join(UPLOADS_DIR, "chunks", upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    
    chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index}")
    content = await chunk.read()
    
    with open(chunk_path, "wb") as f:
        f.write(content)
    
    # Check if all chunks received
    received = len([f for f in os.listdir(chunk_dir) if f.startswith("chunk_")])
    
    if received >= total_chunks:
        # Assemble file
        item_id = str(uuid.uuid4())
        final_path = os.path.join(UPLOADS_DIR, f"{item_id}_{filename}")
        
        with open(final_path, "wb") as outfile:
            for i in range(total_chunks):
                chunk_file = os.path.join(chunk_dir, f"chunk_{i}")
                with open(chunk_file, "rb") as infile:
                    outfile.write(infile.read())
        
        # Clean up chunks
        import shutil
        shutil.rmtree(chunk_dir)
        
        # Read assembled file
        with open(final_path, "rb") as f:
            assembled_content = f.read()
        
        size = len(assembled_content)
        
        # Create Item + ItemMedia
        db = create_connection()
        try:
            item_service = get_item_service(db)
            storage = get_storage()
            
            # Upload to storage
            await storage.upload(item_id, assembled_content, folder="uploads")
            
            # Determine media type
            media_type = item_service.detect_media_type(filename, assembled_content)
            
            item = item_service.create_media_item_sync(
                item_id=item_id,
                file_data={
                    "filename": filename,
                    "content_type": chunk.content_type or "application/octet-stream",
                    "size": size,
                    "uploaded_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "user_id": user["id"],
                    "is_encrypted": is_encrypted,
                },
                media_data={
                    "media_type": media_type,
                    "storage_path": f"uploads/{item_id}_{filename}",
                    "is_encrypted": is_encrypted,
                },
                folder_id=folder_id,
                safe_id=safe_id,
                user_id=user["id"]
            )
            
            return {
                "status": "ok",
                "item": item,
                "complete": True
            }
        finally:
            db.close()
    
    return {
        "status": "ok",
        "chunk": chunk_index,
        "received": received,
        "total": total_chunks,
        "complete": False
    }


@router.post("/upload-album")
async def upload_album(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    album_name: str = Form(...),
    safe_id: Optional[str] = Form(None)
):
    """Upload multiple files as an album (legacy endpoint for backward compatibility).
    
    Creates items and an album containing them.
    """
    user = require_user(request)
    
    # Check permissions
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(403, "Cannot upload to this folder")
    finally:
        db.close()
    
    # Upload all files
    item_ids = []
    for file in files:
        try:
            item = await _process_upload(
                file=file,
                folder_id=folder_id,
                safe_id=safe_id,
                user=user
            )
            item_ids.append(item["id"])
        except Exception as e:
            print(f"[upload-album] Failed to upload {file.filename}: {e}")
    
    # Create album with uploaded items
    db = create_connection()
    try:
        from ...infrastructure.repositories import AlbumRepository
        album_repo = AlbumRepository(db)
        
        album_id = album_repo.create(
            folder_id=folder_id,
            user_id=user["id"],
            name=album_name,
            safe_id=safe_id
        )
        
        # Add items to album
        for position, item_id in enumerate(item_ids):
            album_repo.add_item(album_id, item_id, position)
        
        return {
            "status": "ok",
            "album_id": album_id,
            "photo_count": len(item_ids),
            "item_count": len(item_ids)
        }
    finally:
        db.close()
