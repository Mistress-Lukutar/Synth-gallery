"""Upload routes - unified upload handling for all media types.

Uses ItemService to create polymorphic items instead of photos directly.
"""
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException

from ...application.services import ItemService
from ...config import UPLOADS_DIR, ALLOWED_MEDIA_TYPES
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import ItemRepository, ItemMediaRepository
from ...infrastructure.services.media import get_media_type
from ...infrastructure.services.metadata import extract_taken_date
from ...logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


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
    client_encryption_metadata: Optional[str] = None,
    thumbnail: Optional[UploadFile] = None,
    thumb_width: int = 0,
    thumb_height: int = 0
) -> dict:
    """Process single file upload.
    
    Delegates to ItemService.process_media_upload for all business logic.
    """
    db = create_connection()
    try:
        item_service = get_item_service(db)
        return await item_service.process_media_upload(
            file=file,
            folder_id=folder_id,
            user_id=user["id"],
            safe_id=safe_id,
            is_encrypted=is_encrypted,
            client_encryption_metadata=client_encryption_metadata,
            thumbnail=thumbnail,
            thumb_width=thumb_width,
            thumb_height=thumb_height
        )
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
    encryption_metadata: Optional[str] = Form(None),
    encrypted_ck: Optional[str] = Form(None),
    thumbnail: Optional[UploadFile] = File(None),
    thumb_width: int = Form(0),
    thumb_height: int = Form(0)
):
    """Upload single file.
    
    Creates Item record with type='media' and associated ItemMedia.
    Legacy endpoint returns backward-compatible response format.
    
    For Safe (E2E encrypted) uploads:
    - Pass encrypted_ck='safe' to skip server-side MIME validation
    - Pass encrypted thumbnail via 'thumbnail' field
    - Client must validate file type before encryption.
    """
    user = require_user(request)
    
    # Detect E2E encrypted upload for safes (client passes encrypted_ck)
    is_e2e_encrypted = is_encrypted or (encrypted_ck is not None and encrypted_ck == 'safe')
    
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
        is_encrypted=is_e2e_encrypted,
        client_encryption_metadata=encryption_metadata,
        thumbnail=thumbnail,
        thumb_width=thumb_width,
        thumb_height=thumb_height
    )
    
    # Clean response structure (use id as filename in extension-less storage)
    return {
        "id": item["id"],
        "type": "media",
        "folder_id": folder_id,
        "media_type": item.get("media_type", "image"),
        "title": item.get("title", ""),
        "filename": item["id"],  # Extension-less: filename = item_id
        "content_type": item.get("content_type"),
        "thumb_width": item.get("thumb_width", 0),
        "thumb_height": item.get("thumb_height", 0),
        "taken_at": item.get("taken_at"),
        "is_encrypted": item.get("is_encrypted", False),
        "status": "ok"
    }


@router.post("/api/uploads/batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    safe_id: Optional[str] = Form(None),
    is_encrypted: bool = Form(False),
    encryption_metadata: Optional[str] = Form(None),
    encrypted_ck: Optional[str] = Form(None)
):
    """Upload multiple files.
    
    Creates Item records for each file.
    
    For Safe (E2E encrypted) uploads, pass encrypted_ck='safe' to skip
    server-side MIME validation (client must validate before encryption).
    """
    user = require_user(request)
    
    # Detect E2E encrypted upload for safes (client passes encrypted_ck)
    is_e2e_encrypted = is_encrypted or (encrypted_ck is not None and encrypted_ck == 'safe')
    
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
                is_encrypted=is_e2e_encrypted,
                client_encryption_metadata=encryption_metadata if idx == 0 else None
            )
            # Clean response structure (use id as filename)
            results.append({
                "id": item["id"],
                "type": "media",
                "folder_id": folder_id,
                "media_type": item.get("media_type", "image"),
                "title": item.get("title", "")
            })
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
        final_path = os.path.join(UPLOADS_DIR, item_id)  # Extension-less storage
        
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
            media_type = get_media_type(chunk.content_type)
            
            # Extract taken_at from EXIF for images
            taken_at = None
            if media_type == 'image':
                try:
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(assembled_content)
                        tmp.flush()
                        taken_at = extract_taken_date(Path(tmp.name))
                except Exception:
                    pass
            
            item = item_service.create_db_records(
                item_id=item_id,
                file_data={
                    "filename": filename,
                    "content_type": chunk.content_type or "application/octet-stream",
                    "size": size,
                    "uploaded_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "user_id": user["id"],
                    "is_encrypted": is_encrypted,
                    "taken_at": taken_at,
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


@router.post("/upload-bulk")
async def upload_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    paths: str = Form(...),
    safe_id: Optional[str] = Form(None),
    encrypted_ck: Optional[str] = Form(None)
):
    """Bulk upload folder structure with files.
    
    Creates subfolders and albums from directory structure.
    Files in root go to target folder, files in subfolders create albums.
    
    For Safe (E2E encrypted) uploads, pass encrypted_ck='safe' to skip
    server-side MIME validation (client must validate before encryption).
    """
    import json
    from ...infrastructure.repositories import FolderRepository, AlbumRepository
    from ...application.services import FolderService
    
    user = require_user(request)
    
    # Detect E2E encrypted upload for safes (client passes encrypted_ck)
    is_e2e_encrypted = encrypted_ck is not None and encrypted_ck == 'safe'
    
    # Parse paths
    try:
        file_paths = json.loads(paths)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid paths JSON")
    
    if len(files) != len(file_paths):
        raise HTTPException(400, "Files and paths count mismatch")
    
    # Check permissions on target folder
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user["id"]):
            raise HTTPException(403, "Cannot upload to this folder")
    finally:
        db.close()
    
    # Group files by their parent directory
    root_files = []  # Files to upload directly to target folder
    album_groups = {}  # folder_name -> list of (file, filename)
    skipped_nested = 0
    
    for file, relative_path in zip(files, file_paths):
        # Normalize path separators
        relative_path = relative_path.replace('\\', '/')
        parts = relative_path.split('/')
        
        if len(parts) == 1:
            # Root level file
            root_files.append((file, parts[0]))
        elif len(parts) == 2:
            # One level deep - create album
            folder_name = parts[0]
            filename = parts[1]
            if folder_name not in album_groups:
                album_groups[folder_name] = []
            album_groups[folder_name].append((file, filename))
        else:
            # Nested too deep - skip
            skipped_nested += 1
    
    # Track results
    individual_photos = 0
    albums_created = 0
    photos_in_albums = 0
    failed = 0
    errors = []
    
    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        album_repo = AlbumRepository(db)
        folder_service = FolderService(folder_repo)
        
        # Upload root level files directly
        for file, filename in root_files:
            try:
                await _process_upload(
                    file=file,
                    folder_id=folder_id,
                    safe_id=safe_id,
                    user=user,
                    is_encrypted=is_e2e_encrypted
                )
                individual_photos += 1
            except Exception as e:
                failed += 1
                errors.append(f"{filename}: {str(e)}")
        
        # Create albums from subfolders
        for album_name, album_files in album_groups.items():
            try:
                # Create subfolder for the album
                subfolder = folder_service.create_folder(
                    name=album_name,
                    user_id=user["id"],
                    parent_id=folder_id,
                    safe_id=safe_id
                )
                
                # Upload files to subfolder
                item_ids = []
                for file, _ in album_files:
                    try:
                        item = await _process_upload(
                            file=file,
                            folder_id=subfolder["id"],
                            safe_id=safe_id,
                            user=user,
                            is_encrypted=is_e2e_encrypted
                        )
                        item_ids.append(item["id"])
                        photos_in_albums += 1
                    except Exception as e:
                        failed += 1
                        errors.append(f"{file.filename}: {str(e)}")
                
                # Create album with uploaded items
                if item_ids:
                    album_id = album_repo.create(
                        folder_id=subfolder["id"],
                        user_id=user["id"],
                        name=album_name,
                        safe_id=safe_id
                    )
                    for position, item_id in enumerate(item_ids):
                        album_repo.add_item(album_id, item_id, position)
                    albums_created += 1
                    
            except Exception as e:
                failed += len(album_files)
                errors.append(f"Album {album_name}: {str(e)}")
    finally:
        db.close()
    
    return {
        "status": "ok" if failed == 0 else "partial",
        "summary": {
            "total_files": len(files),
            "individual_photos": individual_photos,
            "albums_created": albums_created,
            "photos_in_albums": photos_in_albums,
            "failed": failed,
            "skipped_nested": skipped_nested
        },
        "errors": errors if errors else None
    }


@router.post("/upload-album")
async def upload_album(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    album_name: str = Form(""),
    safe_id: Optional[str] = Form(None)
):
    """Upload multiple files as an album (legacy endpoint for backward compatibility).
    
    Creates items and an album containing them.
    """
    user = require_user(request)

    # Check minimum files for album
    if len(files) < 2:
        raise HTTPException(400, "Album requires at least 2 files")

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
        except Exception:
            logger.exception("Failed to upload %s", file.filename)
    
    # Create album with uploaded items
    db = create_connection()
    try:
        from ...infrastructure.repositories import AlbumRepository, ItemRepository, ItemMediaRepository
        from ...application.services import ItemService
        
        album_repo = AlbumRepository(db)
        item_service = ItemService(
            item_repository=ItemRepository(db),
            item_media_repository=ItemMediaRepository(db)
        )
        
        # Generate default album name if not provided
        if not album_name:
            from datetime import datetime
            album_name = f"Album {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        album_id = album_repo.create(
            folder_id=folder_id,
            user_id=user["id"],
            name=album_name,
            safe_id=safe_id
        )
        
        # Add items to album
        for position, item_id in enumerate(item_ids):
            album_repo.add_item(album_id, item_id, position)
        
        # Get uploaded items for response (Phase 5: polymorphic items)
        uploaded_items = []
        for item_id in item_ids:
            item = item_service.get_item(item_id)
            if item:
                uploaded_items.append({
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "media_type": item.get("media_type", "image"),
                    "content_type": item.get("content_type"),
                    "thumb_width": item.get("thumb_width"),
                    "thumb_height": item.get("thumb_height"),
                    "taken_at": item.get("taken_at"),
                    "is_encrypted": item.get("is_encrypted", False),
                })
        
        return {
            "status": "ok",
            "album_id": album_id,
            "photo_count": len(item_ids),
            "item_count": len(item_ids),
            "items": uploaded_items,      # Phase 5: new format
            "photos": uploaded_items      # Legacy alias for backward compatibility
        }
    finally:
        db.close()
