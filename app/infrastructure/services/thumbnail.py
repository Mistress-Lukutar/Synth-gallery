"""Thumbnail management service - regeneration, cleanup, statistics."""

from .encryption import EncryptionService, dek_cache
from .media import (
    create_thumbnail, create_video_thumbnail,
    create_thumbnail_bytes, create_video_thumbnail_bytes
)
from ...database import get_db


def regenerate_thumbnail(photo_id: str, user_id: int = None) -> bool:
    """Regenerate thumbnail for a single photo.

    Args:
        photo_id: The photo ID
        user_id: Optional user ID to get DEK from cache (for encrypted files)

    Returns True if thumbnail was successfully regenerated, False otherwise.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    db = get_db()
    photo = db.execute(
        "SELECT filename, media_type, is_encrypted, user_id FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    # Find original file
    original_path = UPLOADS_DIR / photo["filename"]
    if not original_path.exists():
        return False

    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"

    # Handle encrypted files
    if photo["is_encrypted"]:
        # Try to get DEK from cache - check requesting user first, then owner
        dek = None
        if user_id:
            dek = dek_cache.get(user_id)
        if not dek:
            dek = dek_cache.get(photo["user_id"])

        if not dek:
            # Cannot regenerate without DEK
            return False

        try:
            # Read and decrypt original
            with open(original_path, "rb") as f:
                encrypted_data = f.read()
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)

            # Create thumbnail from decrypted bytes
            if photo["media_type"] == "video":
                thumb_bytes, _, _ = create_video_thumbnail_bytes(decrypted_data)
            else:
                thumb_bytes, _, _ = create_thumbnail_bytes(decrypted_data)

            # Encrypt and save thumbnail
            encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, dek)
            with open(thumb_path, "wb") as f:
                f.write(encrypted_thumb)
            return True
        except Exception:
            return False

    # Unencrypted file - use original path-based functions
    try:
        if photo["media_type"] == "video":
            create_video_thumbnail(original_path, thumb_path)
        else:
            create_thumbnail(original_path, thumb_path)
        return True
    except Exception:
        return False


def cleanup_orphaned_thumbnails() -> dict:
    """Remove thumbnails that don't have corresponding photos in database.

    Returns dict with cleanup statistics.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import THUMBNAILS_DIR
    
    db = get_db()

    # Get all photo IDs from database
    photos = db.execute("SELECT id FROM photos").fetchall()
    valid_photo_ids = {p["id"] for p in photos}

    # Scan thumbnails directory
    orphaned = []
    kept = 0

    for thumb_file in THUMBNAILS_DIR.glob("*.jpg"):
        photo_id = thumb_file.stem
        if photo_id not in valid_photo_ids:
            orphaned.append(thumb_file)
        else:
            kept += 1

    # Delete orphaned thumbnails
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
        "deleted": deleted,
        "failed": failed,
        "kept": kept,
        "freed_bytes": freed_bytes
    }


def regenerate_missing_thumbnails() -> dict:
    """Regenerate all missing thumbnails.

    Note: Encrypted files without DEK in cache will be skipped.

    Returns dict with regeneration statistics.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    db = get_db()

    # Get all photos
    photos = db.execute("SELECT id, filename, media_type, is_encrypted, user_id FROM photos").fetchall()

    regenerated = 0
    failed = 0
    skipped = 0  # Original file missing
    skipped_encrypted = 0  # Encrypted but no DEK available
    already_exists = 0

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / f"{photo['id']}.jpg"

        if thumb_path.exists():
            already_exists += 1
            continue

        original_path = UPLOADS_DIR / photo["filename"]
        if not original_path.exists():
            skipped += 1
            continue

        # Handle encrypted files
        if photo["is_encrypted"]:
            dek = dek_cache.get(photo["user_id"])
            if not dek:
                skipped_encrypted += 1
                continue

            try:
                # Read and decrypt original
                with open(original_path, "rb") as f:
                    encrypted_data = f.read()
                decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)

                # Create thumbnail from decrypted bytes
                if photo["media_type"] == "video":
                    thumb_bytes, _, _ = create_video_thumbnail_bytes(decrypted_data)
                else:
                    thumb_bytes, _, _ = create_thumbnail_bytes(decrypted_data)

                # Encrypt and save thumbnail
                encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, dek)
                with open(thumb_path, "wb") as f:
                    f.write(encrypted_thumb)
                regenerated += 1
            except Exception:
                failed += 1
        else:
            # Unencrypted file
            try:
                if photo["media_type"] == "video":
                    create_video_thumbnail(original_path, thumb_path)
                else:
                    create_thumbnail(original_path, thumb_path)
                regenerated += 1
            except Exception:
                failed += 1

    return {
        "regenerated": regenerated,
        "failed": failed,
        "skipped_no_original": skipped,
        "skipped_encrypted": skipped_encrypted,
        "already_exists": already_exists,
        "total": len(photos)
    }


def get_thumbnail_stats() -> dict:
    """Get thumbnail statistics for admin dashboard.

    Returns dict with counts and status information.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    db = get_db()

    # Get all photos
    photos = db.execute("SELECT id, filename FROM photos").fetchall()
    total_photos = len(photos)

    # Check thumbnail status for each photo
    missing_thumbnails = 0
    missing_originals = 0
    healthy = 0

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / f"{photo['id']}.jpg"
        original_path = UPLOADS_DIR / photo["filename"]

        if not original_path.exists():
            missing_originals += 1
        elif not thumb_path.exists():
            missing_thumbnails += 1
        else:
            healthy += 1

    # Count orphaned thumbnails (thumbnails without photos)
    valid_photo_ids = {p["id"] for p in photos}
    orphaned_thumbnails = 0
    orphaned_size = 0

    for thumb_file in THUMBNAILS_DIR.glob("*.jpg"):
        if thumb_file.stem not in valid_photo_ids:
            orphaned_thumbnails += 1
            try:
                orphaned_size += thumb_file.stat().st_size
            except Exception:
                pass

    # Total thumbnail directory size
    total_thumb_size = sum(
        f.stat().st_size for f in THUMBNAILS_DIR.glob("*.jpg")
        if f.is_file()
    )

    return {
        "total_photos": total_photos,
        "healthy": healthy,
        "missing_thumbnails": missing_thumbnails,
        "missing_originals": missing_originals,
        "orphaned_thumbnails": orphaned_thumbnails,
        "orphaned_size": orphaned_size,
        "total_thumbnail_size": total_thumb_size
    }
