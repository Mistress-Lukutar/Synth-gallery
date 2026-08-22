'''
File:   item_service.py
Brief:  Item service - unified handling for all content types.
Author: Mistress-Lukutar
Date:   2026-07-24
'''

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from app.config import (
    ALLOWED_MEDIA_TYPES,
    TEXT_MAX_SIZE,
    USE_JXL,
)
from app.infrastructure.repositories import (
    ItemMediaRepository,
    ItemRepository,
    ItemTextRepository,
)
from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.services.ffmpeg import (
    extract_video_thumbnail_bytes,
    probe_media,
)
from app.infrastructure.services.jxl import is_jxl_content
from app.infrastructure.services.jxl_encoder import (
    JxlEncodeError,
    encode_to_lossless_jxl,
    is_jxl_encoding_available,
)
from app.infrastructure.services.media import (
    create_thumbnail_bytes,
    get_image_dimensions,
    get_media_type,
)
from app.infrastructure.services.metadata import (
    extract_png_text_chunks,
    extract_taken_date,
)
from app.infrastructure.storage import get_storage

from .item_types import (
    ItemType,
    get_renderer_for,
    resolve_item_type_for_content,
)


logger = logging.getLogger(__name__)


# Magic bytes used by :meth:`ItemService._validate_content`.
# Matroska/WebM files start with the EBML header magic ``1A 45 DF A3``.
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"


class ItemService:
    '''Service for managing polymorphic items.

    Responsibilities:
    - Create items of any type
    - Route type-specific operations to appropriate renderer
    - Handle uploads for media items
    - Manage item metadata
    '''

    def __init__(
        self,
        item_repository: ItemRepository,
        item_media_repository: ItemMediaRepository,
        item_text_repository: Optional[ItemTextRepository] = None,
        storage: Any = None,
    ) -> None:
        self.item_repo = item_repository
        self.media_repo = item_media_repository
        self.text_repo = item_text_repository or ItemTextRepository(
            item_repository._conn
        )
        self.storage = storage or get_storage()

    def get_renderer(self, item_type: str) -> ItemRenderer:
        '''Get renderer for item type (delegates to the item-type registry).'''
        return get_renderer_for(item_type)

    # ========================================================================
    # Media Item Creation (Photos/Videos)
    # ========================================================================

    def _validate_content(self, content: bytes, expected_media_type: str) -> bool:
        '''Validate file content by magic bytes.

        Recognizes JPEG, PNG, GIF, WebP (static and animated), JXL images;
        MP4/MOV (ftyp/moov) and Matroska/WebM (EBML magic) video containers.
        Animated WebP is accepted under either image or video expectation
        because the upload pipeline reclassifies it to ``video``.
        '''
        if len(content) < 4:
            return False

        header = content[:12]

        if header[:2] == b'\xff\xd8':
            return expected_media_type in ('image', 'photo')
        if header[:4] == b'\x89PNG':
            return expected_media_type in ('image', 'photo')
        if header[:3] in (b'GIF87', b'GIF89') or header[:4] == b'GIF8':
            return expected_media_type in ('image', 'photo')
        # WebP container: "RIFF"<size>"WEBP" — signature lives at bytes 8..12.
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return expected_media_type in ('image', 'photo', 'video')
        if is_jxl_content(header):
            return expected_media_type in ('image', 'photo')
        if header[4:8] in (b'ftyp', b'moov'):
            return expected_media_type == 'video'
        # Matroska/WebM and other EBML-based containers (MKV, WebM).
        if header[:4] == _EBML_MAGIC:
            return expected_media_type == 'video'
        if expected_media_type == 'video':
            return len(content) > 1000

        return False

    def _maybe_transcode_to_jxl_file(
        self,
        source_path: Path,
        media_type: str,
        content_type: str,
    ) -> tuple[Path, str, bool]:
        '''Transcode image uploads to lossless JPEG XL when enabled.

        Args:
            source_path: Path to the original plaintext image file.
            media_type: ``'image'`` or ``'video'``.
            content_type: Original MIME type.

        Returns:
            Tuple of ``(path, content_type, produced_tmp)``. When transcoding
            succeeds, ``path`` is a fresh temp file and ``produced_tmp`` is
            True (caller must clean up). Otherwise ``path`` equals the input
            and ``produced_tmp`` is False.
        '''
        if not USE_JXL:
            return source_path, content_type, False
        if media_type != 'image':
            return source_path, content_type, False
        if content_type == 'image/jxl':
            return source_path, content_type, False
        if not content_type or not content_type.startswith('image/'):
            return source_path, content_type, False

        try:
            content = source_path.read_bytes()
            is_jpeg = content_type == 'image/jpeg'
            transcoded = encode_to_lossless_jxl(content, is_jpeg=is_jpeg)
        except JxlEncodeError as exc:
            logger.warning('JXL transcode failed, keeping original: %s', exc)
            return source_path, content_type, False

        fd, name = tempfile.mkstemp(suffix='.jxl')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(transcoded)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise
        logger.info(
            'Transcoded upload to JXL: %s bytes (%s) -> %s bytes (image/jxl)',
            len(content),
            content_type,
            len(transcoded),
        )
        return Path(name), 'image/jxl', True

    @staticmethod
    def _suggest_suffix(content_type: str) -> str:
        '''Return a file suffix matching ``content_type`` for ffmpeg/ffprobe.'''
        mapping = {
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/gif': '.gif',
            'image/webp': '.webp',
            'image/jxl': '.jxl',
            'video/mp4': '.mp4',
            'video/webm': '.webm',
            'video/x-matroska': '.mkv',
            'video/x-mkv': '.mkv',
            'video/webp': '.webp',
        }
        return mapping.get(content_type, '')

    @staticmethod
    def _is_animated_webp(path: Path) -> bool:
        '''Return True if ``path`` is an animated WebP (more than one frame).

        Used to reclassify heavy/animated WebP uploads as ``video`` so they
        are thumbnailed via ffmpeg and shown with a video badge.
        '''
        try:
            from PIL import Image
            with Image.open(path) as img:
                return getattr(img, 'n_frames', 1) > 1
        except Exception:
            return False

    @staticmethod
    async def _spool_upload(file: UploadFile, dest: Path) -> int:
        '''Stream an :class:`UploadFile` to ``dest`` in fixed-size chunks.

        Returns the total number of bytes written. Memory usage is bounded
        by the chunk size regardless of upload size.
        '''
        chunk_size = 1 << 20  # 1 MiB
        total = 0
        src = file.file
        with dest.open('wb') as out:
            while True:
                buf = src.read(chunk_size)
                if not buf:
                    break
                if isinstance(buf, str):
                    buf = buf.encode('utf-8')
                out.write(buf)
                total += len(buf)
        return total

    # ========================================================================
    # Async Upload Processing
    # ========================================================================

    async def process_upload(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
    ) -> Dict:
        '''Dispatch an upload to the handler for its item type.

        The item-type registry drives the routing: the first registered
        type whose allowed MIME set matches the upload's content type
        handles the file. Unknown content types are rejected here so both
        handlers can assume a validated type.

        Args:
            file: Uploaded file.
            folder_id: Target folder.
            user_id: Owner user ID.
            user_dek: User's DEK for server-side encryption.

        Returns:
            Created item dict.
        '''
        content_type = file.content_type or 'application/octet-stream'
        if content_type == 'application/octet-stream':
            content_type = self._infer_note_content_type(file.filename)

        item_type = resolve_item_type_for_content(content_type)
        if item_type is None:
            raise HTTPException(400, f'Invalid file type: {content_type}')

        if item_type == ItemType.NOTE.value:
            file.file.seek(0)
            return await self.process_note_upload(
                file=file,
                folder_id=folder_id,
                user_id=user_id,
                user_dek=user_dek,
            )
        return await self.process_media_upload(
            file=file,
            folder_id=folder_id,
            user_id=user_id,
            user_dek=user_dek,
        )

    @staticmethod
    def _infer_note_content_type(filename: Optional[str]) -> str:
        '''Map a note file extension to its MIME type.

        Browsers report ``application/octet-stream`` for less common text
        extensions (yaml, md); the extension decides the content type for
        those. Returns the input MIME unchanged for anything else.
        '''
        if not filename:
            return 'application/octet-stream'
        suffix = Path(filename).suffix.lower()
        return {
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.csv': 'text/csv',
            '.yaml': 'text/yaml',
            '.yml': 'text/yaml',
        }.get(suffix, 'application/octet-stream')

    @staticmethod
    def _decode_text(raw: bytes) -> tuple[str, str]:
        '''Decode text bytes, auto-detecting the encoding.

        Tries UTF-8 (with or without BOM), UTF-16 (BOM) and finally
        cp1251 (legacy Windows text, common for older Russian files).
        cp1251 accepts any byte sequence, so decoding always succeeds.

        Returns:
            Tuple of (decoded_text, encoding_name).
        '''
        for encoding in ('utf-8-sig', 'utf-16'):
            try:
                return raw.decode(encoding), encoding
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw.decode('cp1251'), 'cp1251'

    async def process_note_upload(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
    ) -> Dict:
        '''Process a text-note upload (txt/md/json/csv/yaml).

        The content is validated as decodable text (binary files are
        rejected), measured (chars/lines/encoding) and stored as an
        encrypted SGE1 envelope in storage — identical to media files, so
        serving, backups and batch download work through the same
        pipeline.

        Args:
            file: Uploaded file.
            folder_id: Target folder.
            user_id: Owner user ID.
            user_dek: User's DEK for server-side encryption.

        Returns:
            Created item dict.
        '''
        if not file.filename:
            raise HTTPException(400, 'No filename')

        if user_dek is None:
            raise HTTPException(403, 'Encryption key not available')

        content_type = file.content_type or 'application/octet-stream'
        if content_type == 'application/octet-stream':
            content_type = self._infer_note_content_type(file.filename)
        if resolve_item_type_for_content(content_type) != ItemType.NOTE.value:
            raise HTTPException(400, f'Invalid file type: {content_type}')

        raw = await file.read()
        if not raw:
            raise HTTPException(400, 'Empty file')
        if len(raw) > TEXT_MAX_SIZE:
            raise HTTPException(
                413,
                f'Text file exceeds the {TEXT_MAX_SIZE // (1024 * 1024)} MB limit',
            )

        # Server-side content validation: it must be decodable text
        # without NUL bytes (binary content masquerading as text).
        text, encoding = self._decode_text(raw)
        if '\x00' in text:
            raise HTTPException(400, 'File is not a text file')

        item_id = str(uuid.uuid4())

        # Store re-encoded as UTF-8 so serving always works with a single
        # canonical charset; ``encoding`` records the source encoding.
        import io as _io

        with _io.BytesIO(text.encode('utf-8')) as plaintext_reader:
            await self._encrypt_and_upload(item_id, plaintext_reader, user_dek)

        line_count = text.count('\n') + (0 if text.endswith('\n') or not text else 1)
        self.item_repo.create(
            item_type=ItemType.NOTE.value,
            folder_id=folder_id,
            user_id=user_id,
            item_id=item_id,
            title=file.filename,
            uploaded_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f'),
        )
        self.text_repo.create(
            item_id=item_id,
            content_type=content_type,
            original_name=file.filename,
            encoding=encoding,
            char_count=len(text),
            line_count=line_count,
        )

        return {
            'id': item_id,
            'type': ItemType.NOTE.value,
            'folder_id': folder_id,
            'user_id': user_id,
            'title': file.filename,
            'content_type': content_type,
            'encoding': encoding,
            'char_count': len(text),
            'line_count': line_count,
            'filename': item_id,
        }

    async def process_media_upload(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
    ) -> Dict:
        '''Process a media upload with bounded memory usage.

        Streams the incoming :class:`UploadFile` to a plaintext temp file,
        probes it for metadata, generates a thumbnail, then streams the
        encryption envelope directly into storage. The full plaintext is
        never held in memory all at once, so arbitrarily large files
        (including multi-GiB MKVs) can be processed.

        Args:
            file: Uploaded file.
            folder_id: Target folder.
            user_id: Owner user ID.
            user_dek: User's DEK for server-side encryption.

        Returns:
            Created item dict.
        '''
        if not file.filename:
            raise HTTPException(400, 'No filename')

        content_type = file.content_type or 'application/octet-stream'

        if content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(400, f'Invalid file type: {content_type}')

        if user_dek is None:
            raise HTTPException(403, 'Encryption key not available')

        if USE_JXL and not is_jxl_encoding_available():
            raise HTTPException(503, 'JPEG XL encoder (cjxl) is not available')

        media_type = get_media_type(content_type)

        item_id = str(uuid.uuid4())
        suffix = self._suggest_suffix(content_type)

        # Stage 1: stream the raw upload to a plaintext temp file.
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False
        ) as tmp:
            plain_path = Path(tmp.name)
        try:
            size = await self._spool_upload(file, plain_path)
            if size == 0:
                raise HTTPException(400, 'Empty file')

            # Validate by magic bytes.
            with plain_path.open('rb') as f:
                head = f.read(12)
            if not self._validate_content(head, media_type):
                raise HTTPException(
                    400, f'Invalid file content for type: {content_type}'
                )

            # Reclassify animated WebP as video so it is thumbnailed via ffmpeg
            # and shown with a video badge. Static WebP stays an image.
            if media_type == 'image' and content_type == 'image/webp':
                if self._is_animated_webp(plain_path):
                    media_type = 'video'
                    content_type = 'video/webp'

            # Stage 2: probe metadata + thumbnail from the plaintext file.
            orig_width: Optional[int] = None
            orig_height: Optional[int] = None
            duration: Optional[int] = None
            taken_at: Optional[str] = None
            png_text_chunks: Optional[str] = None
            thumb_w = 0
            thumb_h = 0
            thumb_bytes: Optional[bytes] = None

            if media_type == 'image':
                content_bytes = plain_path.read_bytes()
                dims = get_image_dimensions(content_bytes)
                if dims:
                    orig_width, orig_height = dims
                try:
                    taken_at = extract_taken_date(plain_path)
                except Exception:
                    pass
                png_text_chunks = extract_png_text_chunks(content_bytes) or None
                try:
                    thumb_bytes, thumb_w, thumb_h = create_thumbnail_bytes(
                        content_bytes
                    )
                except Exception:
                    pass
                # Hand off content_bytes to the optional JXL transcode path.
                # _maybe_transcode_to_jxl_file expects a path; we have one.
            elif media_type == 'video':
                info = probe_media(plain_path)
                if info:
                    orig_width = info.get('width') or None
                    orig_height = info.get('height') or None
                    if info.get('duration') is not None:
                        duration = int(round(info['duration']))
                try:
                    taken_at = extract_taken_date(plain_path)
                except Exception:
                    pass
                try:
                    thumb = extract_video_thumbnail_bytes(plain_path)
                    if thumb is not None:
                        thumb_bytes, thumb_w, thumb_h = thumb
                except Exception:
                    pass

            # Stage 3: optional JXL transcode (image only).
            final_path, final_content_type, produced_tmp = (
                self._maybe_transcode_to_jxl_file(
                    plain_path, media_type, content_type
                )
            )
            content_type = final_content_type
            try:
                # Recompute image dimensions if we transcoded.
                if (
                    content_type == 'image/jxl'
                    and media_type == 'image'
                    and produced_tmp
                ):
                    data = final_path.read_bytes()
                    dims = get_image_dimensions(data)
                    if dims:
                        orig_width, orig_height = dims

                # Re-measure final size from disk (post-transcode).
                size = final_path.stat().st_size

                # Stage 4: stream-encrypt into storage.
                with final_path.open('rb') as plaintext_reader:
                    await self._encrypt_and_upload(
                        item_id, plaintext_reader, user_dek
                    )

                if thumb_bytes:
                    enc_thumb = EncryptionService.encrypt_bytes(thumb_bytes, user_dek)
                    await self.storage.upload(
                        item_id, enc_thumb, folder='thumbnails'
                    )

                return self.create_db_records(
                    item_id=item_id,
                    file_data={
                        'filename': file.filename,
                        'content_type': content_type,
                        'size': size,
                        'uploaded_at': datetime.utcnow().strftime(
                            '%Y-%m-%d %H:%M:%S.%f'
                        ),
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
            finally:
                if produced_tmp:
                    try:
                        final_path.unlink(missing_ok=True)
                    except OSError:
                        pass
        finally:
            try:
                plain_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _encrypt_and_upload(
        self,
        item_id: str,
        plaintext_reader: BinaryIO,
        dek: bytes,
    ) -> None:
        '''Stream-encrypt ``plaintext_reader`` into storage as ``item_id``.

        Writes the encrypted envelope to a temp file first (so storage.upload
        sees a normal seekable file), then hands the temp file to the storage
        backend.
        '''
        # Storage.upload accepts BinaryIO and streams it; we pipe plaintext
        # through an in-process encryption stream into another temp file, then
        # pass that temp file to storage.
        fd, tmp_name = tempfile.mkstemp(prefix=item_id + '.', suffix='.enc')
        try:
            with os.fdopen(fd, 'wb') as enc_writer:
                EncryptionService.encrypt_to_stream(
                    plaintext_reader, enc_writer, dek
                )
            with open(tmp_name, 'rb') as enc_reader:
                await self.storage.upload(item_id, enc_reader, folder='uploads')
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

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
            item_type=ItemType.MEDIA.value,
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
            'type': ItemType.MEDIA.value,
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

        if base['type'] == ItemType.MEDIA.value:
            media = self.media_repo.get_by_item_id(item_id)
            if media:
                base.update({
                    'media_type': media.get('media_type'),
                    'content_type': media.get('content_type'),
                    'thumb_width': media.get('thumb_width'),
                    'thumb_height': media.get('thumb_height'),
                    'taken_at': media.get('taken_at'),
                })
        elif base['type'] == ItemType.NOTE.value:
            text = self.text_repo.get_by_item_id(item_id)
            if text:
                base.update({
                    'content_type': text.get('content_type'),
                    'original_name': text.get('original_name'),
                    'encoding': text.get('encoding'),
                    'char_count': text.get('char_count'),
                    'line_count': text.get('line_count'),
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
        if item_type == ItemType.MEDIA.value and not standalone_only:
            return self.item_repo.get_media_with_details(
                folder_id, sort_by=sort_by
            )

        items = self.item_repo.get_by_folder(folder_id, item_type, sort_by)

        if standalone_only:
            album_item_ids = self._get_album_item_ids(folder_id)
            items = [item for item in items if item['id'] not in album_item_ids]

        for item in items:
            if item['type'] == ItemType.MEDIA.value:
                media = self.media_repo.get_by_item_id(item['id'])
                if media:
                    item.update({
                        'media_type': media.get('media_type'),
                        'content_type': media.get('content_type'),
                        'thumb_width': media.get('thumb_width'),
                        'thumb_height': media.get('thumb_height'),
                        'taken_at': media.get('taken_at'),
                    })
            elif item['type'] == ItemType.NOTE.value:
                text = self.text_repo.get_by_item_id(item['id'])
                if text:
                    item.update({
                        'content_type': text.get('content_type'),
                        'original_name': text.get('original_name'),
                        'encoding': text.get('encoding'),
                        'char_count': text.get('char_count'),
                        'line_count': text.get('line_count'),
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

    async def copy_item(
        self,
        item_id: str,
        dest_folder_id: str,
        user_id: int,
        source_owner_id: Optional[int] = None,
    ) -> str:
        '''Copy a single item to another folder.

        File copying goes through the storage abstraction:

        - same owner: a backend-native byte-exact copy
          (``storage.copy`` — ``shutil.copy2`` locally, ``copy_object`` on
          S3), since the envelope is already valid for the destination DEK.
        - cross owner: decrypt the source stream and re-encrypt into the
          destination via two temp files, then ``storage.upload``. Memory
          usage stays bounded by the chunk size for arbitrarily large files.

        Returns:
            New item ID
        '''
        from app.infrastructure.services.encryption import dek_cache

        item = self.item_repo.get_by_id(item_id)
        if not item:
            raise HTTPException(404, 'Item not found')

        media = None
        text = None
        if item['type'] == ItemType.NOTE.value:
            text = self.text_repo.get_by_item_id(item_id)
            if not text:
                raise HTTPException(404, 'Text metadata not found')
        else:
            media = self.media_repo.get_by_item_id(item_id)
            if not media:
                raise HTTPException(404, 'Media not found')

        source_owner_id = source_owner_id or item['user_id']
        new_item_id = str(uuid.uuid4())

        async def _copy_storage_object(
            folder: str,
            source_owner_id: int,
            dest_owner_id: int,
        ) -> bool:
            '''Copy one encrypted object (uploads or thumbnails) via storage.'''
            if not self.storage.exists(item_id, folder):
                return False

            if source_owner_id == dest_owner_id:
                # Envelope already valid for the dest DEK: native copy.
                await self.storage.copy(
                    item_id, new_item_id, folder, folder
                )
                return True

            source_dek = dek_cache.get(source_owner_id)
            dest_dek = dek_cache.get(dest_owner_id)
            if not source_dek or not dest_dek:
                return False

            # Decrypt source -> temp plaintext, then re-encrypt -> temp cipher,
            # then upload. Bounded memory; works for arbitrarily large files.
            plain_fd, plain_name = tempfile.mkstemp(prefix='copy-plain-')
            enc_fd, enc_name = tempfile.mkstemp(prefix='copy-enc-')
            try:
                src_stream = await self.storage.get_stream(item_id, folder)
                try:
                    with os.fdopen(plain_fd, 'wb') as plain_writer:
                        EncryptionService.decrypt_to_stream(
                            src_stream, plain_writer, source_dek
                        )
                finally:
                    try:
                        src_stream.close()
                    except Exception:
                        pass
                with open(plain_name, 'rb') as plain_reader, \
                        os.fdopen(enc_fd, 'wb') as enc_writer:
                    EncryptionService.encrypt_to_stream(
                        plain_reader, enc_writer, dest_dek
                    )
                with open(enc_name, 'rb') as enc_reader:
                    await self.storage.upload(
                        new_item_id, enc_reader, folder=folder
                    )
                return True
            except Exception as exc:
                logger.warning('copy_item: re-encrypt failed (%s): %s', folder, exc)
                return False
            finally:
                for name in (plain_name, enc_name):
                    try:
                        os.unlink(name)
                    except OSError:
                        pass

        if not await _copy_storage_object('uploads', source_owner_id, user_id):
            raise HTTPException(500, 'Failed to copy file')

        if self.storage.exists(item_id, 'thumbnails'):
            await _copy_storage_object('thumbnails', source_owner_id, user_id)

        self.item_repo.create(
            item_type=item['type'],
            folder_id=dest_folder_id,
            user_id=user_id,
            item_id=new_item_id,
            title=item.get('title', 'Untitled'),
            description=item.get('description'),
        )

        if text is not None:
            self.text_repo.create(
                item_id=new_item_id,
                content_type=text['content_type'],
                original_name=text.get('original_name'),
                encoding=text.get('encoding', 'utf-8'),
                char_count=text.get('char_count', 0),
                line_count=text.get('line_count', 0),
            )
        else:
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
        '''Get combined metadata from items and its type detail table.

        Args:
            item_id: Item ID

        Returns:
            Combined metadata dict or None if not found
        '''
        cursor = self.item_repo._execute(
            '''SELECT
                i.id, i.type, i.title, i.description, i.user_id,
                i.uploaded_at, i.updated_at,
                im.media_type,
                COALESCE(im.original_name, it.original_name) AS original_name,
                COALESCE(im.content_type, it.content_type) AS content_type,
                im.width, im.height, im.duration, im.taken_at, im.file_size,
                im.png_text_chunks,
                it.encoding AS text_encoding, it.char_count, it.line_count
               FROM items i
               LEFT JOIN item_media im ON i.id = im.item_id
               LEFT JOIN item_texts it ON i.id = it.item_id
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
        png_text_chunks: Optional[dict] = None,
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
            png_text_chunks: New PNG text chunks dict (optional)

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
        if png_text_chunks is not None:
            media_updates['png_text_chunks'] = png_text_chunks

        if media_updates:
            if self.media_repo.update(item_id, **media_updates):
                updated = True

        return {'status': 'ok' if updated else 'no_changes', 'updated': updated}
