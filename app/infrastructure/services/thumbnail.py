'''
File:   thumbnail.py
Brief:  Thumbnail management service - regeneration, cleanup, statistics.
Author: Mistress-Lukutar
Date:   2026-07-21
Version: v1.1.0
'''
import io
import tempfile
from pathlib import Path

from .encryption import EncryptionService, dek_cache
from .ffmpeg import extract_video_thumbnail_bytes
from .media import create_thumbnail_bytes
from ...database import get_db


def _decrypt_to_bytes(path: Path, dek: bytes) -> bytes:
    '''Decrypt a chunked envelope into a single in-memory buffer.

    Suitable only for thumbnails / small images; large videos should use the
    ffmpeg path that operates on a temp file instead.
    '''
    buf = io.BytesIO()
    with path.open('rb') as reader:
        EncryptionService.decrypt_to_stream(reader, buf, dek)
    return buf.getvalue()


def _decrypt_to_temp_file(
    path: Path,
    dek: bytes,
    suffix: str = '',
) -> Path:
    '''Decrypt a chunked envelope to a temp plaintext file.

    Used for video originals that must be passed to ffmpeg by path.
    Caller is responsible for unlinking the returned path.
    '''
    fd, name = tempfile.mkstemp(suffix=suffix)
    import os

    try:
        with os.fdopen(fd, 'wb') as out, path.open('rb') as reader:
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


def regenerate_thumbnail(photo_id: str, user_id: int = None) -> bool:
    '''Regenerate a single item's thumbnail.

    Args:
        photo_id: The item ID.
        user_id: Optional user ID to look up the DEK.

    Returns:
        True if the thumbnail was regenerated, False otherwise.
    '''
    from ...config import THUMBNAILS_DIR, UPLOADS_DIR

    db = get_db()
    photo = db.execute(
        """SELECT im.filename, im.media_type, im.content_type, i.user_id
            FROM item_media im
            JOIN items i ON im.item_id = i.id
            WHERE i.id = ?""",
        (photo_id,),
    ).fetchone()
    if not photo:
        return False

    original_path = UPLOADS_DIR / photo['filename']
    if not original_path.exists():
        return False

    thumb_path = THUMBNAILS_DIR / photo_id

    dek = None
    if user_id:
        dek = dek_cache.get(user_id)
    if not dek:
        dek = dek_cache.get(photo['user_id'])
    if not dek:
        return False

    temp_plain: Path | None = None
    try:
        if photo['media_type'] == 'video':
            # Video: decrypt to temp file (ffmpeg needs a path).
            suffix = _suggest_suffix('video', photo['content_type'])
            temp_plain = _decrypt_to_temp_file(original_path, dek, suffix=suffix)
            result = _make_thumbnail_for(temp_plain, 'video')
        else:
            # Image: small, decrypt to memory.
            data = _decrypt_to_bytes(original_path, dek)
            result = create_thumbnail_bytes(data)

        if not result:
            return False
        thumb_bytes, _, _ = result

        encrypted_thumb = EncryptionService.encrypt_bytes(thumb_bytes, dek)
        thumb_path.write_bytes(encrypted_thumb)
        return True
    except Exception:
        return False
    finally:
        if temp_plain is not None:
            try:
                temp_plain.unlink(missing_ok=True)
            except OSError:
                pass


def cleanup_orphaned_thumbnails() -> dict:
    '''Remove thumbnails that have no corresponding item in the database.'''
    from ...config import THUMBNAILS_DIR

    db = get_db()

    photos = db.execute("SELECT id FROM items WHERE type = 'media'").fetchall()
    valid_photo_ids = {p['id'] for p in photos}

    orphaned = []
    kept = 0
    for thumb_file in THUMBNAILS_DIR.iterdir():
        if thumb_file.is_file():
            if thumb_file.stem not in valid_photo_ids:
                orphaned.append(thumb_file)
            else:
                kept += 1

    deleted = 0
    failed = 0
    freed_bytes = 0
    for thumb_file in orphaned:
        try:
            freed_bytes += thumb_file.stat().st_size
            thumb_file.unlink()
            deleted += 1
        except Exception:
            failed += 1

    return {
        'deleted': deleted,
        'failed': failed,
        'kept': kept,
        'freed_bytes': freed_bytes,
    }


def cleanup_orphaned_uploads() -> dict:
    '''Remove orphaned uploads and DB entries without files.'''
    from ...config import THUMBNAILS_DIR, UPLOADS_DIR

    db = get_db()

    items = db.execute(
        """SELECT i.id, im.filename
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()

    valid_filenames = {item['filename'] for item in items}
    item_id_to_filename = {item['id']: item['filename'] for item in items}

    # Case 1: files on disk without DB entries.
    orphaned_files = []
    for upload_file in UPLOADS_DIR.iterdir():
        if upload_file.is_file() and upload_file.name not in valid_filenames:
            orphaned_files.append(upload_file)

    # Case 2: DB entries without files.
    missing_originals = []
    for item_id, filename in item_id_to_filename.items():
        if not (UPLOADS_DIR / filename).exists():
            missing_originals.append((item_id, filename))

    files_deleted = 0
    files_failed = 0
    freed_bytes = 0
    for upload_file in orphaned_files:
        try:
            freed_bytes += upload_file.stat().st_size
            upload_file.unlink()
            files_deleted += 1
        except Exception:
            files_failed += 1

    db_deleted = 0
    db_failed = 0
    thumbs_deleted = 0
    for item_id, _ in missing_originals:
        try:
            thumb_path = THUMBNAILS_DIR / item_id
            if thumb_path.exists():
                thumb_path.unlink()
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


def regenerate_missing_thumbnails() -> dict:
    '''Regenerate all missing thumbnails and update stored dimensions.

    Skips items whose DEK is not currently cached.
    '''
    from ...config import THUMBNAILS_DIR, UPLOADS_DIR

    db = get_db()

    photos = db.execute(
        """SELECT i.id, im.filename, im.media_type, im.content_type,
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

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / photo['id']
        original_path = UPLOADS_DIR / photo['filename']

        if not original_path.exists():
            skipped += 1
            continue

        if thumb_path.exists():
            if photo['thumb_width'] is None:
                dek = dek_cache.get(photo['user_id'])
                if not dek:
                    skipped_encrypted += 1
                    continue
                temp_plain: Path | None = None
                try:
                    if photo['media_type'] == 'video':
                        suffix = _suggest_suffix('video', photo['content_type'])
                        temp_plain = _decrypt_to_temp_file(
                            original_path, dek, suffix=suffix
                        )
                        result = _make_thumbnail_for(temp_plain, 'video')
                    else:
                        data = _decrypt_to_bytes(original_path, dek)
                        result = create_thumbnail_bytes(data)
                    if not result:
                        skipped_encrypted += 1
                        continue
                    thumb_bytes, width, height = result
                    encrypted_thumb = EncryptionService.encrypt_bytes(
                        thumb_bytes, dek
                    )
                    thumb_path.write_bytes(encrypted_thumb)
                    db.execute(
                        'UPDATE item_media SET thumb_width = ?, thumb_height = ? '
                        'WHERE item_id = ?',
                        (width, height, photo['id']),
                    )
                    db.commit()
                    regenerated += 1
                except Exception:
                    skipped_encrypted += 1
                finally:
                    if temp_plain is not None:
                        try:
                            temp_plain.unlink(missing_ok=True)
                        except OSError:
                            pass
            else:
                already_exists_with_dims += 1
            continue

        # Thumbnail does not exist - create it.
        dek = dek_cache.get(photo['user_id'])
        if not dek:
            skipped_encrypted += 1
            continue

        temp_plain = None
        try:
            if photo['media_type'] == 'video':
                suffix = _suggest_suffix('video', photo['content_type'])
                temp_plain = _decrypt_to_temp_file(
                    original_path, dek, suffix=suffix
                )
                result = _make_thumbnail_for(temp_plain, 'video')
            else:
                data = _decrypt_to_bytes(original_path, dek)
                result = create_thumbnail_bytes(data)
            if not result:
                failed += 1
                continue
            thumb_bytes, width, height = result
            encrypted_thumb = EncryptionService.encrypt_bytes(thumb_bytes, dek)
            thumb_path.write_bytes(encrypted_thumb)
            db.execute(
                'UPDATE item_media SET thumb_width = ?, thumb_height = ? '
                'WHERE item_id = ?',
                (width, height, photo['id']),
            )
            db.commit()
            regenerated += 1
        except Exception:
            failed += 1
        finally:
            if temp_plain is not None:
                try:
                    temp_plain.unlink(missing_ok=True)
                except OSError:
                    pass

    return {
        'regenerated': regenerated,
        'dimensions_updated': dimensions_updated,
        'failed': failed,
        'skipped_no_original': skipped,
        'skipped_encrypted': skipped_encrypted,
        'already_exists': already_exists_with_dims,
        'total': len(photos),
    }


def get_thumbnail_stats() -> dict:
    '''Return thumbnail statistics for the admin dashboard.'''
    from ...config import THUMBNAILS_DIR, UPLOADS_DIR

    db = get_db()

    photos = db.execute(
        """SELECT i.id, im.filename, im.thumb_width, i.user_id
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()
    total_photos = len(photos)
    valid_filenames = {p['filename'] for p in photos}

    missing_thumbnails = 0
    missing_dimensions = 0
    missing_originals = 0
    healthy = 0
    encrypted_no_dek = 0

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / photo['id']
        original_path = UPLOADS_DIR / photo['filename']

        if not original_path.exists():
            missing_originals += 1
        elif not thumb_path.exists():
            missing_thumbnails += 1
        elif photo['thumb_width'] is None:
            missing_dimensions += 1
            missing_thumbnails += 1
            if not dek_cache.get(photo['user_id']):
                encrypted_no_dek += 1
        else:
            healthy += 1

    valid_photo_ids = {p['id'] for p in photos}
    orphaned_thumbnails = 0
    orphaned_size = 0
    for thumb_file in THUMBNAILS_DIR.iterdir():
        if thumb_file.is_file() and thumb_file.stem not in valid_photo_ids:
            orphaned_thumbnails += 1
            try:
                orphaned_size += thumb_file.stat().st_size
            except Exception:
                pass

    orphaned_uploads = 0
    orphaned_uploads_size = 0
    uploads_total_size = 0
    for upload_file in UPLOADS_DIR.iterdir():
        if upload_file.is_file():
            try:
                file_size = upload_file.stat().st_size
                uploads_total_size += file_size
            except Exception:
                file_size = 0
            if upload_file.name not in valid_filenames:
                orphaned_uploads += 1
                orphaned_uploads_size += file_size

    total_thumb_size = sum(
        f.stat().st_size for f in THUMBNAILS_DIR.iterdir() if f.is_file()
    )

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
