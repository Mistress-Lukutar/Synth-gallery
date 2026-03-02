"""Item routes - unified API for all content types.

Replaces the old photos.py with polymorphic item handling.
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from ...database import create_connection
from ...dependencies import require_user
from ...infrastructure.repositories import (
    ItemRepository, ItemMediaRepository, AlbumRepository, FolderRepository
)
from ...infrastructure.services.encryption import EncryptionService, dek_cache
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


class BatchMoveInput(BaseModel):
    """Input for batch move operation."""
    photo_ids: List[str] = []  # Legacy: item IDs
    album_ids: List[str] = []
    folder_id: str


@router.put("/api/items/move")
def batch_move_items(data: BatchMoveInput, request: Request):
    """Move multiple items and albums to another folder."""
    user = require_user(request)
    
    db = create_connection()
    try:
        item_service = get_item_service(db)
        return item_service.batch_move(
            item_ids=data.photo_ids,
            album_ids=data.album_ids,
            dest_folder_id=data.folder_id,
            user_id=user["id"]
        )
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


class BatchDeleteInput(BaseModel):
    """Input for batch delete operation."""
    photo_ids: List[str] = []  # Legacy: item IDs
    album_ids: List[str] = []


@router.post("/api/items/batch-delete")
def batch_delete_items(data: BatchDeleteInput, request: Request):
    """Delete multiple items (photos and albums)."""
    user = require_user(request)
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_service = get_item_service(db)
        album_service = get_album_service(db)
        
        deleted_photos = 0
        deleted_albums = 0
        skipped_photos = 0
        skipped_albums = 0

        # Delete items (photos)
        for item_id in data.photo_ids:
            # Check access
            item = item_service.get_item(item_id)
            if not item:
                skipped_photos += 1
                continue
                
            if item.get("user_id") != user["id"]:
                skipped_photos += 1
                continue
            
            if item_service.delete_item(item_id, user["id"]):
                deleted_photos += 1
            else:
                skipped_photos += 1

        # Delete albums
        for album_id in data.album_ids:
            # Check ownership via album service
            album = album_service.get_album(album_id, user["id"])
            if not album:
                skipped_albums += 1
                continue
                
            if album.get("user_id") != user["id"]:
                skipped_albums += 1
                continue
            
            album_service.delete_album(album_id, user["id"])
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


# =============================================================================
# Batch Copy
# =============================================================================

def _copy_and_reencrypt_file(
    old_path: Path,
    new_path: Path,
    is_encrypted: bool,
    source_owner_id: int,
    dest_owner_id: int
) -> bool:
    """Copy file, re-encrypting if needed when owner changes."""
    import shutil
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


@router.post("/api/items/copy")
def batch_copy_items(data: BatchMoveInput, request: Request):
    """Copy multiple items and albums to another folder."""
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_repo = ItemRepository(db)
        album_repo = AlbumRepository(db)
        media_repo = ItemMediaRepository(db)
        
        if not perm_service.can_edit(data.folder_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot copy to this folder")

        copied_items = 0
        copied_albums = 0
        skipped_items = 0
        skipped_albums = 0

        # Copy individual items
        for item_id in data.photo_ids:
            item = item_repo.get_by_id(item_id)
            if not item:
                skipped_items += 1
                continue

            if not perm_service.can_access_photo(item_id, user["id"]):
                skipped_items += 1
                continue

            source_owner_id = item["user_id"]
            is_encrypted = item["is_encrypted"]

            if is_encrypted and source_owner_id != user["id"]:
                if not dek_cache.get(source_owner_id) or not user_dek:
                    skipped_items += 1
                    continue

            # Get media details
            media = media_repo.get_by_item_id(item_id)
            if not media:
                skipped_items += 1
                continue
                
            new_item_id = str(uuid.uuid4())
            old_filename = media["filename"]
            ext = Path(old_filename).suffix if "." in old_filename else ""
            new_filename = f"{new_item_id}{ext}"

            old_upload = UPLOADS_DIR / old_filename
            new_upload = UPLOADS_DIR / new_filename
            old_thumb = THUMBNAILS_DIR / item_id  # Extension-less
            new_thumb = THUMBNAILS_DIR / new_item_id  # Extension-less

            try:
                if not _copy_and_reencrypt_file(
                    old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
                ):
                    skipped_items += 1
                    continue

                if old_thumb.exists():
                    _copy_and_reencrypt_file(
                        old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                    )

                # Create new item
                item_repo.create(
                    item_type='media',
                    folder_id=data.folder_id,
                    user_id=user["id"],
                    item_id=new_item_id,
                    title=item.get("title", media["original_name"]),
                    safe_id=item.get("safe_id"),
                    is_encrypted=is_encrypted
                )
                
                # Create media record
                media_repo.create(
                    item_id=new_item_id,
                    media_type=media["media_type"],
                    filename=new_filename,
                    original_name=media["original_name"],
                    content_type=media["content_type"],
                    thumb_width=media["thumb_width"],
                    thumb_height=media["thumb_height"],
                    taken_at=media["taken_at"]
                )

                # Copy tags
                tags = db.execute(
                    "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                    (item_id,)
                ).fetchall()
                for tag in tags:
                    db.execute(
                        "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                        (new_item_id, tag["tag"], tag["category_id"], tag["confidence"])
                    )

                db.commit()
                copied_items += 1
            except Exception:
                if new_upload.exists():
                    new_upload.unlink()
                if new_thumb.exists():
                    new_thumb.unlink()
                skipped_items += 1

        # Copy albums
        for album_id in data.album_ids:
            album = album_repo.get_by_id(album_id)
            if not album:
                skipped_albums += 1
                continue

            if not perm_service.can_access_album(album_id, user["id"]):
                skipped_albums += 1
                continue
            
            # Get album items
            album_items = album_repo.get_items(album_id)
            if not album_items:
                skipped_albums += 1
                continue

            new_album_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO albums (id, name, folder_id, user_id, safe_id) VALUES (?, ?, ?, ?, ?)",
                (new_album_id, album["name"], data.folder_id, user["id"], album.get("safe_id"))
            )

            for idx, item in enumerate(album_items):
                new_item_id = str(uuid.uuid4())
                old_filename = item.get("filename", "")
                ext = Path(old_filename).suffix if "." in old_filename else ""
                new_filename = f"{new_item_id}{ext}"

                old_upload = UPLOADS_DIR / old_filename
                new_upload = UPLOADS_DIR / new_filename
                old_thumb = THUMBNAILS_DIR / item["id"]
                new_thumb = THUMBNAILS_DIR / new_item_id

                source_owner_id = item["user_id"]
                is_encrypted = item.get("is_encrypted", False)

                try:
                    if not _copy_and_reencrypt_file(
                        old_upload, new_upload, is_encrypted, source_owner_id, user["id"]
                    ):
                        continue

                    if old_thumb.exists():
                        _copy_and_reencrypt_file(
                            old_thumb, new_thumb, is_encrypted, source_owner_id, user["id"]
                        )

                    # Create new item in album via album_items
                    db.execute(
                        """INSERT INTO items (id, type, folder_id, user_id, safe_id, is_encrypted, created_at)
                         VALUES (?, 'media', ?, ?, ?, ?, datetime('now'))""",
                        (new_item_id, data.folder_id, user["id"], 
                         item.get("safe_id"), is_encrypted)
                    )
                    
                    # Create media record
                    db.execute(
                        """INSERT INTO item_media (item_id, media_type, filename, original_name,
                             thumb_width, thumb_height, taken_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (new_item_id, item.get("media_type", "image"), new_filename,
                         item.get("original_name", ""), item.get("thumb_width", 0),
                         item.get("thumb_height", 0), item.get("taken_at"))
                    )

                    # Add to album
                    db.execute(
                        "INSERT INTO album_items (album_id, item_id, position) VALUES (?, ?, ?)",
                        (new_album_id, new_item_id, idx)
                    )

                    # Copy tags
                    tags = db.execute(
                        "SELECT tag, category_id, confidence FROM tags WHERE photo_id = ?",
                        (item["id"],)
                    ).fetchall()
                    for tag in tags:
                        db.execute(
                            "INSERT INTO tags (photo_id, tag, category_id, confidence) VALUES (?, ?, ?, ?)",
                            (new_item_id, tag["tag"], tag["category_id"], tag["confidence"])
                        )

                    if item["id"] == album.get("cover_item_id"):
                        db.execute(
                            "UPDATE albums SET cover_item_id = ? WHERE id = ?",
                            (new_item_id, new_album_id)
                        )
                except Exception:
                    continue

            db.commit()
            copied_albums += 1

        return {
            "status": "ok",
            "copied_photos": copied_items,
            "copied_albums": copied_albums,
            "skipped_photos": skipped_items,
            "skipped_albums": skipped_albums
        }
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
                """SELECT i.id, im.filename, im.original_name, i.is_encrypted, i.user_id 
                   FROM items i
                   JOIN item_media im ON i.id = im.item_id
                   WHERE i.id = ?""",
                (item_id,)
            ).fetchone()

            if item:
                file_path = UPLOADS_DIR / item["filename"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{item['original_name']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        item["is_encrypted"],
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
                """SELECT i.id, im.filename, im.original_name, i.is_encrypted, i.user_id
                   FROM items i
                   JOIN item_media im ON i.id = im.item_id
                   JOIN album_items ai ON i.id = ai.item_id
                   WHERE ai.album_id = ?
                   ORDER BY ai.position""",
                (album_id,)
            ).fetchall()

            safe_album_name = "".join(c for c in album["name"] if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_album_name:
                safe_album_name = "album"

            for item in album_items:
                file_path = UPLOADS_DIR / item["filename"]
                if file_path.exists():
                    archive_path = f"{date_folder}/{safe_album_name}/{item['original_name']}"
                    files_to_download.append((
                        archive_path,
                        file_path,
                        item["is_encrypted"],
                        item["user_id"]
                    ))

        if not files_to_download:
            raise HTTPException(status_code=404, detail="No files to download")

        # Create ZIP file
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for archive_path, file_path, is_encrypted, owner_id in files_to_download:
                if is_encrypted:
                    # Need to decrypt before adding to ZIP
                    dek = user_dek if owner_id == user["id"] else dek_cache.get(owner_id)
                    if dek:
                        try:
                            encrypted_data = file_path.read_bytes()
                            plaintext = EncryptionService.decrypt_file(encrypted_data, dek)
                            zf.writestr(archive_path, plaintext)
                        except Exception:
                            continue
                else:
                    zf.write(file_path, archive_path)

        zip_buffer.seek(0)
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=synth-download-{date_folder}.zip"}
        )
    finally:
        db.close()


# =============================================================================
# Item Tag Endpoints
# =============================================================================

class TagInput(BaseModel):
    tag: str
    category_id: int | None = None


@router.post("/api/items/{item_id}/tag")
def add_tag_to_item(item_id: str, tag_input: TagInput, request: Request):
    """Add a tag to an item."""
    user = require_user(request)
    db = create_connection()
    try:
        # Check if item exists and user has access
        item_service = get_item_service(db)
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        # Check ownership for tagging
        if item["user_id"] != user["id"]:
            raise HTTPException(403, "Not owner")
        
        # Check if tag already exists
        existing = db.execute(
            "SELECT id FROM tags WHERE photo_id = ? AND tag = ?",
            (item_id, tag_input.tag.lower().strip())
        ).fetchone()
        if existing:
            return {"status": "exists", "message": "Tag already added"}
        
        # Add tag
        cursor = db.execute(
            "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
            (item_id, tag_input.tag.lower().strip(), tag_input.category_id)
        )
        db.commit()
        
        return {"status": "ok", "tag_id": cursor.lastrowid}
    finally:
        db.close()


@router.delete("/api/items/{item_id}/tag/{tag_id}")
def remove_tag_from_item(item_id: str, tag_id: int, request: Request):
    """Remove a tag from an item."""
    user = require_user(request)
    db = create_connection()
    try:
        # Check ownership
        item_service = get_item_service(db)
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        if item["user_id"] != user["id"]:
            raise HTTPException(403, "Not owner")
        
        db.execute("DELETE FROM tags WHERE id = ? AND photo_id = ?", (tag_id, item_id))
        db.commit()
        
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/api/items/{item_id}/ai-tags")
def generate_ai_tags_for_item(item_id: str, request: Request):
    """Generate AI tags for an item."""
    import random
    user = require_user(request)
    db = create_connection()
    try:
        # Check if item exists
        item_service = get_item_service(db)
        item = item_service.get_item(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        if item["user_id"] != user["id"]:
            raise HTTPException(403, "Not owner")
        
        # Get random presets
        presets = db.execute("""
            SELECT p.name, p.category_id 
            FROM tag_presets p
            ORDER BY RANDOM()
            LIMIT 5
        """).fetchall()
        
        if not presets:
            return {"status": "error", "message": "No presets available"}
        
        # Add random tags
        added_tags = []
        for preset in presets:
            # Check if tag already exists
            existing = db.execute(
                "SELECT id FROM tags WHERE photo_id = ? AND tag = ?",
                (item_id, preset["name"].lower().strip())
            ).fetchone()
            if not existing:
                cursor = db.execute(
                    "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
                    (item_id, preset["name"].lower().strip(), preset["category_id"])
                )
                added_tags.append({"id": cursor.lastrowid, "tag": preset["name"]})
        
        db.commit()
        return {"status": "ok", "tags": added_tags}
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
