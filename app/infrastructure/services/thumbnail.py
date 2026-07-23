'''
File:   thumbnail.py
Brief:  Thumbnail management service - regeneration, cleanup, statistics.
Author: Mistress-Lukutar
Date:   2026-07-23
Version: v1.1.0
'''
import io
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from .encryption import EncryptionService, dek_cache
from .ffmpeg import extract_video_thumbnail_bytes
from .media import create_thumbnail_bytes
from ...database import get_db
from ..storage import get_storage


def _decrypt_stream_to_bytes(reader: BinaryIO, dek: bytes) -> bytes:
    '''Decrypt a chunked envelope from ``reader`` into an in-memory buffer.

    Suitable only for thumbnails / small images; large videos should use the
    ffmpeg path that operates on a temp file instead.
    '''
    buf = io.BytesIO()
    EncryptionService.decrypt_to_stream(reader, buf, dek)
    return buf.getvalue()


def _decrypt_stream_to_temp_file(
    reader: BinaryIO,
    dek: bytes,
    suffix: str = '',
) -> Path:
    '''Decrypt a chunked envelope from ``reader`` to a temp plaintext file.

    Used for video originals that must be passed to ffmpeg by path.
    Caller is responsible for unlinking the returned path.
    '''
    fd, name = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as out:
            EncryptionService.decrypt_to_stream(reader, out, dek)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def _suggest_suffix(media_type: str, content_type: str | None) -> str:
    '''Guess a file suffix for a decrypted original.'''
    if media_type == 'video':
        if content_type and 'matroska' in content_type:
            return '.mkv'
        if content_type and 'webm' in content_type:
            return '.webm'
        if content_type and 'webp' in content_type:
            return '.webp'
        return '.mp4'
    return ''


def _make_thumbnail_for(
    original_path: Path,
    media_type: str,
) -> tuple[bytes, int, int] | None:
    '''Generate thumbnail bytes from a decrypted plaintext original.

    Args:
        original_path: Path to the plaintext original on disk.
        media_type: ``'image'`` or ``'video'``.

    Returns:
        Tuple (jpeg_bytes, width, height), or ``None`` on failure.
    '''
    if media_type == 'video':
        return extract_video_thumbnail_bytes(original_path)
    try:
        data = original_path.read_bytes()
        return create_thumbnail_bytes(data)
    except Exception:
        return None


async def regenerate_thumbnail(photo_id: str, user_id: int = None) -> bool:
    '''Regenerate a single item's thumbnail.

    Args:
        photo_id: The item ID.
        user_id: Optional user ID to look up the DEK.

    Returns:
        True if the thumbnail was regenerated, False otherwise.
    '''
    storage = get_storage()
    db = get_db()
    photo = db.execute(
        """SELECT im.media_type, im.content_type, i.user_id
            FROM item_media im
            JOIN items i ON im.item_id = i.id
            WHERE i.id = ?""",
        (photo_id,),
    ).fetchone()
    if not photo:
        return False

    # Storage is keyed by item_id (extension-less).
    if not storage.exists(photo_id, 'uploads'):
        return False

    dek = None
    if user_id:
        dek = dek_cache.get(user_id)
    if not dek:
        dek = dek_cache.get(photo['user_id'])
    if not dek:
        return False

    temp_plain: Path | None = None
    src_stream = None
    try:
        src_stream = await storage.get_stream(photo_id, 'uploads')
        if photo['media_type'] == 'video':
            # Video: decrypt to temp file (ffmpeg needs a path).
            suffix = _suggest_suffix('video', photo['content_type'])
            temp_plain = _decrypt_stream_to_temp_file(
                src_stream, dek, suffix=suffix
            )
            src_stream.close()
            src_stream = None
            result = _make_thumbnail_for(temp_plain, 'video')
        else:
            # Image: small, decrypt to memory.
            data = _decrypt_stream_to_bytes(src_stream, dek)
            src_stream.close()
            src_stream = None
            result = create_thumbnail_bytes(data)

        if not result:
            return False
        thumb_bytes, _, _ = result

        encrypted_thumb = EncryptionService.encrypt_bytes(thumb_bytes, dek)
        await storage.upload(photo_id, encrypted_thumb, folder='thumbnails')
        return True
    except Exception:
        return False
    finally:
        if src_stream is not None:
            try:
                src_stream.close()
            except Exception:
                pass
        if temp_plain is not None:
            try:
                temp_plain.unlink(missing_ok=True)
            except OSError:
                pass


async def cleanup_orphaned_thumbnails() -> dict:
    '''Remove thumbnails that have no corresponding item in the database.'''
    storage = get_storage()
    db = get_db()

    photos = db.execute("SELECT id FROM items WHERE type = 'media'").fetchall()
    valid_photo_ids = {p['id'] for p in photos}

    orphaned = []
    kept = 0
    for thumb_id in storage.list_files('thumbnails'):
        if thumb_id not in valid_photo_ids:
            orphaned.append(thumb_id)
        else:
            kept += 1

    deleted = 0
    failed = 0
    freed_bytes = 0
    for thumb_id in orphaned:
        try:
            freed_bytes += await storage.get_size(thumb_id, 'thumbnails')
            await storage.delete(thumb_id, 'thumbnails')
            deleted += 1
        except Exception:
            failed += 1

    return {
        'deleted': deleted,
        'failed': failed,
        'kept': kept,
        'freed_bytes': freed_bytes,
    }


async def cleanup_orphaned_uploads() -> dict:
    '''Remove orphaned uploads and DB entries without files.'''
    storage = get_storage()
    db = get_db()

    items = db.execute(
        """SELECT i.id, im.filename
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()

    # ``filename`` is always equal to ``item_id`` (Phase 4 will drop the
    # column); the storage key is the item id in either case.
    valid_item_ids = {item['id'] for item in items}
    item_id_to_id = {item['id']: item['id'] for item in items}

    # Case 1: files on disk without DB entries.
    orphaned_files = []
    for upload_id in storage.list_files('uploads'):
        if upload_id not in valid_item_ids:
            orphaned_files.append(upload_id)

    # Case 2: DB entries without files.
    missing_originals = []
    for item_id in item_id_to_id:
        if not storage.exists(item_id, 'uploads'):
            missing_originals.append(item_id)

    files_deleted = 0
    files_failed = 0
    freed_bytes = 0
    for upload_id in orphaned_files:
        try:
            freed_bytes += await storage.get_size(upload_id, 'uploads')
            await storage.delete(upload_id, 'uploads')
            files_deleted += 1
        except Exception:
            files_failed += 1

    db_deleted = 0
    db_failed = 0
    thumbs_deleted = 0
    for item_id in missing_originals:
        try:
            if storage.exists(item_id, 'thumbnails'):
                await storage.delete(item_id, 'thumbnails')
                thumbs_deleted += 1
            db.execute('DELETE FROM items WHERE id = ?', (item_id,))
            db_deleted += 1
        except Exception:
            db_failed += 1
    db.commit()

    return {
        'files_deleted': files_deleted,
        'files_failed': files_failed,
        'db_deleted': db_deleted,
        'db_failed': db_failed,
        'thumbs_deleted': thumbs_deleted,
        'freed_bytes': freed_bytes,
        'total_deleted': files_deleted + db_deleted,
    }


async def regenerate_missing_thumbnails() -> dict:
    '''Regenerate all missing thumbnails and update stored dimensions.

    Skips items whose DEK is not currently cached.
    '''
    storage = get_storage()
    db = get_db()

    photos = db.execute(
        """SELECT i.id, im.media_type, im.content_type,
                  i.user_id, im.thumb_width
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()

    regenerated = 0
    dimensions_updated = 0
    failed = 0
    skipped = 0
    skipped_encrypted = 0
    already_exists_with_dims = 0

    async def _regenerate_one(photo) -> bool:
        '''Generate thumbnail bytes for one photo and store it.

        Returns True on success (caller updates DB dims), False on failure.
        '''
        dek = dek_cache.get(photo['user_id'])
        if not dek:
            return False
        temp_plain: Path | None = None
        src_stream = None
        try:
            src_stream = await storage.get_stream(photo['id'], 'uploads')
            if photo['media_type'] == 'video':
                suffix = _suggest_suffix('video', photo['content_type'])
                temp_plain = _decrypt_stream_to_temp_file(
                    src_stream, dek, suffix=suffix
                )
                src_stream.close()
                src_stream = None
                result = _make_thumbnail_for(temp_plain, 'video')
            else:
                data = _decrypt_stream_to_bytes(src_stream, dek)
                src_stream.close()
                src_stream = None
                result = create_thumbnail_bytes(data)
            if not result:
                return None  # type: ignore[return-value]
            thumb_bytes, width, height = result
            encrypted_thumb = EncryptionService.encrypt_bytes(thumb_bytes, dek)
            await storage.upload(
                photo['id'], encrypted_thumb, folder='thumbnails'
            )
            db.execute(
                'UPDATE item_media SET thumb_width = ?, thumb_height = ? '
                'WHERE item_id = ?',
                (width, height, photo['id']),
            )
            db.commit()
            return True
        finally:
            if src_stream is not None:
                try:
                    src_stream.close()
                except Exception:
                    pass
            if temp_plain is not None:
                try:
                    temp_plain.unlink(missing_ok=True)
                except OSError:
                    pass

    for photo in photos:
        thumb_exists = storage.exists(photo['id'], 'thumbnails')
        original_exists = storage.exists(photo['id'], 'uploads')

        if not original_exists:
            skipped += 1
            continue

        if thumb_exists:
            if photo['thumb_width'] is None:
                dek = dek_cache.get(photo['user_id'])
                if not dek:
                    skipped_encrypted += 1
                    continue
                try:
                    outcome = await _regenerate_one(photo)
                    if outcome is None:
                        skipped_encrypted += 1
                        continue
                    regenerated += 1
                except Exception:
                    skipped_encrypted += 1
            else:
                already_exists_with_dims += 1
            continue

        # Thumbnail does not exist - create it.
        dek = dek_cache.get(photo['user_id'])
        if not dek:
            skipped_encrypted += 1
            continue

        try:
            outcome = await _regenerate_one(photo)
            if outcome is None:
                failed += 1
                continue
            regenerated += 1
        except Exception:
            failed += 1

    return {
        'regenerated': regenerated,
        'dimensions_updated': dimensions_updated,
        'failed': failed,
        'skipped_no_original': skipped,
        'skipped_encrypted': skipped_encrypted,
        'already_exists': already_exists_with_dims,
        'total': len(photos),
    }


async def get_thumbnail_stats() -> dict:
    '''Return thumbnail statistics for the admin dashboard.'''
    storage = get_storage()
    db = get_db()

    photos = db.execute(
        """SELECT i.id, im.filename, im.thumb_width, i.user_id
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()
    total_photos = len(photos)
    valid_item_ids = {p['id'] for p in photos}

    missing_thumbnails = 0
    missing_dimensions = 0
    missing_originals = 0
    healthy = 0
    encrypted_no_dek = 0

    for photo in photos:
        thumb_exists = storage.exists(photo['id'], 'thumbnails')
        original_exists = storage.exists(photo['id'], 'uploads')

        if not original_exists:
            missing_originals += 1
        elif not thumb_exists:
            missing_thumbnails += 1
        elif photo['thumb_width'] is None:
            missing_dimensions += 1
            missing_thumbnails += 1
            if not dek_cache.get(photo['user_id']):
                encrypted_no_dek += 1
        else:
            healthy += 1

    valid_photo_ids = valid_item_ids
    orphaned_thumbnails = 0
    orphaned_size = 0
    total_thumb_size = 0
    for thumb_id in storage.list_files('thumbnails'):
        try:
            size = await storage.get_size(thumb_id, 'thumbnails')
        except Exception:
            size = 0
        total_thumb_size += size
        if thumb_id not in valid_photo_ids:
            orphaned_thumbnails += 1
            orphaned_size += size

    orphaned_uploads = 0
    orphaned_uploads_size = 0
    uploads_total_size = 0
    for upload_id in storage.list_files('uploads'):
        try:
            size = await storage.get_size(upload_id, 'uploads')
        except Exception:
            size = 0
        uploads_total_size += size
        if upload_id not in valid_item_ids:
            orphaned_uploads += 1
            orphaned_uploads_size += size

    return {
        'total_photos': total_photos,
        'healthy': healthy,
        'missing_thumbnails': missing_thumbnails,
        'missing_dimensions': missing_dimensions,
        'missing_originals': missing_originals,
        'orphaned_thumbnails': orphaned_thumbnails,
        'orphaned_size': orphaned_size,
        'total_thumbnail_size': total_thumb_size,
        'encrypted_no_dek': encrypted_no_dek,
        'orphaned_uploads': orphaned_uploads,
        'orphaned_uploads_size': orphaned_uploads_size,
        'uploads_total_size': uploads_total_size,
    }

