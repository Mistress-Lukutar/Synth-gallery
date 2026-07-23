'''
File:   files.py
Brief:  File serving routes for gallery media (streaming + HTTP Range).
Author: Mistress-Lukutar
Date:   2026-07-21
Version: v1.1.0
'''
import io
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.database import create_connection
from app.dependencies import require_user
from app.infrastructure.repositories import ItemRepository, ItemMediaRepository
from app.infrastructure.services.encryption import (
    EncryptionError,
    EncryptionService,
    dek_cache,
)
from app.infrastructure.services.jxl_fallback_service import JxlFallbackService
from app.infrastructure.storage import get_storage
from app.logging_config import get_logger
from app.routes.gallery.deps import get_permission_service

router = APIRouter()
logger = get_logger(__name__)

# Storage backend used by all routes in this module.
storage = get_storage()


def _client_accepts_jxl(request: Request) -> bool:
    '''Return True if the request explicitly accepts image/jxl.'''
    accept = request.headers.get('Accept', '')
    return 'image/jxl' in accept


def _force_jpeg_fallback(request: Request) -> bool:
    '''Return True when the format=jpeg query parameter is present.'''
    return request.query_params.get('format') == 'jpeg'


def _parse_range(header: str, total: int) -> tuple[int, int] | None:
    '''Parse an HTTP ``Range: bytes=`` header.

    Args:
        header: Raw Range header value.
        total: Total plaintext byte length.

    Returns:
        Tuple (start, end) inclusive, or ``None`` if the header is absent
        or syntactically invalid. Suffix ranges (``bytes=-N``) and
        open-ended ranges (``bytes=N-``) are supported.
    '''
    if not header or not header.startswith('bytes='):
        return None
    spec = header[len('bytes=') :].strip()
    if ',' in spec:
        # Multipart ranges are not supported; take the first range only.
        spec = spec.split(',', 1)[0].strip()
    if '-' not in spec:
        return None
    start_str, end_str = spec.split('-', 1)
    try:
        if start_str == '':
            # Suffix: last N bytes.
            n = int(end_str)
            if n <= 0:
                return None
            start = max(0, total - n)
            end = total - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else total - 1
        if start < 0 or start >= total or end < start:
            return None
        if end >= total:
            end = total - 1
        return start, end
    except ValueError:
        return None


def _get_file_record(item_id: str, item_repo: ItemRepository, item_media_repo=None):
    '''Build a photo-like dict for the requested item.

    Dispatch is driven by the item-type registry: today only ``media`` items
    serve files, but the registry check is the single extension point so a
    future type can opt into file serving without reopening this route.
    '''
    from app.application.services.item_types import is_known_item_type

    item = item_repo.get_by_id(item_id)
    if item and is_known_item_type(item.get('type', '')):
        media = item_media_repo.get_by_item_id(item_id) if item_media_repo else None
        return {
            'id': item['id'],
            'filename': item_id,
            'title': item.get('title', item_id),
            'user_id': item.get('user_id'),
            'folder_id': item.get('folder_id'),
            'content_type': (
                media.get('content_type', 'image/jpeg') if media else 'image/jpeg'
            ),
        }
    return None


async def _open_encrypted_reader(filename: str, folder: str):
    '''Return a seekable reader over the stored encrypted envelope.

    Goes through the storage abstraction's ``get_random_access_reader`` so
    any backend that supports random access (local today) works identically.
    Backends that cannot provide a seekable stream (e.g. S3) raise
    ``NotImplementedError`` from the storage layer; range/JXL serving is not
    supported for those until a range-GET reader is added.
    '''
    return storage.get_random_access_reader(filename, folder)


async def _encrypted_size(filename: str, folder: str) -> int:
    '''Return on-disk encrypted envelope size in bytes.'''
    return await storage.get_size(filename, folder)


def _build_headers(
    content_type: str,
    plaintext_size: int,
    accept_ranges: bool = True,
    content_range: tuple[int, int, int] | None = None,
) -> dict[str, str]:
    '''Build response headers for a streaming file response.'''
    headers: dict[str, str] = {
        'Content-Type': content_type,
        'Cache-Control': 'private, max-age=3600',
    }
    if accept_ranges:
        headers['Accept-Ranges'] = 'bytes'
    if content_range is not None:
        start, end, total = content_range
        headers['Content-Range'] = f'bytes {start}-{end}/{total}'
        headers['Content-Length'] = str(end - start + 1)
    else:
        headers['Content-Length'] = str(plaintext_size)
    return headers


async def _serve_jxl_or_fallback(
    request: Request,
    photo_id: str,
    jxl_bytes: bytes,
    dek: bytes,
) -> Response:
    '''Serve JXL directly or generate a JPEG fallback when needed.

    Uses the Accept header and the ``format=jpeg`` query parameter to decide
    which representation to return. Generated fallbacks are encrypted and
    cached with the same DEK as the original.
    '''
    if _force_jpeg_fallback(request) or not _client_accepts_jxl(request):
        fallback_service = JxlFallbackService()
        jpeg_bytes = await fallback_service.get_fallback(photo_id, jxl_bytes, dek)
        return Response(content=jpeg_bytes, media_type='image/jpeg')
    return Response(content=jxl_bytes, media_type='image/jxl')


@router.head('/files/{photo_id}')
@router.head('/files/{photo_id}/thumbnail')
async def file_head(photo_id: str, request: Request):
    '''HEAD metadata for a media file (Content-Length, Accept-Ranges).'''
    user = require_user(request)
    db = create_connection()
    try:
        item_repo = ItemRepository(db)
        item_media_repo = ItemMediaRepository(db)
        record = _get_file_record(photo_id, item_repo, item_media_repo)
        if not record:
            raise HTTPException(status_code=404, detail='Item not found')

        folder_id = record.get('folder_id')
        if folder_id and not get_permission_service(db).can_access(
            folder_id, user['id']
        ):
            raise HTTPException(status_code=403, detail='Access denied')

        if not storage.exists(photo_id, 'uploads'):
            raise HTTPException(status_code=404, detail='File missing')

        owner_id = record.get('user_id')
        if not dek_cache.get(owner_id) if owner_id else True:
            raise HTTPException(status_code=403, detail='Encryption key not available')

        enc_size = await _encrypted_size(photo_id, 'uploads')
        content_type = record.get('content_type') or 'application/octet-stream'
        try:
            plaintext_size = EncryptionService.get_plaintext_size(enc_size)
        except EncryptionError:
            plaintext_size = enc_size

        headers = _build_headers(content_type, plaintext_size)
        return Response(content=b'', headers=headers)
    finally:
        db.close()


@router.get('/files/{photo_id}')
async def get_file(photo_id: str, request: Request):
    '''Stream a media file with optional HTTP Range support.

    Files are decrypted on the fly from the chunked envelope. Range requests
    decrypt only the chunks overlapping ``[start, end]``; non-range requests
    stream the whole file through a bounded-memory decrypt pipe.
    '''
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_repo = ItemRepository(db)
        item_media_repo = ItemMediaRepository(db)

        file_record = _get_file_record(photo_id, item_repo, item_media_repo)
        if not file_record:
            raise HTTPException(status_code=404, detail='Item not found')

        folder_id = file_record.get('folder_id')
        if folder_id and not perm_service.can_access(folder_id, user['id']):
            raise HTTPException(status_code=403, detail='Access denied')

        filename = file_record.get('filename', photo_id)
        content_type = file_record.get('content_type') or 'application/octet-stream'

        owner_id = file_record.get('user_id')
        dek = dek_cache.get(owner_id) if owner_id else None
        if not dek:
            raise HTTPException(
                status_code=403, detail='Encryption key not available'
            )

        enc_size = await _encrypted_size(filename, 'uploads')
        try:
            plaintext_size = EncryptionService.get_plaintext_size(enc_size)
        except EncryptionError as exc:
            raise HTTPException(
                status_code=500, detail=f'Decryption failed: {exc}'
            )

        # JXL path stays whole-file (small images; needs Accept negotiation).
        if content_type == 'image/jxl':
            reader = await _open_encrypted_reader(filename, 'uploads')
            try:
                buf = io.BytesIO()
                EncryptionService.decrypt_to_stream(reader, buf, dek)
                return await _serve_jxl_or_fallback(
                    request, photo_id, buf.getvalue(), dek
                )
            except EncryptionError as exc:
                raise HTTPException(
                    status_code=500, detail=f'Decryption failed: {exc}'
                )
            finally:
                reader.close()

        range_header = request.headers.get('range')
        parsed = _parse_range(range_header, plaintext_size) if range_header else None

        if parsed is not None:
            start, end = parsed
            # Range serving needs a seekable reader so decrypt_range can skip
            # to the chunks overlapping [start, end]. Open it here (awaitable)
            # and hand it to the sync generator.
            reader = await _open_encrypted_reader(filename, 'uploads')

            def _gen():
                try:
                    yield from EncryptionService.decrypt_range(
                        reader, dek, start, end
                    )
                finally:
                    reader.close()

            headers = _build_headers(
                content_type,
                plaintext_size,
                content_range=(start, end, plaintext_size),
            )
            return StreamingResponse(
                _gen(), status_code=206, headers=headers, media_type=content_type
            )

        # Full-file streaming response. iter_decrypt is sequential (no seek),
        # so a plain storage stream works for any backend including S3.
        stream = await storage.get_stream(filename, 'uploads')

        def _gen_full():
            try:
                yield from EncryptionService.iter_decrypt(stream, dek)
            except EncryptionError as exc:
                logger.warning('Stream decrypt failed for %s: %s', filename, exc)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        headers = _build_headers(content_type, plaintext_size)
        return StreamingResponse(
            _gen_full(), headers=headers, media_type=content_type
        )
    finally:
        db.close()


@router.get('/files/{photo_id}/thumbnail')
async def get_file_thumbnail(photo_id: str, request: Request):
    '''Thumbnail access endpoint. Thumbnails are small JPEGs.'''
    user = require_user(request)

    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        item_repo = ItemRepository(db)
        item_media_repo = ItemMediaRepository(db)

        file_record = _get_file_record(photo_id, item_repo, item_media_repo)
        if not file_record:
            raise HTTPException(status_code=404, detail='Item not found')

        folder_id = file_record.get('folder_id')
        if folder_id and not perm_service.can_access(folder_id, user['id']):
            raise HTTPException(status_code=403, detail='Access denied')

        # Auto-regenerate missing thumbnails.
        if not storage.exists(photo_id, 'thumbnails'):
            from app.infrastructure.services.thumbnail import regenerate_thumbnail

            if not regenerate_thumbnail(photo_id, user['id']):
                raise HTTPException(status_code=404, detail='Thumbnail unavailable')

        # Thumbnails are always generated as JPEG, regardless of the original
        # content type (e.g. image/jxl).
        thumbnail_content_type = 'image/jpeg'

        owner_id = file_record.get('user_id')
        dek = dek_cache.get(owner_id) if owner_id else None
        if not dek:
            raise HTTPException(
                status_code=403, detail='Encryption key not available'
            )

        data = await storage.download(photo_id, 'thumbnails')
        try:
            decrypted_data = EncryptionService.decrypt_bytes(data, dek)
        except EncryptionError as exc:
            raise HTTPException(
                status_code=500, detail=f'Thumbnail decryption failed: {exc}'
            )

        headers = {
            'Content-Type': thumbnail_content_type,
            'Cache-Control': 'private, max-age=3600',
            'Content-Length': str(len(decrypted_data)),
        }
        return Response(
            content=decrypted_data, media_type=thumbnail_content_type, headers=headers
        )
    finally:
        db.close()
