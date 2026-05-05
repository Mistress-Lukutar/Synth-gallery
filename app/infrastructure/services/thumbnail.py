"""Thumbnail management service - regeneration, cleanup, statistics."""

from pathlib import Path

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
    # Phase 5: Get from item_media + items tables
    photo = db.execute(
        """SELECT im.filename, im.media_type, i.safe_id, i.user_id 
            FROM item_media im
            JOIN items i ON im.item_id = i.id
            WHERE i.id = ?""",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    # Find original file
    original_path = UPLOADS_DIR / photo["filename"]
    if not original_path.exists():
        return False

    thumb_path = THUMBNAILS_DIR / photo_id  # Extension-less storage

    # E2E files: cannot regenerate thumbnail server-side
    if photo["safe_id"]:
        return False

    # Server-side encrypted files: decrypt, generate thumbnail, re-encrypt
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


def cleanup_orphaned_thumbnails() -> dict:
    """Remove thumbnails that don't have corresponding photos in database.

    Returns dict with cleanup statistics.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import THUMBNAILS_DIR
    
    db = get_db()

    # Get all photo IDs from database
    # Phase 5: Get item IDs from items table
    photos = db.execute("SELECT id FROM items WHERE type = 'media'").fetchall()
    valid_photo_ids = {p["id"] for p in photos}

    # Scan thumbnails directory
    orphaned = []
    kept = 0

    for thumb_file in THUMBNAILS_DIR.iterdir():
        if thumb_file.is_file():
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


def cleanup_orphaned_uploads() -> dict:
    """Remove orphaned uploads and DB entries without files.

    Handles two cases:
    1. Files in uploads/ without DB entries (orphaned files)
    2. DB entries without files (missing originals)

    Returns dict with cleanup statistics.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    db = get_db()

    # Get all items with their filenames from database
    items = db.execute(
        """SELECT i.id, im.filename 
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()
    
    # Build sets for validation
    valid_filenames = {item["filename"] for item in items}
    item_id_to_filename = {item["id"]: item["filename"] for item in items}

    # Case 1: Files on disk without DB entries (orphaned files)
    orphaned_files = []
    for upload_file in UPLOADS_DIR.iterdir():
        if upload_file.is_file():
            if upload_file.name not in valid_filenames:
                orphaned_files.append(upload_file)

    # Case 2: DB entries without files (missing originals)
    missing_originals = []
    for item_id, filename in item_id_to_filename.items():
        upload_path = UPLOADS_DIR / filename
        if not upload_path.exists():
            missing_originals.append((item_id, filename))

    # Delete orphaned files
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

    # Delete DB entries for missing originals (cascades to item_media, item_tags)
    db_deleted = 0
    db_failed = 0
    thumbs_deleted = 0

    for item_id, filename in missing_originals:
        try:
            # Delete thumbnail if exists
            thumb_path = THUMBNAILS_DIR / item_id
            if thumb_path.exists():
                thumb_path.unlink()
                thumbs_deleted += 1
            
            # Delete item (cascades to related tables)
            db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            db_deleted += 1
        except Exception:
            db_failed += 1

    db.commit()

    return {
        "files_deleted": files_deleted,
        "files_failed": files_failed,
        "db_deleted": db_deleted,
        "db_failed": db_failed,
        "thumbs_deleted": thumbs_deleted,
        "freed_bytes": freed_bytes,
        "total_deleted": files_deleted + db_deleted
    }


def _update_item_thumbnail_dimensions(item_id: str, thumb_path: Path, db):
    """Update thumb_width, thumb_height in item_media table from thumbnail file."""
    try:
        from PIL import Image
        with Image.open(thumb_path) as img:
            width, height = img.size
            
            db.execute(
                "UPDATE item_media SET thumb_width = ?, thumb_height = ? WHERE item_id = ?",
                (width, height, item_id)
            )
            db.commit()
            return True
    except Exception:
        return False


def regenerate_missing_thumbnails() -> dict:
    """Regenerate all missing thumbnails and update dimensions.

    Note: 
    - Encrypted files without DEK in cache will be skipped
    - For existing thumbnails without dimensions, measures and updates DB

    Returns dict with regeneration statistics.
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR

    db = get_db()

    # Get all photos including current dimension status
    # Phase 5: Get from item_media + items tables
    photos = db.execute(
        """SELECT i.id, im.filename, im.media_type, i.safe_id, i.user_id, im.thumb_width 
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()

    regenerated = 0
    dimensions_updated = 0  # Existing thumbnails where we added dimensions
    failed = 0
    skipped = 0  # Original file missing
    skipped_encrypted = 0  # Server-side encrypted but no DEK available
    already_exists_with_dims = 0

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / photo['id']  # Extension-less
        original_path = UPLOADS_DIR / photo["filename"]

        if not original_path.exists():
            skipped += 1
            continue

        # E2E files: skip thumbnail regeneration (client-provided)
        if photo["safe_id"]:
            if thumb_path.exists() and photo["thumb_width"] is not None:
                already_exists_with_dims += 1
            continue

        # Check if thumbnail exists
        if thumb_path.exists():
            # If thumbnail exists but no dimensions in DB, try to measure and update
            if photo["thumb_width"] is None:
                # Try to regenerate with DEK if available
                dek = dek_cache.get(photo["user_id"])
                if dek:
                    try:
                        # Read and decrypt original
                        with open(original_path, "rb") as f:
                            encrypted_data = f.read()
                        decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)

                        # Create thumbnail with dimensions
                        if photo["media_type"] == "video":
                            thumb_bytes, width, height = create_video_thumbnail_bytes(decrypted_data)
                        else:
                            thumb_bytes, width, height = create_thumbnail_bytes(decrypted_data)

                        # Encrypt and save thumbnail
                        encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, dek)
                        with open(thumb_path, "wb") as f:
                            f.write(encrypted_thumb)
                        
                        # Update dimensions in DB
                        db.execute(
                            "UPDATE item_media SET thumb_width = ?, thumb_height = ? WHERE item_id = ?",
                            (width, height, photo["id"])
                        )
                        db.commit()
                        regenerated += 1
                    except Exception:
                        skipped_encrypted += 1
                else:
                    skipped_encrypted += 1
            else:
                already_exists_with_dims += 1
            continue

        # Thumbnail doesn't exist - create it
        dek = dek_cache.get(photo["user_id"])
        if not dek:
            skipped_encrypted += 1
            continue

        try:
            # Read and decrypt original
            with open(original_path, "rb") as f:
                encrypted_data = f.read()
            decrypted_data = EncryptionService.decrypt_file(encrypted_data, dek)

            # Create thumbnail with dimensions
            if photo["media_type"] == "video":
                thumb_bytes, width, height = create_video_thumbnail_bytes(decrypted_data)
            else:
                thumb_bytes, width, height = create_thumbnail_bytes(decrypted_data)

            # Encrypt and save thumbnail
            encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, dek)
            with open(thumb_path, "wb") as f:
                f.write(encrypted_thumb)
            
            # Update dimensions in DB
            db.execute(
                "UPDATE item_media SET thumb_width = ?, thumb_height = ? WHERE item_id = ?",
                (width, height, photo["id"])
            )
            db.commit()
            regenerated += 1
        except Exception:
            failed += 1

    return {
        "regenerated": regenerated,
        "dimensions_updated": dimensions_updated,
        "failed": failed,
        "skipped_no_original": skipped,
        "skipped_encrypted": skipped_encrypted,
        "already_exists": already_exists_with_dims,
        "total": len(photos)
    }


def get_thumbnail_stats() -> dict:
    """Get thumbnail statistics for admin dashboard.

    Returns dict with counts and status information.
    
    Note: "Missing thumbnails" includes:
    - Photos without thumbnail files
    - Photos with thumbnails but without dimensions in database
    """
    # Import config here to respect test patches (Issue #16)
    from ...config import UPLOADS_DIR, THUMBNAILS_DIR
    
    db = get_db()

    # Get all photos with their dimension status
    photos = db.execute(
        """SELECT i.id, im.filename, im.thumb_width, i.safe_id, i.user_id
            FROM items i
            JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'"""
    ).fetchall()
    total_photos = len(photos)

    # Get all valid filenames from database (for orphaned uploads check)
    valid_filenames = {p["filename"] for p in photos}

    # Check thumbnail status for each photo
    missing_thumbnails = 0  # No file OR no dimensions in DB
    missing_dimensions = 0  # File exists but no dimensions (needs repair)
    missing_originals = 0
    healthy = 0
    encrypted_no_dek = 0  # Server-side encrypted files where we can't check dimensions

    for photo in photos:
        thumb_path = THUMBNAILS_DIR / photo['id']  # Extension-less
        original_path = UPLOADS_DIR / photo["filename"]

        if not original_path.exists():
            missing_originals += 1
        elif not thumb_path.exists():
            missing_thumbnails += 1
        elif photo["thumb_width"] is None:
            # File exists but no dimensions in DB
            missing_dimensions += 1
            missing_thumbnails += 1  # Count as missing for regeneration purposes
            if not photo["safe_id"]:
                # Server-side encrypted thumbnail: need DEK to measure
                if not dek_cache.get(photo["user_id"]):
                    encrypted_no_dek += 1
        else:
            healthy += 1

    # Count orphaned thumbnails (thumbnails without photos)
    valid_photo_ids = {p["id"] for p in photos}
    orphaned_thumbnails = 0
    orphaned_size = 0

    for thumb_file in THUMBNAILS_DIR.iterdir():
        if thumb_file.is_file():
            if thumb_file.stem not in valid_photo_ids:
                orphaned_thumbnails += 1
                try:
                    orphaned_size += thumb_file.stat().st_size
                except Exception:
                    pass

    # Count orphaned uploads (files in uploads/ not registered in DB)
    orphaned_uploads = 0
    orphaned_uploads_size = 0
    uploads_total_size = 0

    for upload_file in UPLOADS_DIR.iterdir():
        if upload_file.is_file():
            file_size = 0
            try:
                file_size = upload_file.stat().st_size
                uploads_total_size += file_size
            except Exception:
                pass
            
            if upload_file.name not in valid_filenames:
                orphaned_uploads += 1
                orphaned_uploads_size += file_size

    # Total thumbnail directory size
    total_thumb_size = sum(
        f.stat().st_size for f in THUMBNAILS_DIR.iterdir() if f.is_file()
    )

    return {
        "total_photos": total_photos,
        "healthy": healthy,
        "missing_thumbnails": missing_thumbnails,
        "missing_dimensions": missing_dimensions,
        "missing_originals": missing_originals,
        "orphaned_thumbnails": orphaned_thumbnails,
        "orphaned_size": orphaned_size,
        "total_thumbnail_size": total_thumb_size,
        "encrypted_no_dek": encrypted_no_dek,
        "orphaned_uploads": orphaned_uploads,
        "orphaned_uploads_size": orphaned_uploads_size,
        "uploads_total_size": uploads_total_size
    }
