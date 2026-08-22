'''
File:   items.py
Brief:  Item routes - unified API for all content types.
Author: Mistress-Lukutar
Date:   2026-07-24
'''
import tempfile
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.routes.gallery.deps import get_permission_service, get_album_service
from app.application.services import ItemService, AlbumService
from app.database import create_connection
from app.dependencies import require_user
from app.infrastructure.repositories import (
    ItemRepository, ItemMediaRepository, AlbumRepository, FolderRepository
)
from app.infrastructure.services.encryption import EncryptionService, dek_cache
from app.infrastructure.services.image_conversion import (
    ConversionSettings,
    ImageConversionError,
    SUPPORTED_DOWNLOAD_FORMATS,
    convert_image,
    format_extension,
    needs_conversion,
)
from app.infrastructure.services.media import get_media_type
from app.infrastructure.storage import get_storage
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


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
            "png_text_chunks": metadata.get("png_text_chunks"),
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
    png_text_chunks: Optional[dict] = None
    
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
            duration=data.duration,
            png_text_chunks=data.png_text_chunks
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
        
        if not perm_service.can_access_item(item_id, user["id"]):
            raise HTTPException(status_code=403, detail="Cannot access item")
        
        item = item_service.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        source_owner_id = item["user_id"]

        if source_owner_id != user["id"]:
            if not dek_cache.get(source_owner_id) or not user_dek:
                raise HTTPException(status_code=403, detail="Cannot re-encrypt without DEK")
        
        new_item_id = await item_service.copy_item(
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


# =============================================================================
# Batch Download
# =============================================================================

class BatchDownloadOptions(BaseModel):
    """Image conversion options from the download modal."""

    format: str = "jxl"
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    webp_quality: int = Field(default=90, ge=1, le=100)
    webp_lossless: bool = False
    jxl_effort: int = Field(default=7, ge=1, le=9)
    png_optimize: bool = True

    @field_validator("format")
    @classmethod
    def _validate_format(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SUPPORTED_DOWNLOAD_FORMATS:
            raise ValueError(
                f"format must be one of: {', '.join(SUPPORTED_DOWNLOAD_FORMATS)}"
            )
        return value


class BatchDownloadInput(BaseModel):
    item_ids: list[str] = []
    album_ids: list[str] = []
    options: BatchDownloadOptions = BatchDownloadOptions()


@dataclass
class _DownloadEntry:
    """One file scheduled for a batch download response."""

    dir_name: str      # "" for the archive root, album name for album items
    title: str         # raw item title (original filename)
    item_id: str
    owner_id: int
    content_type: str
    media_type: str    # 'image' | 'video'


def _sanitize_name(name: str | None, fallback: str = "file") -> str:
    """Make an archive-safe file/folder name (unicode letters preserved).

    Albums in older datasets may carry a NULL name, so None input is
    accepted and replaced with the caller-supplied fallback.
    """
    if not name:
        return fallback
    sanitized = "".join(
        c if (c.isalnum() or c in (" ", "-", "_", ".")) else "_"
        for c in name
    )
    sanitized = sanitized.strip(" .")
    return sanitized or fallback


def _archive_path(dir_name: str, filename: str) -> str:
    """Build a zip entry path, nesting album items into a subfolder."""
    return f"{dir_name}/{filename}" if dir_name else filename


def _unique_archive_path(used: set, path: str) -> str:
    """Deduplicate archive paths by appending " (n)" before the extension."""
    if path not in used:
        used.add(path)
        return path
    base, dot, ext = path.rpartition(".")
    if dot and base:
        stem, suffix = base, f".{ext}"
    else:
        stem, suffix = path, ""
    n = 2
    while True:
        candidate = f"{stem} ({n}){suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _with_extension(filename: str, extension: str) -> str:
    """Replace (or append) the file extension of a filename."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{base}{extension}"


def _passthrough_filename(
    title: str, content_type: str, conversion: ConversionSettings
) -> str:
    """Align the extension of a passthrough download with the stored bytes.

    Items transcoded to JXL at upload time keep their original title
    (e.g. ``photo.jpg``), so a JXL download would otherwise carry a
    misleading extension.
    """
    jxl_ext = format_extension("jxl")
    if content_type == "image/jxl" and conversion.format == "jxl" and jxl_ext:
        return _with_extension(title, jxl_ext)
    return title


def _attachment_header(filename: str) -> str:
    """Build a Content-Disposition header with an ASCII + UTF-8 filename."""
    ascii_name = (
        filename.encode("ascii", "ignore").decode("ascii").replace('"', "")
        or "file"
    )
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def _entry_from_row(row, dir_name: str = "") -> _DownloadEntry:
    """Build a _DownloadEntry from an items JOIN item_media row."""
    content_type = row["content_type"] or "application/octet-stream"
    media_type = row["media_type"] or get_media_type(content_type)
    return _DownloadEntry(
        dir_name=dir_name,
        title=row["title"] or row["id"],
        item_id=row["id"],
        owner_id=row["user_id"],
        content_type=content_type,
        media_type=media_type,
    )


async def _load_converted_bytes(
    entry: _DownloadEntry,
    dek: bytes,
    storage,
    conversion: ConversionSettings,
) -> tuple[bytes, str, str] | None:
    """Decrypt an entry's bytes and convert them when conversion applies.

    Returns:
        (converted_bytes, content_type, extension), or None when the entry
        must be served as-is (video, no-op conversion, or a failed
        conversion which falls back to the original bytes).
    """
    if entry.media_type == "video" or not needs_conversion(
        entry.content_type, conversion
    ):
        return None
    encrypted = await storage.download(entry.item_id, "uploads")
    plaintext = EncryptionService.decrypt_bytes(encrypted, dek)
    try:
        return convert_image(plaintext, entry.content_type, conversion)
    except ImageConversionError as exc:
        logger.warning(
            "batch-download: conversion of %s to %s failed (%s); using original",
            entry.item_id, conversion.format, exc,
        )
        return None


async def _single_file_response(
    entry: _DownloadEntry,
    user,
    user_dek: bytes | None,
    storage,
    conversion: ConversionSettings,
) -> Response:
    """Serve a single selected file directly (no ZIP archive)."""
    dek = (
        user_dek if entry.owner_id == user["id"] else dek_cache.get(entry.owner_id)
    )
    if not dek:
        raise HTTPException(status_code=403, detail="Encryption key not available")

    filename = _sanitize_name(entry.title, "file")
    converted = await _load_converted_bytes(entry, dek, storage, conversion)
    if converted is not None:
        data, content_type, extension = converted
        filename = _with_extension(filename, extension)
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": _attachment_header(filename),
                "Content-Length": str(len(data)),
            },
        )

    # Passthrough: stream the decrypted original.
    filename = _passthrough_filename(filename, entry.content_type, conversion)
    stream = await storage.get_stream(entry.item_id, "uploads")

    def _gen():
        try:
            yield from EncryptionService.iter_decrypt(stream, dek)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    headers = {"Content-Disposition": _attachment_header(filename)}
    try:
        enc_size = await storage.get_size(entry.item_id, "uploads")
        headers["Content-Length"] = str(
            EncryptionService.get_plaintext_size(enc_size)
        )
    except Exception:  # noqa: BLE001 - size header is best-effort
        pass

    return StreamingResponse(
        _gen(),
        media_type=entry.content_type or "application/octet-stream",
        headers=headers,
    )


@router.post("/api/items/batch-download")
async def batch_download(data: BatchDownloadInput, request: Request):
    """Download selected items and albums, optionally converting images.

    A single-file selection is served directly (no ZIP). Larger selections
    are written into a spooled ZIP one file at a time via
    :meth:`zipfile.ZipFile.open`, so memory use stays bounded regardless of
    total batch size: standalone items land at the archive root and each
    album in a subfolder named after the album. Images are converted to the
    requested format (default ``jxl`` = as stored); videos always pass
    through unchanged.
    """
    user = require_user(request)
    user_dek = dek_cache.get(user["id"])
    storage = get_storage()
    conversion = ConversionSettings(**data.options.model_dump())

    db = create_connection()
    try:
        perm_service = get_permission_service(db)

        entries: list[_DownloadEntry] = []
        album_dirs: list[str] = []
        standalone_count = 0

        # Individual items.
        for item_id in data.item_ids:
            if not perm_service.can_access_item(item_id, user["id"]):
                continue
            item = db.execute(
                """SELECT i.id, i.title, i.user_id,
                          COALESCE(im.content_type, it.content_type) AS content_type,
                          im.media_type
                   FROM items i
                   LEFT JOIN item_media im ON im.item_id = i.id
                   LEFT JOIN item_texts it ON it.item_id = i.id
                   WHERE i.id = ?""",
                (item_id,),
            ).fetchone()
            if item and storage.exists(item_id, "uploads"):
                entries.append(_entry_from_row(item))
                standalone_count += 1

        # Albums: each album contributes a subfolder inside the archive.
        for album_id in data.album_ids:
            if not perm_service.can_access_album(album_id, user["id"]):
                continue
            album = db.execute(
                "SELECT id, name FROM albums WHERE id = ?",
                (album_id,),
            ).fetchone()
            if not album:
                continue
            album_items = db.execute(
                """SELECT i.id, i.title, i.user_id,
                          COALESCE(im.content_type, it.content_type) AS content_type,
                          im.media_type
                   FROM items i
                   JOIN album_items ai ON i.id = ai.item_id
                   LEFT JOIN item_media im ON im.item_id = i.id
                   LEFT JOIN item_texts it ON it.item_id = i.id
                   WHERE ai.album_id = ?
                   ORDER BY ai.position""",
                (album_id,),
            ).fetchall()
            if not album_items:
                continue
            # NULL names (legacy rows) fall back to a per-album unique dir
            # so several unnamed albums never merge into one subfolder.
            dir_name = _sanitize_name(
                album["name"], fallback=f"album-{str(album_id)[:8]}"
            )
            for item in album_items:
                if storage.exists(item["id"], "uploads"):
                    entries.append(_entry_from_row(item, dir_name=dir_name))
            album_dirs.append(dir_name)

        if not entries:
            raise HTTPException(status_code=404, detail="No files to download")

        # Single file: serve it directly instead of packing a one-file ZIP.
        if len(entries) == 1:
            return await _single_file_response(
                entries[0], user, user_dek, storage, conversion
            )

        if standalone_count == 0 and len(album_dirs) == 1:
            zip_name = f"{album_dirs[0]}.zip"
        else:
            date_folder = datetime.now().strftime("%Y-%m-%d")
            zip_name = f"synth-download-{date_folder}.zip"

        # Spooled temp file: rolls to disk if it exceeds 64 MiB.
        spool = tempfile.SpooledTemporaryFile(
            max_size=64 * 1024 * 1024, suffix=".zip"
        )
        try:
            used_paths: set = set()
            with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
                for entry in entries:
                    dek = (
                        user_dek
                        if entry.owner_id == user["id"]
                        else dek_cache.get(entry.owner_id)
                    )
                    if not dek:
                        logger.warning(
                            "batch-download: skip %s (owner encryption key "
                            "not available)",
                            entry.item_id,
                        )
                        continue
                    safe_title = _sanitize_name(entry.title, "file")
                    try:
                        converted = await _load_converted_bytes(
                            entry, dek, storage, conversion
                        )
                        if converted is not None:
                            converted_bytes, _ct, extension = converted
                            path = _unique_archive_path(
                                used_paths,
                                _archive_path(
                                    entry.dir_name,
                                    _with_extension(safe_title, extension),
                                ),
                            )
                            zf.writestr(path, converted_bytes)
                            continue

                        path = _unique_archive_path(
                            used_paths,
                            _archive_path(
                                entry.dir_name,
                                _passthrough_filename(
                                    safe_title, entry.content_type, conversion
                                ),
                            ),
                        )
                        enc_reader = await storage.get_stream(
                            entry.item_id, "uploads"
                        )
                        try:
                            with zf.open(path, "w") as zip_writer:
                                EncryptionService.decrypt_to_stream(
                                    enc_reader, zip_writer, dek
                                )
                        finally:
                            try:
                                enc_reader.close()
                            except Exception:
                                pass
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "batch-download: skip %s (%s)",
                            entry.item_id, exc,
                        )
                        continue

            spool.seek(0)

            def _stream():
                try:
                    while True:
                        buf = spool.read(1 << 20)
                        if not buf:
                            break
                        yield buf
                finally:
                    try:
                        spool.close()
                    except Exception:
                        pass

            return StreamingResponse(
                _stream(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": _attachment_header(zip_name)
                },
            )
        except Exception:
            try:
                spool.close()
            except Exception:
                pass
            raise
    finally:
        db.close()


# =============================================================================
# Album Endpoints
# =============================================================================

class AlbumCreateInput(BaseModel):
    name: str = Field(min_length=1)
    folder_id: str
    item_ids: List[str] = []


class AlbumUpdateInput(BaseModel):
    """Album rename payload; name must be non-empty when provided."""
    name: Optional[str] = Field(default=None, min_length=1)


@router.post("/api/albums")
def create_album(data: AlbumCreateInput, request: Request):
    """Create new album with items."""
    user = require_user(request)

    item_ids = data.item_ids or []
    
    db = create_connection()
    try:
        album_service = get_album_service(db)
        
        album = album_service.create_album(
            name=data.name,
            folder_id=data.folder_id,
            user_id=user["id"],
            item_ids=item_ids
        )
        
        return {
            "status": "ok",
            "album_id": album["id"],
            "item_count": album["item_count"],
            "album": album
        }
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
def update_album(album_id: str, data: AlbumUpdateInput, request: Request):
    """Update album (rename). Empty or missing names are rejected."""
    user = require_user(request)

    db = create_connection()
    try:
        album_repo = AlbumRepository(db)
        album_service = get_album_service(db)

        # Check edit permission
        if not album_service._can_edit(album_id, user["id"]):
            raise HTTPException(403, "Cannot edit album")

        # Update allowed fields (null name = no rename requested)
        if data.name is not None:
            album_repo.update(album_id, name=data.name)

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
async def copy_album(album_id: str, data: AlbumCopyInput, request: Request):
    """Copy album and all its items to a different folder."""
    user = require_user(request)

    db = create_connection()
    try:
        album_service = get_album_service(db)

        new_album_id = await album_service.copy_album(
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


@router.put("/api/albums/{album_id}/reorder")
def reorder_album_items(album_id: str, data: AlbumReorderInput, request: Request):
    """Reorder items in album."""
    user = require_user(request)

    item_ids = data.item_ids or []

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
