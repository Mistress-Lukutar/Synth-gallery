'''
File:   item_service.py
Brief:  Item service - unified handling for all content types.
Author: Mistress-Lukutar
Date:   2026-07-13
Version: v1.0.0
'''

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from app.config import ALLOWED_MEDIA_TYPES, USE_JXL
from app.infrastructure.repositories import ItemMediaRepository, ItemRepository
from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.services.jxl import is_jxl_content
from app.infrastructure.services.jxl_encoder import (
    JxlEncodeError,
    encode_to_lossless_jxl,
    is_jxl_encoding_available,
)
from app.infrastructure.services.media import (
    create_thumbnail_bytes,
    create_video_thumbnail_bytes,
    get_image_dimensions,
    get_media_type,
    get_video_info,
)
from app.infrastructure.services.metadata import (
    extract_png_text_chunks,
    extract_taken_date,
)
from app.infrastructure.storage import get_storage


logger = logging.getLogger(__name__)


class ItemRenderer(ABC):
    '''Abstract base for item type renderers.

    Strategy Pattern: each item type implements its own rendering logic.
    '''

    @abstractmethod
    def get_thumbnail_url(self, item: Dict) -> str:
        '''Get URL for item thumbnail.'''

    @abstractmethod
    def get_full_url(self, item: Dict) -> str:
        '''Get URL for full item view.'''

    @abstractmethod
    def get_dimensions(self, item: Dict) -> tuple:
        '''Get display dimensions (width, height).'''

    @abstractmethod
    def render_gallery_item(self, item: Dict) -> Dict:
        '''Render HTML for gallery grid.'''

    @abstractmethod
    def render_lightbox(self, item: Dict) -> Dict:
        '''Render HTML for lightbox view.'''


class MediaRenderer(ItemRenderer):
    '''Renderer for photos and videos.'''

    def get_thumbnail_url(self, item: Dict) -> str:
        from app.config import BASE_URL
        return f'{BASE_URL}/files/{item["id"]}/thumbnail'

    def get_full_url(self, item: Dict) -> str:
        from app.config import BASE_URL
        return f'{BASE_URL}/files/{item["id"]}'

    def get_dimensions(self, item: Dict) -> tuple:
        thumb_w = item.get('thumb_width', 280)
        thumb_h = item.get('thumb_height', 210)
        return thumb_w, thumb_h

    def render_gallery_item(self, item: Dict) -> Dict:
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'thumb_url': self.get_thumbnail_url(item),
            'width': item.get('thumb_width', 280),
            'height': item.get('thumb_height', 210),
        }

    def render_lightbox(self, item: Dict) -> Dict:
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'url': self.get_full_url(item),
            'title': item.get('title', ''),
        }


class ItemService:
    '''Service for managing polymorphic items.

    Responsibilities:
    - Create items of any type
    - Route type-specific operations to appropriate renderer
    - Handle uploads for media items
    - Manage item metadata
    '''

    RENDERERS = {
        'media': MediaRenderer(),
    }

    def __init__(
        self,
        item_repository: ItemRepository,
        item_media_repository: ItemMediaRepository,
        storage: Any = None,
    ) -> None:
        self.item_repo = item_repository
        self.media_repo = item_media_repository
        self.storage = storage or get_storage()

    def get_renderer(self, item_type: str) -> ItemRenderer:
        '''Get renderer for item type.'''
        renderer = self.RENDERERS.get(item_type)
        if not renderer:
            raise ValueError(f'Unknown item type: {item_type}')
        return renderer

    # ========================================================================
    # Media Item Creation (Photos/Videos)
    # ========================================================================

    def _validate_content(self, content: bytes, expected_media_type: str) -> bool:
        '''Validate file content by magic bytes.'''
        if len(content) < 4:
            return False

        header = content[:12]

        if header[:2] == b'\xff\xd8':
            return expected_media_type in ('image', 'photo')
        if header[:4] == b'\x89PNG':
            return expected_media_type in ('image', 'photo')
        if header[:3] in (b'GIF87', b'GIF89') or header[:4] == b'GIF8':
            return expected_media_type in ('image', 'photo')
        if header[4:8] == b'WEBP':
            return expected_media_type in ('image', 'photo')
        if is_jxl_content(header):
            return expected_media_type in ('image', 'photo')
        if header[4:8] in (b'ftyp', b'moov'):
            return expected_media_type == 'video'
        if expected_media_type == 'video':
            return len(content) > 1000

        return False

    def _maybe_transcode_to_jxl(
        self,
        content: bytes,
        media_type: str,
        content_type: str,
    ) -> tuple[bytes, str]:
        '''Transcode image uploads to lossless JPEG XL when enabled.

        Falls back to the original content if encoding fails.

        Args:
            content: Raw file bytes.
            media_type: 'image' or 'video'.
            content_type: Original MIME type.

        Returns:
            Tuple of (content, content_type). Content type becomes image/jxl
            when transcoding succeeds.
        '''
        if not USE_JXL:
            return content, content_type

        if media_type != 'image':
            return content, content_type

        if content_type == 'image/jxl':
            return content, content_type

        if not content_type or not content_type.startswith('image/'):
            return content, content_type

        try:
            is_jpeg = content_type == 'image/jpeg'
            transcoded = encode_to_lossless_jxl(content, is_jpeg=is_jpeg)
            logger.info(
                'Transcoded upload to JXL: %s bytes (%s) -> %s bytes (image/jxl)',
                len(content),
                content_type,
                len(transcoded),
            )
            return transcoded, 'image/jxl'
        except JxlEncodeError as exc:
            logger.warning('JXL transcode failed, keeping original: %s', exc)
            return content, content_type

    # ========================================================================
    # Async Upload Processing
    # ========================================================================

    async def process_media_upload(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
    ) -> Dict:
        '''Process complete media upload: validation, thumbnail, storage, DB.

        Args:
            file: Uploaded file
            folder_id: Target folder
            user_id: Owner user ID
            user_dek: User's DEK for server-side encryption

        Returns:
            Created item dict
        '''
        import tempfile

        if not file.filename:
            raise HTTPException(400, 'No filename')

        content_type = file.content_type or 'application/octet-stream'

        if content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(400, f'Invalid file type: {content_type}')

        item_id = str(uuid.uuid4())

        content = await file.read()
        size = len(content)

        if size == 0:
            raise HTTPException(400, 'Empty file')

        media_type = get_media_type(content_type)

        if USE_JXL and not is_jxl_encoding_available():
            raise HTTPException(503, 'JPEG XL encoder (cjxl) is not available')

        if not self._validate_content(content, media_type):
            raise HTTPException(400, f'Invalid file content for type: {content_type}')

        orig_width, orig_height = None, None
        duration = None
        taken_at = None
        png_text_chunks = None

        if media_type == 'image':
            dims = get_image_dimensions(content)
            if dims:
                orig_width, orig_height = dims
            try:
                suffix = '.jxl' if content_type == 'image/jxl' else None
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content)
                    tmp.flush()
                    taken_at = extract_taken_date(Path(tmp.name))
                png_text_chunks = extract_png_text_chunks(content) or None
            except Exception:
                pass
        elif media_type == 'video':
            info = get_video_info(content)
            if info:
                orig_width, orig_height, duration_sec = info
                duration = int(round(duration_sec))

        content, content_type = self._maybe_transcode_to_jxl(
            content, media_type, content_type
        )
        size = len(content)
        if content_type == 'image/jxl' and media_type == 'image':
            dims = get_image_dimensions(content)
            if dims:
                orig_width, orig_height = dims

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

        if user_dek:
            content = EncryptionService.encrypt_file(content, user_dek)
            if thumb_bytes:
                thumb_bytes = EncryptionService.encrypt_file(thumb_bytes, user_dek)
        else:
            raise HTTPException(403, 'Encryption key not available')

        await self.storage.upload(item_id, content, folder='uploads')
        if thumb_bytes:
            await self.storage.upload(item_id, thumb_bytes, folder='thumbnails')

        return self.create_db_records(
            item_id=item_id,
            file_data={
                'filename': file.filename,
                'content_type': content_type,
                'size': size,
                'uploaded_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f'),
                'user_id': user_id,
                'taken_at': taken_at,
            },
            media_data={
                'media_type': media_type,
                'storage_path': f'uploads/{item_id}',
                'thumb_width': thumb_w,
                'thumb_height': thumb_h,
                'width': orig_width,
                'height': orig_height,
                'duration': duration,
                'png_text_chunks': png_text_chunks,
            },
            folder_id=folder_id,
            user_id=user_id,
        )

    # ========================================================================
    # Sync Item Creation
    # ========================================================================

    def create_db_records(
        self,
        item_id: str,
        file_data: dict,
        media_data: dict,
        folder_id: str,
        user_id: int,
    ) -> Dict:
        '''Create database records for uploaded media item.

        Args:
            item_id: Pre-generated item UUID
            file_data: Dict with filename, content_type, size, uploaded_at, user_id
            media_data: Dict with media_type, storage_path, thumbnail dimensions
            folder_id: Target folder
            user_id: Owner

        Returns:
            Created item dict
        '''
        self.item_repo.create(
            item_type='media',
            folder_id=folder_id,
            user_id=user_id,
            item_id=item_id,
            title=file_data.get('filename', ''),
            uploaded_at=file_data.get('uploaded_at'),
        )

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
            file_size=file_data.get('size'),
            png_text_chunks=media_data.get('png_text_chunks'),
        )

        return {
            'id': item_id,
            'type': 'media',
            'folder_id': folder_id,
            'user_id': user_id,
            'uploaded_at': file_data.get('uploaded_at'),
            'title': file_data.get('filename', ''),
            'media_type': media_data.get('media_type', 'image'),
            'content_type': file_data.get('content_type', 'application/octet-stream'),
            'width': media_data.get('width'),
            'height': media_data.get('height'),
            'duration': media_data.get('duration'),
            'thumb_width': media_data.get('thumb_width', 0),
            'thumb_height': media_data.get('thumb_height', 0),
            'taken_at': taken_at,
            'filename': item_id,
        }

    # ========================================================================
    # Generic Item Operations
    # ========================================================================

    def get_item(self, item_id: str) -> Optional[Dict]:
        '''Get full item with type-specific data.'''
        base = self.item_repo.get_by_id(item_id)
        if not base:
            return None

        if base['type'] == 'media':
            media = self.media_repo.get_by_item_id(item_id)
            if media:
                base.update({
                    'media_type': media.get('media_type'),
                    'content_type': media.get('content_type'),
                    'thumb_width': media.get('thumb_width'),
                    'thumb_height': media.get('thumb_height'),
                    'taken_at': media.get('taken_at'),
                })

        return base

    def get_items_by_folder(
        self,
        folder_id: str,
        item_type: Optional[str] = None,
        sort_by: str = 'uploaded',
        standalone_only: bool = False,
    ) -> List[Dict]:
        '''Get items in folder with full data.

        Args:
            folder_id: Folder ID
            item_type: Filter by type ('media', 'note') or None for all
            sort_by: 'uploaded', 'taken', or 'title'
            standalone_only: If True, exclude items that are in albums
        '''
        if item_type == 'media' and not standalone_only:
            return self.media_repo.get_by_folder(folder_id, sort_by=sort_by)

        items = self.item_repo.get_by_folder(folder_id, item_type, sort_by)

        if standalone_only:
            album_item_ids = self._get_album_item_ids(folder_id)
            items = [item for item in items if item['id'] not in album_item_ids]

        for item in items:
            if item['type'] == 'media':
                media = self.media_repo.get_by_item_id(item['id'])
                if media:
                    item.update({
                        'media_type': media.get('media_type'),
                        'content_type': media.get('content_type'),
                        'thumb_width': media.get('thumb_width'),
                        'thumb_height': media.get('thumb_height'),
                        'taken_at': media.get('taken_at'),
                    })

        return items

    def _get_album_item_ids(self, folder_id: str) -> set:
        '''Get IDs of all items that are in albums for a given folder.'''
        from app.infrastructure.repositories import AlbumRepository
        album_repo = AlbumRepository(self.item_repo._conn)
        return album_repo.get_item_ids_by_folder(folder_id)

    def move_item(self, item_id: str, folder_id: str, user_id: int) -> bool:
        '''Move item to different folder.'''
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False

        if item['user_id'] != user_id:
            raise HTTPException(403, 'Not owner')

        return self.item_repo.move_to_folder(item_id, folder_id)

    async def delete_item(self, item_id: str, user_id: int) -> bool:
        '''Delete item and all its data including files.'''
        item = self.item_repo.get_by_id(item_id)
        if not item:
            return False

        if item['user_id'] != user_id:
            raise HTTPException(403, 'Not owner')

        await self._delete_item_files(item_id)

        return self.item_repo.delete(item_id)

    async def _delete_item_files(self, item_id: str) -> None:
        '''Delete item files from storage.'''
        await self.storage.delete(item_id, folder='uploads')
        await self.storage.delete(item_id, folder='thumbnails')

    def copy_item(
        self,
        item_id: str,
        dest_folder_id: str,
        user_id: int,
        source_owner_id: Optional[int] = None,
    ) -> str:
        '''Copy a single item to another folder.

        Returns:
            New item ID
        '''
        from app.config import THUMBNAILS_DIR, UPLOADS_DIR
        from app.infrastructure.services.encryption import EncryptionService, dek_cache

        item = self.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(404, 'Item not found')

        media = self.media_repo.get_by_item_id(item_id)
        if not media:
            raise HTTPException(404, 'Media not found')

        source_owner_id = source_owner_id or item['user_id']

        new_item_id = str(uuid.uuid4())

        old_upload = UPLOADS_DIR / item_id
        new_upload = UPLOADS_DIR / new_item_id
        old_thumb = THUMBNAILS_DIR / item_id
        new_thumb = THUMBNAILS_DIR / new_item_id

        def _copy_and_reencrypt_file(
            old_path: Path,
            new_path: Path,
            source_owner_id: int,
            dest_owner_id: int,
        ) -> bool:
            import os
            import shutil

            if not old_path.exists():
                return False

            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                return False

            try:
                if source_owner_id == dest_owner_id:
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
                    if (
                        len(encrypted_data) > 12
                        and (
                            encrypted_data.startswith(b'\xff\xd8')
                            or encrypted_data.startswith(b'\x89PNG')
                            or encrypted_data[:4] in (b'GIF8', b'GIF9')
                            or encrypted_data[8:12] == b'WEBP'
                            or encrypted_data[4:8] in (b'ftyp', b'moov')
                        )
                    ):
                        plaintext = encrypted_data
                    else:
                        return False

                new_encrypted = EncryptionService.encrypt_file(plaintext, dest_dek)
                new_path.write_bytes(new_encrypted)
                return new_path.exists()
            except Exception:
                return False

        if not _copy_and_reencrypt_file(
            old_upload, new_upload, source_owner_id, user_id
        ):
            raise HTTPException(500, 'Failed to copy file')

        if old_thumb.exists():
            _copy_and_reencrypt_file(
                old_thumb, new_thumb, source_owner_id, user_id
            )

        self.item_repo.create(
            item_type='media',
            folder_id=dest_folder_id,
            user_id=user_id,
            item_id=new_item_id,
            title=item.get('title', 'Untitled'),
            description=item.get('description'),
        )

        self.media_repo.create(
            item_id=new_item_id,
            media_type=media['media_type'],
            original_name=media.get('original_name'),
            content_type=media['content_type'],
            width=media.get('width'),
            height=media.get('height'),
            duration=media.get('duration'),
            thumb_width=media['thumb_width'],
            thumb_height=media['thumb_height'],
            taken_at=media['taken_at'],
            file_size=media.get('file_size'),
            png_text_chunks=media.get('png_text_chunks'),
        )

        conn = self.item_repo._conn
        item_tags = conn.execute(
            'SELECT tag_id FROM item_tags WHERE item_id = ?',
            (item_id,),
        ).fetchall()
        for item_tag in item_tags:
            conn.execute(
                'INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)',
                (new_item_id, item_tag['tag_id']),
            )

        return new_item_id

    # ========================================================================
    # Rendering Helpers
    # ========================================================================

    def render_for_gallery(self, item: Dict) -> Dict:
        '''Render item for gallery view using appropriate strategy.'''
        renderer = self.get_renderer(item['type'])
        return renderer.render_gallery_item(item)

    def render_for_lightbox(self, item: Dict) -> Dict:
        '''Render item for lightbox view using appropriate strategy.'''
        renderer = self.get_renderer(item['type'])
        return renderer.render_lightbox(item)

    def count_items_by_folder(self, folder_id: str) -> int:
        '''Count items in folder.'''
        return self.item_repo.count_by_folder(folder_id)

    # ========================================================================
    # Metadata Operations
    # ========================================================================

    def get_item_metadata(self, item_id: str) -> Optional[Dict]:
        '''Get combined metadata from items and item_media tables.

        Args:
            item_id: Item ID

        Returns:
            Combined metadata dict or None if not found
        '''
        cursor = self.item_repo._execute(
            '''SELECT
                i.id, i.type, i.title, i.description, i.user_id,
                i.uploaded_at, i.updated_at,
                im.media_type, im.original_name, im.content_type,
                im.width, im.height, im.duration, im.taken_at, im.file_size,
                im.png_text_chunks
               FROM items i
               LEFT JOIN item_media im ON i.id = im.item_id
               WHERE i.id = ?''',
            (item_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        metadata = dict(row)
        if metadata.get('png_text_chunks'):
            try:
                metadata['png_text_chunks'] = json.loads(metadata['png_text_chunks'])
            except json.JSONDecodeError:
                metadata['png_text_chunks'] = None
        return metadata

    def update_metadata(
        self,
        item_id: str,
        user_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        taken_at: Optional[datetime] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> Dict:
        '''Update item metadata.

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
        '''
        item = self.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(404, 'Item not found')

        from app.infrastructure.repositories import PermissionRepository
        perm_repo = PermissionRepository(self.item_repo._conn)

        is_owner = item.get('user_id') == user_id
        can_edit = perm_repo.can_edit(item.get('folder_id'), user_id)

        if not is_owner and not can_edit:
            raise HTTPException(403, 'Not owner or editor')

        updated = False

        if title is not None or description is not None:
            if self.item_repo.update_metadata(item_id, title, description):
                updated = True

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

        return {'status': 'ok' if updated else 'no_changes', 'updated': updated}
