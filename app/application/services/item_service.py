"""Item service - unified handling for all content types.

Uses Strategy Pattern for type-specific operations.
"""
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, List, Any, BinaryIO

from fastapi import UploadFile, HTTPException

from ...infrastructure.repositories import ItemRepository, ItemMediaRepository
from ...infrastructure.services.encryption import EncryptionService
from ...infrastructure.services.media import (
    create_thumbnail_bytes, create_video_thumbnail_bytes, get_media_type
)
from ...infrastructure.services.metadata import extract_taken_date
from ...infrastructure.storage import get_storage
from ...config import ALLOWED_MEDIA_TYPES


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
    def render_gallery_item(self, item: Dict) -> str:
        """Render HTML for gallery grid."""
        pass
    
    @abstractmethod
    def render_lightbox(self, item: Dict) -> str:
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
        return (thumb_w, thumb_h)
    
    def render_gallery_item(self, item: Dict) -> str:
        # Returns data attributes for frontend rendering
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'thumb_url': self.get_thumbnail_url(item),
            'width': item.get('thumb_width', 280),
            'height': item.get('thumb_height', 210)
        }
    
    def render_lightbox(self, item: Dict) -> str:
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'url': self.get_full_url(item),
            'title': item.get('title', item.get('original_name', ''))
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
    
    async def create_media_item(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
        safe_id: str = None,
        is_safe: bool = False
    ) -> Dict[str, Any]:
        """Create a media item (photo or video).
        
        Args:
            file: Uploaded file
            folder_id: Target folder
            user_id: Owner
            user_dek: Encryption key (if server-side encryption)
            safe_id: Safe ID (if E2E)
            is_safe: Whether this is E2E encrypted
            
        Returns:
            Created item dict
        """
        # Validate
        if not file or not file.filename:
            raise HTTPException(400, "file is required")
        
        if not is_safe and file.content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(400, "Invalid file type")
        
        media_type = get_media_type(file.content_type)
        item_id = str(uuid.uuid4())
        filename = item_id  # Extension-less storage
        ext = __import__('pathlib').Path(file.filename).suffix.lower()
        if not ext:
            ext = ".mp4" if media_type == "video" else ".jpg"
        
        # Read content
        file_content = await file.read()
        
        # Validate content (magic bytes)
        if not is_safe and not self._validate_content(file_content, media_type):
            raise HTTPException(400, "File content does not match format")
        
        # Process based on encryption
        if is_safe:
            # E2E: Save as-is, client encrypted
            await self._save_encrypted(item_id, file_content, None, is_safe=True)
            taken_at = datetime.now()
            thumb_w = thumb_h = 0
            is_encrypted = True
        else:
            # Server-side: Process thumbnail, encrypt if needed
            taken_at, thumb_w, thumb_h = await self._process_media(
                item_id, file_content, ext, media_type, user_dek
            )
            is_encrypted = user_dek is not None
        
        content_type = file.content_type or (
            "video/mp4" if media_type == "video" else "image/jpeg"
        )
        
        # Create base item
        self.item_repo.create(
            item_type='media',
            folder_id=folder_id,
            user_id=user_id,
            item_id=item_id,
            title=file.filename,
            safe_id=safe_id,
            is_encrypted=is_encrypted
        )
        
        # Create media details
        self.media_repo.create(
            item_id=item_id,
            media_type='video' if media_type == 'video' else 'image',
            filename=filename,
            original_name=file.filename,
            content_type=content_type,
            thumb_width=thumb_w,
            thumb_height=thumb_h,
            taken_at=taken_at
        )
        
        return {
            'id': item_id,
            'type': 'media',
            'media_type': media_type,
            'title': file.filename,
            'taken_at': taken_at.isoformat() if taken_at else None
        }
    
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
    # Sync Item Creation (for non-async contexts)
    # ========================================================================
    
    def create_media_item_sync(
        self,
        item_id: str,
        file_data: dict,
        media_data: dict,
        folder_id: str,
        user_id: int,
        safe_id: str = None
    ) -> Dict:
        """Synchronous media item creation (used by upload routes).
        
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
            created_at=file_data.get('uploaded_at')
        )
        
        # Create media details
        self.media_repo.create(
            item_id=item_id,
            media_type=media_data.get('media_type', 'image'),
            filename=item_id,  # Storage uses item_id as filename
            original_name=file_data.get('filename', ''),
            content_type=file_data.get('content_type', 'application/octet-stream'),
            thumb_width=media_data.get('thumb_width', 0),
            thumb_height=media_data.get('thumb_height', 0),
            taken_at=file_data.get('taken_at')
        )
        
        return {
            'id': item_id,
            'type': 'media',
            'media_type': media_data.get('media_type', 'image'),
            'title': file_data.get('filename', ''),
            'folder_id': folder_id,
            'user_id': user_id
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
                base.update(media)
        
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
                    item.update(media)
        
        return items
    
    def _get_album_item_ids(self, folder_id: str) -> set:
        """Get IDs of all items that are in albums for a given folder."""
        cursor = self.item_repo._conn.execute(
            """SELECT DISTINCT ai.item_id 
               FROM album_items ai
               JOIN items i ON ai.item_id = i.id
               WHERE i.folder_id = ?""",
            (folder_id,)
        )
        return {row['item_id'] for row in cursor.fetchall()}
    
    def move_item(self, item_id: str, folder_id: str, user_id: int) -> bool:
        """Move item to different folder."""
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False
        
        if item['user_id'] != user_id:
            raise HTTPException(403, "Not owner")
        
        return self.item_repo.move_to_folder(item_id, folder_id)
    
    def batch_move(
        self,
        item_ids: list[str],
        album_ids: list[str],
        dest_folder_id: str,
        user_id: int
    ) -> dict:
        """Move multiple items and albums to another folder.
        
        Args:
            item_ids: List of item UUIDs to move
            album_ids: List of album UUIDs to move
            dest_folder_id: Destination folder UUID
            user_id: User performing the move
            
        Returns:
            Dict with counts of moved and skipped items
        """
        from ...infrastructure.repositories import FolderRepository, AlbumRepository
        
        folder_repo = FolderRepository(self.item_repo._conn)
        album_repo = AlbumRepository(self.item_repo._conn)
        
        # Check destination permission (must be owner or editor)
        dest_folder = folder_repo.get_by_id(dest_folder_id)
        if not dest_folder:
            raise HTTPException(403, "Destination folder not found")
        
        if dest_folder['user_id'] != user_id:
            # Check if user has editor permission
            from ...infrastructure.repositories import PermissionRepository
            perm_repo = PermissionRepository(self.item_repo._conn)
            perm = perm_repo.get_permission(dest_folder_id, user_id)
            if perm != 'editor':
                raise HTTPException(403, "Cannot move to this folder")
        
        moved_items = 0
        moved_albums = 0
        skipped_items = 0
        skipped_albums = 0
        
        # Move items
        for item_id in item_ids:
            item = self.item_repo.get_by_id(item_id)
            
            if not item:
                skipped_items += 1
                continue
            
            # Check ownership
            if item['user_id'] != user_id:
                skipped_items += 1
                continue
            
            # Skip if already in target folder
            if item.get('folder_id') == dest_folder_id:
                continue
            
            if self.item_repo.move_to_folder(item_id, dest_folder_id):
                moved_items += 1
            else:
                skipped_items += 1
        
        # Move albums
        for album_id in album_ids:
            album = album_repo.get_by_id(album_id)
            
            if not album:
                skipped_albums += 1
                continue
            
            # Check ownership
            if album['user_id'] != user_id:
                skipped_albums += 1
                continue
            
            # Skip if already in target folder
            if album.get('folder_id') == dest_folder_id:
                continue
            
            if album_repo.move_to_folder(album_id, dest_folder_id):
                moved_albums += 1
            else:
                skipped_albums += 1
        
        return {
            'moved_photos': moved_items,
            'moved_albums': moved_albums,
            'skipped_photos': skipped_items,
            'skipped_albums': skipped_albums
        }
    
    def delete_item(self, item_id: str, user_id: int) -> bool:
        """Delete item and all its data including files."""
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False
        
        if item['user_id'] != user_id:
            raise HTTPException(403, "Not owner")
        
        # Delete files from storage
        self._delete_item_files(item_id)
        
        return self.item_repo.delete(item_id)
    
    def _delete_item_files(self, item_id: str) -> None:
        """Delete item files from storage."""
        from ...config import UPLOADS_DIR, THUMBNAILS_DIR
        from pathlib import Path
        
        media = self.media_repo.get_by_item_id(item_id)
        
        if media and media.get('filename'):
            # Delete from uploads
            upload_path = UPLOADS_DIR / media['filename']
            if upload_path.exists():
                upload_path.unlink()
        
        # Delete thumbnail (extension-less storage)
        thumb_path = THUMBNAILS_DIR / item_id
        if thumb_path.exists():
            thumb_path.unlink()
    
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
    
    def detect_media_type(self, filename: str, content: bytes) -> str:
        """Detect media type from filename and content."""
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        
        video_exts = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v'}
        image_exts = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff'}
        
        if ext in video_exts:
            return 'video'
        if ext in image_exts:
            return 'image'
        
        # Check content
        if len(content) >= 12:
            header = content[:12]
            if header[4:8] in (b'ftyp', b'moov'):
                return 'video'
        
        return 'unknown'
