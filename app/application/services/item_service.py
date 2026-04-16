"""Item service - unified handling for all content types.

Uses Strategy Pattern for type-specific operations.
"""
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from fastapi import UploadFile, HTTPException

from ...config import ALLOWED_MEDIA_TYPES
from ...infrastructure.repositories import ItemRepository, ItemMediaRepository
from ...infrastructure.services.encryption import EncryptionService
from ...infrastructure.services.media import (
    create_thumbnail_bytes, create_video_thumbnail_bytes, get_media_type,
    get_image_dimensions, get_video_info
)
from ...infrastructure.services.metadata import extract_taken_date
from ...infrastructure.storage import get_storage


class ItemRenderer(ABC):
    """Abstract base for item type renderers.
    
    Strategy Pattern: each item type implements its own rendering logic.
    """
    
    @abstractmethod
    def get_thumbnail_url(self, item: Dict) -> str:
        """Get URL for item thumbnail."""
        pass
    
    @abstractmethod
    def get_full_url(self, item: Dict) -> str:
        """Get URL for full item view."""
        pass
    
    @abstractmethod
    def get_dimensions(self, item: Dict) -> tuple:
        """Get display dimensions (width, height)."""
        pass
    
    @abstractmethod
    def render_gallery_item(self, item: Dict) -> Dict:
        """Render HTML for gallery grid."""
        pass
    
    @abstractmethod
    def render_lightbox(self, item: Dict) -> Dict:
        """Render HTML for lightbox view."""
        pass


class MediaRenderer(ItemRenderer):
    """Renderer for photos and videos."""
    
    def get_thumbnail_url(self, item: Dict) -> str:
        from ...config import BASE_URL
        return f"{BASE_URL}/files/{item['id']}/thumbnail"
    
    def get_full_url(self, item: Dict) -> str:
        from ...config import BASE_URL
        return f"{BASE_URL}/files/{item['id']}"
    
    def get_dimensions(self, item: Dict) -> tuple:
        thumb_w = item.get('thumb_width', 280)
        thumb_h = item.get('thumb_height', 210)
        return thumb_w, thumb_h
    
    def render_gallery_item(self, item: Dict) -> Dict:
        # Returns data attributes for frontend rendering
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'thumb_url': self.get_thumbnail_url(item),
            'width': item.get('thumb_width', 280),
            'height': item.get('thumb_height', 210)
        }
    
    def render_lightbox(self, item: Dict) -> Dict:
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'url': self.get_full_url(item),
            'title': item.get('title', '')
        }


class ItemService:
    """Service for managing polymorphic items.
    
    Responsibilities:
    - Create items of any type
    - Route type-specific operations to appropriate renderer
    - Handle uploads for media items
    - Manage item metadata
    """
    
    # Strategy registry
    RENDERERS = {
        'media': MediaRenderer(),
        # Future: 'note': NoteRenderer(),
        # Future: 'file': FileRenderer(),
    }
    
    def __init__(
        self,
        item_repository: ItemRepository,
        item_media_repository: ItemMediaRepository,
        storage=None
    ):
        self.item_repo = item_repository
        self.media_repo = item_media_repository
        self.storage = storage or get_storage()
    
    def get_renderer(self, item_type: str) -> ItemRenderer:
        """Get renderer for item type."""
        renderer = self.RENDERERS.get(item_type)
        if not renderer:
            raise ValueError(f"Unknown item type: {item_type}")
        return renderer
    
    # ========================================================================
    # Media Item Creation (Photos/Videos)
    # ========================================================================

    async def _process_media(
        self,
        item_id: str,
        file_content: bytes,
        ext: str,
        media_type: str,
        user_dek: Optional[bytes]
    ) -> tuple:
        """Process media: thumbnail, encryption, metadata."""
        import tempfile
        from pathlib import Path
        
        # Extract taken date
        taken_at = datetime.now()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = Path(tmp.name)
        
        try:
            extracted = extract_taken_date(tmp_path)
            if extracted:
                taken_at = extracted
        finally:
            tmp_path.unlink(missing_ok=True)
        
        # Create thumbnail
        try:
            if media_type == 'video':
                thumb_bytes, thumb_w, thumb_h = create_video_thumbnail_bytes(file_content)
            else:
                thumb_bytes, thumb_w, thumb_h = create_thumbnail_bytes(file_content)
        except Exception:
            thumb_bytes, thumb_w, thumb_h = None, 0, 0
        
        # Encrypt if needed
        if user_dek:
            file_content = EncryptionService.encrypt_file(file_content, user_dek)
            if thumb_bytes:
                thumb_bytes = EncryptionService.encrypt_file(thumb_bytes, user_dek)
        
        # Save to storage
        await self.storage.upload(
            file_id=item_id,
            content=file_content,
            folder="uploads"
        )
        if thumb_bytes:
            await self.storage.upload(
                file_id=item_id,
                content=thumb_bytes,
                folder="thumbnails"
            )
        
        return taken_at, thumb_w, thumb_h
    
    async def _save_encrypted(
        self,
        item_id: str,
        content: bytes,
        thumbnail: Optional[bytes],
        is_safe: bool = True
    ):
        """Save E2E encrypted content."""
        await self.storage.upload(
            file_id=item_id,
            content=content,
            folder="uploads"
        )
        if thumbnail:
            await self.storage.upload(
                file_id=item_id,
                content=thumbnail,
                folder="thumbnails"
            )
    
    def _validate_content(self, content: bytes, expected_media_type: str) -> bool:
        """Validate file content by magic bytes."""
        if len(content) < 4:
            return False
        
        header = content[:12]
        
        # JPEG
        if header[:2] == b'\xff\xd8':
            return expected_media_type in ('image', 'photo')
        # PNG
        if header[:4] == b'\x89PNG':
            return expected_media_type in ('image', 'photo')
        # GIF
        if header[:3] in (b'GIF87', b'GIF89') or header[:4] == b'GIF8':
            return expected_media_type in ('image', 'photo')
        # WebP
        if header[4:8] == b'WEBP':
            return expected_media_type in ('image', 'photo')
        # MP4
        if header[4:8] in (b'ftyp', b'moov'):
            return expected_media_type == 'video'
        # Quick check for video
        if expected_media_type == 'video':
            return len(content) > 1000
        
        return False
    
    # ========================================================================
    # Async Upload Processing (consolidated business logic)
    # ========================================================================
    
    async def process_media_upload(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        safe_id: str = None,
        is_encrypted: bool = False,
        client_encryption_metadata: str = None,
        thumbnail: UploadFile = None,
        thumb_width: int = 0,
        thumb_height: int = 0
    ) -> Dict:
        """Process complete media upload: validation, thumbnail, storage, DB.
        
        This method consolidates all upload logic in one place:
        - File validation
        - EXIF date extraction
        - Thumbnail generation (or use client-provided for E2E)
        - Storage upload
        - Database record creation
        
        Args:
            file: Uploaded file
            folder_id: Target folder
            user_id: Owner user ID
            safe_id: Safe ID if in encrypted vault
            is_encrypted: Whether file is E2E encrypted
            client_encryption_metadata: Client encryption metadata
            thumbnail: Client-provided thumbnail (for E2E)
            thumb_width: Thumbnail width (client-provided)
            thumb_height: Thumbnail height (client-provided)
            
        Returns:
            Created item dict
        """
        import tempfile
        from pathlib import Path
        
        # Validate file
        if not file.filename:
            raise HTTPException(400, "No filename")
        
        if not is_encrypted and file.content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(400, f"Invalid file type: {file.content_type}")
        
        # Generate item ID
        item_id = str(uuid.uuid4())
        
        # Read content
        content = await file.read()
        size = len(content)
        
        if size == 0:
            raise HTTPException(400, "Empty file")
        
        # Determine media type
        media_type = get_media_type(file.content_type)
        
        # Extract dimensions and metadata
        orig_width, orig_height = None, None
        duration = None
        taken_at = None
        
        if not is_encrypted:
            if media_type == 'image':
                # Get original dimensions
                dims = get_image_dimensions(content)
                if dims:
                    orig_width, orig_height = dims
                # Get EXIF date
                try:
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(content)
                        tmp.flush()
                        taken_at = extract_taken_date(Path(tmp.name))
                except Exception:
                    pass
            elif media_type == 'video':
                # Get video dimensions and duration
                info = get_video_info(content)
                if info:
                    orig_width, orig_height, duration_sec = info
                    duration = int(round(duration_sec))
        
        # Handle thumbnail
        thumb_w, thumb_h = thumb_width, thumb_height
        thumb_bytes = None
        if is_encrypted and thumbnail:
            # E2E: Use encrypted thumbnail from client
            thumb_bytes = await thumbnail.read()
        elif not is_encrypted:
            # Server-side: Generate thumbnail
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
        await self.storage.upload(item_id, content, folder="uploads")
        if thumb_bytes:
            await self.storage.upload(item_id, thumb_bytes, folder="thumbnails")
        
        # Create database records
        return self.create_db_records(
            item_id=item_id,
            file_data={
                "filename": file.filename,
                "content_type": file.content_type or "application/octet-stream",
                "size": size,
                "uploaded_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "user_id": user_id,
                "is_encrypted": is_encrypted,
                "taken_at": taken_at,
            },
            media_data={
                "media_type": media_type,
                "storage_path": f"uploads/{item_id}",
                "is_encrypted": is_encrypted,
                "client_encryption_metadata": client_encryption_metadata,
                "thumb_width": thumb_w,
                "thumb_height": thumb_h,
                "width": orig_width,
                "height": orig_height,
                "duration": duration,
            },
            folder_id=folder_id,
            safe_id=safe_id,
            user_id=user_id
        )
    
    # ========================================================================
    # Sync Item Creation (for non-async contexts)
    # ========================================================================
    
    def create_db_records(
        self,
        item_id: str,
        file_data: dict,
        media_data: dict,
        folder_id: str,
        user_id: int,
        safe_id: str = None
    ) -> Dict:
        """Create database records for uploaded media item.

        This is a synchronous method that creates records in items and item_media
        tables after the file has been uploaded to storage.

        Args:
            item_id: Pre-generated item UUID
            file_data: Dict with filename, content_type, size, uploaded_at, user_id, is_encrypted
            media_data: Dict with media_type, storage_path, is_encrypted, client_encryption_metadata
            folder_id: Target folder
            user_id: Owner
            safe_id: Safe ID if in encrypted vault

        Returns:
            Created item dict
        """
        # Create base item
        self.item_repo.create(
            item_type='media',
            folder_id=folder_id,
            user_id=user_id,
            item_id=item_id,
            title=file_data.get('filename', ''),
            safe_id=safe_id,
            is_encrypted=file_data.get('is_encrypted', False),
            uploaded_at=file_data.get('uploaded_at')
        )
        
        # Create media details (no filename - uses item_id as filename in storage)
        # Fallback: if no EXIF date, use upload date
        taken_at = file_data.get('taken_at') or file_data.get('uploaded_at')
        self.media_repo.create(
            item_id=item_id,
            media_type=media_data.get('media_type', 'image'),
            original_name=file_data.get('filename', ''),
            content_type=file_data.get('content_type', 'application/octet-stream'),
            width=media_data.get('width'),
            height=media_data.get('height'),
            duration=media_data.get('duration'),
            thumb_width=media_data.get('thumb_width', 0),
            thumb_height=media_data.get('thumb_height', 0),
            taken_at=taken_at,
            file_size=file_data.get('size')
        )
        
        return {
            'id': item_id,
            'type': 'media',
            'folder_id': folder_id,
            'safe_id': safe_id,
            'user_id': user_id,
            'uploaded_at': file_data.get('uploaded_at'),
            'title': file_data.get('filename', ''),
            'is_encrypted': file_data.get('is_encrypted', False),
            'media_type': media_data.get('media_type', 'image'),
            # original_name removed - using title only
            'content_type': file_data.get('content_type', 'application/octet-stream'),
            'width': media_data.get('width'),
            'height': media_data.get('height'),
            'duration': media_data.get('duration'),
            'thumb_width': media_data.get('thumb_width', 0),
            'thumb_height': media_data.get('thumb_height', 0),
            'taken_at': taken_at,
            # Extension-less storage: filename equals item_id
            'filename': item_id,
        }
    
    # ========================================================================
    # Generic Item Operations
    # ========================================================================
    
    def get_item(self, item_id: str) -> Optional[Dict]:
        """Get full item with type-specific data."""
        base = self.item_repo.get_by_id(item_id)
        if not base:
            return None
        
        if base['type'] == 'media':
            media = self.media_repo.get_by_item_id(item_id)
            if media:
                # Add only necessary fields (exclude internal/db-specific)
                # Note: filename is not needed - we use item_id as filename
                base.update({
                    'media_type': media.get('media_type'),
                    # original_name removed - using title only
                    'content_type': media.get('content_type'),
                    'thumb_width': media.get('thumb_width'),
                    'thumb_height': media.get('thumb_height'),
                    'taken_at': media.get('taken_at'),
                })
        
        return base
    
    def get_items_by_folder(
        self,
        folder_id: str,
        item_type: str = None,
        sort_by: str = "created",
        standalone_only: bool = False
    ) -> List[Dict]:
        """Get items in folder with full data.
        
        Args:
            folder_id: Folder ID
            item_type: Filter by type ('media', 'note') or None for all
            sort_by: 'created' or 'title'
            standalone_only: If True, exclude items that are in albums
        """
        if item_type == 'media' and not standalone_only:
            return self.media_repo.get_by_folder(folder_id)
        
        items = self.item_repo.get_by_folder(folder_id, item_type, sort_by)
        
        # Filter out items that are in albums if requested
        if standalone_only:
            # Get all item_ids that are in albums
            album_item_ids = self._get_album_item_ids(folder_id)
            items = [item for item in items if item['id'] not in album_item_ids]
        
        # Enrich with type-specific data
        for item in items:
            if item['type'] == 'media':
                media = self.media_repo.get_by_item_id(item['id'])
                if media:
                    item.update({
                        'media_type': media.get('media_type'),
                        # original_name removed - using title only
                        'content_type': media.get('content_type'),
                        'thumb_width': media.get('thumb_width'),
                        'thumb_height': media.get('thumb_height'),
                        'taken_at': media.get('taken_at'),
                    })
        
        return items
    
    def _get_album_item_ids(self, folder_id: str) -> set:
        """Get IDs of all items that are in albums for a given folder."""
        from ...infrastructure.repositories import AlbumRepository
        album_repo = AlbumRepository(self.item_repo._conn)
        return album_repo.get_item_ids_by_folder(folder_id)
    
    def move_item(self, item_id: str, folder_id: str, user_id: int) -> bool:
        """Move item to different folder."""
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False
        
        if item['user_id'] != user_id:
            raise HTTPException(403, "Not owner")
        
        return self.item_repo.move_to_folder(item_id, folder_id)

    async def delete_item(self, item_id: str, user_id: int) -> bool:
        """Delete item and all its data including files."""
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False

        if item['user_id'] != user_id:
            raise HTTPException(403, "Not owner")

        # Delete files from storage
        await self._delete_item_files(item_id)

        return self.item_repo.delete(item_id)

    async def _delete_item_files(self, item_id: str) -> None:
        """Delete item files from storage."""
        await self.storage.delete(item_id, folder="uploads")
        await self.storage.delete(item_id, folder="thumbnails")
    
    def copy_item(
        self,
        item_id: str,
        dest_folder_id: str,
        user_id: int,
        source_owner_id: int = None,
        is_encrypted: bool = False
    ) -> str:
        """Copy a single item to another folder.
        
        Returns:
            New item ID
        """
        from ...config import UPLOADS_DIR, THUMBNAILS_DIR
        from ...infrastructure.services.encryption import EncryptionService, dek_cache
        
        item = self.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        media = self.media_repo.get_by_item_id(item_id)
        if not media:
            raise HTTPException(404, "Media not found")
        
        source_owner_id = source_owner_id or item["user_id"]
        is_encrypted = is_encrypted if is_encrypted else item.get("is_encrypted", False)
        
        new_item_id = str(uuid.uuid4())
        
        old_upload = UPLOADS_DIR / item_id
        new_upload = UPLOADS_DIR / new_item_id
        old_thumb = THUMBNAILS_DIR / item_id
        new_thumb = THUMBNAILS_DIR / new_item_id
        
        def _copy_and_reencrypt_file(
            old_path: Path,
            new_path: Path,
            is_encrypted: bool,
            source_owner_id: int,
            dest_owner_id: int
        ) -> bool:
            import shutil
            import os
            
            old_path_str = str(old_path)
            new_path_str = str(new_path)
            
            if not old_path.exists():
                return False
            
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                return False
            
            try:
                if not is_encrypted or source_owner_id == dest_owner_id:
                    data = old_path.read_bytes()
                    new_path.write_bytes(data)
                    return new_path.exists()
                
                source_dek = dek_cache.get(source_owner_id)
                dest_dek = dek_cache.get(dest_owner_id)
                
                if not source_dek or not dest_dek:
                    return False
                
                encrypted_data = old_path.read_bytes()
                try:
                    plaintext = EncryptionService.decrypt_file(encrypted_data, source_dek)
                except Exception:
                    return False
                
                new_encrypted = EncryptionService.encrypt_file(plaintext, dest_dek)
                new_path.write_bytes(new_encrypted)
                return new_path.exists()
            except Exception:
                return False
        
        if not _copy_and_reencrypt_file(
            old_upload, new_upload, is_encrypted, source_owner_id, user_id
        ):
            raise HTTPException(500, "Failed to copy file")
        
        if old_thumb.exists():
            _copy_and_reencrypt_file(
                old_thumb, new_thumb, is_encrypted, source_owner_id, user_id
            )
        
        self.item_repo.create(
            item_type='media',
            folder_id=dest_folder_id,
            user_id=user_id,
            item_id=new_item_id,
            title=item.get("title", "Untitled"),
            description=item.get("description"),
            safe_id=item.get("safe_id"),
            is_encrypted=is_encrypted
        )
        
        self.media_repo.create(
            item_id=new_item_id,
            media_type=media["media_type"],
            original_name=media.get("original_name"),
            content_type=media["content_type"],
            width=media.get("width"),
            height=media.get("height"),
            duration=media.get("duration"),
            thumb_width=media["thumb_width"],
            thumb_height=media["thumb_height"],
            taken_at=media["taken_at"],
            file_size=media.get("file_size")
        )
        
        # Copy tags
        conn = self.item_repo._conn
        item_tags = conn.execute(
            "SELECT tag_id FROM item_tags WHERE item_id = ?",
            (item_id,)
        ).fetchall()
        for item_tag in item_tags:
            conn.execute(
                "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                (new_item_id, item_tag["tag_id"])
            )
        
        return new_item_id
    
    # ========================================================================
    # Rendering Helpers
    # ========================================================================
    
    def render_for_gallery(self, item: Dict) -> Dict:
        """Render item for gallery view using appropriate strategy."""
        renderer = self.get_renderer(item['type'])
        return renderer.render_gallery_item(item)
    
    def render_for_lightbox(self, item: Dict) -> Dict:
        """Render item for lightbox view using appropriate strategy."""
        renderer = self.get_renderer(item['type'])
        return renderer.render_lightbox(item)
    
    def count_items_by_folder(self, folder_id: str) -> int:
        """Count items in folder."""
        return self.item_repo.count_by_folder(folder_id)
    
    # ========================================================================
    # Metadata Operations
    # ========================================================================

    
    def get_item_metadata(self, item_id: str) -> Optional[Dict]:
        """Get combined metadata from items and item_media tables.
        
        Args:
            item_id: Item ID
            
        Returns:
            Combined metadata dict or None if not found
        """
        cursor = self.item_repo._execute(
            """SELECT 
                i.id, i.type, i.title, i.description, i.user_id,
                i.uploaded_at, i.updated_at,
                im.media_type, im.original_name, im.content_type,
                im.width, im.height, im.duration, im.taken_at, im.file_size
               FROM items i
               LEFT JOIN item_media im ON i.id = im.item_id
               WHERE i.id = ?""",
            (item_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        return dict(row)
    
    def update_metadata(
        self,
        item_id: str,
        user_id: int,
        title: str = None,
        description: str = None,
        taken_at: datetime = None,
        width: int = None,
        height: int = None,
        duration: int = None
    ) -> Dict:
        """Update item metadata.
        
        Args:
            item_id: Item ID
            user_id: User performing the update (for ownership check)
            title: New title (optional)
            description: New description (optional)
            taken_at: New capture date (optional)
            width: New width in pixels (optional)
            height: New height in pixels (optional)
            duration: Duration in seconds for video (optional)
            
        Returns:
            Dict with update status
            
        Raises:
            HTTPException: 404 if item not found, 403 if not owner
        """
        # Check item exists and get ownership info
        item = self.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(404, "Item not found")
        
        # Validate ownership or editor permission
        from ...infrastructure.repositories import PermissionRepository
        perm_repo = PermissionRepository(self.item_repo._conn)
        
        is_owner = item.get('user_id') == user_id
        can_edit = perm_repo.can_edit(item.get('folder_id'), user_id)
        
        if not is_owner and not can_edit:
            raise HTTPException(403, "Not owner or editor")
        
        updated = False
        
        # Update items table fields
        if title is not None or description is not None:
            if self.item_repo.update_metadata(item_id, title, description):
                updated = True
        
        # Update item_media table fields
        media_updates = {}
        if taken_at is not None:
            media_updates['taken_at'] = taken_at
        if width is not None:
            media_updates['width'] = width
        if height is not None:
            media_updates['height'] = height
        if duration is not None:
            media_updates['duration'] = duration
            
        if media_updates:
            if self.media_repo.update(item_id, **media_updates):
                updated = True
        
        return {"status": "ok" if updated else "no_changes", "updated": updated}
