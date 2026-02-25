"""Photo routes - CRUD operations, dimensions, and metadata."""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ...config import UPLOADS_DIR, THUMBNAILS_DIR
from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import PhotoRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from .deps import get_permission_service


def _copy_and_reencrypt_file(
    old_path: Path,
    new_path: Path,
    is_encrypted: bool,
    source_owner_id: int,
    dest_owner_id: int
) -> bool:
    """Copy file, re-encrypting if needed when owner changes."""
    if not old_path.exists():
        return False

    # If not encrypted or same owner - just copy
    if not is_encrypted or source_owner_id == dest_owner_id:
        shutil.copy2(old_path, new_path)
        return True

    # Need to re-encrypt: decrypt with source DEK, encrypt with dest DEK
    source_dek = dek_cache.get(source_owner_id)
    dest_dek = dek_cache.get(dest_owner_id)

    if not source_dek or not dest_dek:
        return False

    # Read and decrypt
    encrypted_data = old_path.read_bytes()
    try:
        plaintext = EncryptionService.decrypt_file(encrypted_data, source_dek)
    except Exception:
        return False

    # Re-encrypt with destination owner's key
    new_encrypted = EncryptionService.encrypt_file(plaintext, dest_dek)
    new_path.write_bytes(new_encrypted)
    return True

router = APIRouter()


@router.get("/api/photos/{photo_id}")
def get_photo_data(photo_id: str, request: Request):
    """Get photo data for lightbox viewer."""
    user = require_user(request)
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        photo = photo_repo.get_by_id(photo_id)

        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")

        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        tags = db.execute("""
            SELECT t.id, t.tag, t.category_id, c.color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.photo_id = ?
        """, (photo_id,)).fetchall()

        album_info = None
        if photo["album_id"]:
            album = photo_repo.get_album(photo["album_id"])
            if album:
                album_photos = photo_repo.get_album_photos(photo["album_id"])
                photo_ids = [p["id"] for p in album_photos]
                current_index = photo_ids.index(photo_id) if photo_id in photo_ids else 0
                album_info = {
                    "id": album["id"],
                    "name": album["name"],
                    "total": len(photo_ids),
                    "current": current_index + 1,
                    "photo_ids": photo_ids,
                    "can_edit": perm_service.can_edit_album(photo["album_id"], user["id"])
                }

        return {
            "id": photo["id"],
            "filename": photo["filename"],
            "original_name": photo["original_name"],
            "media_type": photo["media_type"] or "image",
            "uploaded_at": photo["uploaded_at"],
            "taken_at": photo["taken_at"],
            "tags": [{"id": t["id"], "tag": t["tag"], "color": t["color"] or "#6b7280"} for t in tags],
            "album": album_info,
            "safe_id": photo["safe_id"]
        }
    finally:
        db.close()


class ThumbnailDimensionsInput(BaseModel):
    width: int
    height: int


@router.put("/api/photos/{photo_id}/dimensions")
async def update_dimensions(photo_id: str, data: ThumbnailDimensionsInput, request: Request):
    """Update thumbnail dimensions for a photo."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_access_photo(photo_id, user["id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        if data.width < 1 or data.height < 1 or data.width > 1000 or data.height > 1000:
            raise HTTPException(status_code=400, detail="Invalid dimensions")

        photo_repo.update_thumbnail_dimensions(photo_id, data.width, data.height)
        return {"status": "ok"}
    finally:
        db.close()


class MoveInput(BaseModel):
    folder_id: str


class BatchMoveInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []
    folder_id: str


@router.put("/api/photos/{photo_id}/move")
def move_photo_endpoint(photo_id: str, data: MoveInput, request: Request):
    """Move a standalone photo to another folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        from .deps import get_photo_service
        service = get_photo_service(db)
        return service.move_photo(photo_id, data.folder_id, user["id"])
    finally:
        db.close()


class BatchDeleteInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


@router.post("/api/photos/batch-delete")
async def batch_delete_photos(data: BatchDeleteInput, request: Request):
    """Delete multiple photos and albums."""
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        from .deps import get_upload_service
        service = get_upload_service(db)
        
        deleted_photos = 0
        deleted_albums = 0
        skipped_photos = 0
        skipped_albums = 0

        for photo_id in data.photo_ids:
            if not perm_service.can_delete_photo(photo_id, user["id"]):
                skipped_photos += 1
                continue

            if await service.delete_photo(photo_id):
                deleted_photos += 1
            else:
                skipped_photos += 1

        for album_id in data.album_ids:
            if not perm_service.can_delete_album(album_id, user["id"]):
                skipped_albums += 1
                continue

            await service.delete_album(album_id)
            deleted_albums += 1

        return {
            "status": "ok",
            "deleted_photos": deleted_photos,
            "deleted_albums": deleted_albums,
            "skipped_photos": skipped_photos,
            "skipped_albums": skipped_albums
        }
    finally:
        db.close()


@router.put("/api/items/move")
def batch_move_items(data: BatchMoveInput, request: Request):
    """Move multiple photos and albums to another folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        from .deps import get_photo_service
        service = get_photo_service(db)
        return service.batch_move(
            photo_ids=data.photo_ids,
            album_ids=data.album_ids,
            dest_folder_id=data.folder_id,
            user_id=user["id"]
        )
    finally:
        db.close()


@router.post("/api/items/copy")
def batch_copy_items(data: BatchMoveInput, request: Request):
    """Copy multiple photos and albums to another folder."""
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        photo_repo = PhotoRepository(db)
        
        if not perm_service.can_edit(data.folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot copy to this folder")

        copied_photos = 0
        copied_albums = 0
        skipped_photos = 0
        skipped_albums = 0

        # Copy individual photos
        for photo_id in data.photo_ids:
            photo = photo_repo.get_by_id(photo_id)
            if not photo:
                skipped_photos += 1
                continue

            if not perm_service.can_access_photo(photo_id, user["id"]):
                skipped_photos += 1
                continue

            source_owner_id = photo["user_id"]
            is_encrypted = photo["is_encrypted"]

            if is_encrypted and source_owner_id != user["id"]:
                if not dek_cache.get(source_owner_id) or not user_dek:
                    skipped_photos += 1
                    continue

            new_photo_id = str(uuid.uuid4())
            old_filename = photo["filename"]
            ext = Path(old_filename).suffix
            new_filename = f"{new_photo_id}{ext}"

            old_upload = UPLOADS_DIR / old_filename
            new_upload = UPLOADS_DIR / new_filename
            old_thumb = THUMBNAILS_DIR / f"{Path(old_filename).stem}.jpg"
            new_thumb = THUMBNAILS_DIR / f"{new_photo_id}.jpg"

            try:
                if not _copy_and_reencrypt_file(
                    old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
                ):
                    skipped_photos += 1
                    continue

                if old_thumb.exists():
                    _copy_and_reencrypt_file(
                        old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                    )

                db.execute(
                    """INSERT INTO photos (id, filename, original_name, media_type, folder_id, user_id,
                                         taken_at, is_encrypted, thumb_width, thumb_height)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (new_photo_id, new_filename, photo["original_name"], photo["media_type"],
                     data.folder_id, user["id"], photo["taken_at"], is_encrypted,
                     photo["thumb_width"], photo["thumb_height"])
                )

                # Copy tags
                tags = db.execute(
                    "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                    (photo_id,)
                ).fetchall()
                for tag in tags:
                    db.execute(
                        "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                        (new_photo_id, tag["tag"], tag["category_id"], tag["confidence"])
                    )

                db.commit()
                copied_photos += 1
            except Exception:
                if new_upload.exists():
                    new_upload.unlink()
                if new_thumb.exists():
                    new_thumb.unlink()
                skipped_photos += 1

        # Copy albums
        for album_id in data.album_ids:
            album = photo_repo.get_album(album_id)
            if not album:
                skipped_albums += 1
                continue

            if not perm_service.can_access_album(album_id, user["id"]):
                skipped_albums += 1
                continue

            album_photos = photo_repo.get_album_photos(album_id)
            if not album_photos:
                skipped_albums += 1
                continue

            # Check encryption
            for photo in album_photos:
                if photo["is_encrypted"] and photo["user_id"] != user["id"]:
                    if not dek_cache.get(photo["user_id"]) or not user_dek:
                        skipped_albums += 1
                        continue

            new_album_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO albums (id, name, folder_id, user_id) VALUES (?, ?, ?, ?)",
                (new_album_id, album["name"], data.folder_id, user["id"])
            )

            new_cover_id = None
            for photo in album_photos:
                new_photo_id = str(uuid.uuid4())
                old_filename = photo["filename"]
                ext = Path(old_filename).suffix
                new_filename = f"{new_photo_id}{ext}"

                old_upload = UPLOADS_DIR / old_filename
                new_upload = UPLOADS_DIR / new_filename
                old_thumb = THUMBNAILS_DIR / f"{Path(old_filename).stem}.jpg"
                new_thumb = THUMBNAILS_DIR / f"{new_photo_id}.jpg"

                source_owner_id = photo["user_id"]
                is_encrypted = photo["is_encrypted"]

                try:
                    if not _copy_and_reencrypt_file(
                        old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
                    ):
                        continue

                    if old_thumb.exists():
                        _copy_and_reencrypt_file(
                            old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                        )

                    db.execute(
                        """INSERT INTO photos (id, filename, original_name, album_id, position,
                                             media_type, folder_id, user_id, taken_at, is_encrypted,
                                             thumb_width, thumb_height)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (new_photo_id, new_filename, photo["original_name"], new_album_id,
                         photo["position"], photo["media_type"], data.folder_id, user["id"],
                         photo["taken_at"], is_encrypted, photo["thumb_width"],
                         photo["thumb_height"])
                    )

                    # Copy tags
                    tags = db.execute(
                        "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                        (photo["id"],)
                    ).fetchall()
                    for tag in tags:
                        db.execute(
                            "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                            (new_photo_id, tag["tag"], tag["category_id"], tag["confidence"])
                        )

                    if photo["id"] == album["cover_photo_id"]:
                        new_cover_id = new_photo_id

                except Exception:
                    if new_upload.exists():
                        new_upload.unlink()
                    if new_thumb.exists():
                        new_thumb.unlink()

            # Update album cover
            if new_cover_id:
                db.execute(
                    "UPDATE albums SET cover_photo_id = ? WHERE id = ?",
                    (new_cover_id, new_album_id)
                )

            db.commit()
            copied_albums += 1

        return {
            "status": "ok",
            "copied_photos": copied_photos,
            "copied_albums": copied_albums,
            "skipped_photos": skipped_photos,
            "skipped_albums": skipped_albums
        }
    finally:
        db.close()


class BatchDownloadInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


@router.post("/api/photos/batch-download")
async def batch_download(data: BatchDownloadInput, request: Request):
    """Download multiple photos and albums as a ZIP file."""
    from datetime import datetime
    from io import BytesIO
    import zipfile
    from fastapi.responses import StreamingResponse
    
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        
        files_to_download = []
        date_folder = datetime.now().strftime("%Y-%m-%d")

        # Process individual photos
        for photo_id in data.photo_ids:
            if not perm_service.can_access_photo(photo_id, user["id"]):
                continue

            photo = db.execute(
                "SELECT id, filename, original_name, is_encrypted, user_id FROM photos WHERE id = ?",
                (photo_id,)
            ).fetchone()

            if photo:
                file_path = UPLOADS_DIR / photo["filename"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{photo['original_name']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        photo["is_encrypted"],
                        photo["user_id"]
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

            album_photos = db.execute(
                """SELECT p.id, p.filename, p.original_name, p.is_encrypted, p.user_id
                   FROM photos p WHERE p.album_id = ? ORDER BY p.position""",
                (album_id,)
            ).fetchall()

            safe_album_name = "".join(c for c in album["name"] if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_album_name:
                safe_album_name = "album"

            for photo in album_photos:
                file_path = UPLOADS_DIR / photo["filename"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{safe_album_name}/{photo['original_name']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        photo["is_encrypted"],
                        photo["user_id"]
                    ))

        if not files_to_download:
            raise HTTPException(status_code=404, detail="No files to download")

        # Single file - return directly
        if len(files_to_download) == 1:
            _, file_path, is_encrypted, owner_id = files_to_download[0]
            
            if is_encrypted:
                owner_dek = dek_cache.get(owner_id) if owner_id else None
                if not owner_dek:
                    raise HTTPException(status_code=403, detail="Encryption key not available")
                
                encrypted_data = file_path.read_bytes()
                try:
                    decrypted_data = EncryptionService.decrypt_file(encrypted_data, owner_dek)
                except Exception:
                    raise HTTPException(status_code=500, detail="Decryption failed")
                
                return StreamingResponse(
                    BytesIO(decrypted_data),
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'}
                )
            
            return FileResponse(file_path)

        # Multiple files - create ZIP
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            seen_names = set()
            for archive_path, file_path, is_encrypted, owner_id in files_to_download:
                # Handle duplicate names
                original_path = archive_path
                counter = 1
                while archive_path in seen_names:
                    path_parts = original_path.rsplit('.', 1)
                    if len(path_parts) == 2:
                        archive_path = f"{path_parts[0]} ({counter}).{path_parts[1]}"
                    else:
                        archive_path = f"{original_path} ({counter})"
                    counter += 1
                seen_names.add(archive_path)

                # Read and decrypt if needed
                if is_encrypted:
                    owner_dek = dek_cache.get(owner_id) if owner_id else None
                    if not owner_dek:
                        continue  # Skip files we can't decrypt
                    
                    encrypted_data = file_path.read_bytes()
                    try:
                        file_data = EncryptionService.decrypt_file(encrypted_data, owner_dek)
                    except Exception:
                        continue
                else:
                    file_data = file_path.read_bytes()

                zf.writestr(archive_path, file_data)

        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{date_folder}.zip"'}
        )
    finally:
        db.close()
