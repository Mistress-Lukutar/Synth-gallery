"""Thumbnail management service - regeneration, cleanup, statistics."""
from pathlib import Path

from ..config import UPLOADS_DIR, THUMBNAILS_DIR
from ..database import get_db
from .media import create_thumbnail, create_video_thumbnail


def regenerate_thumbnail(photo_id: str) -> bool:
    """Regenerate thumbnail for a single photo.

    Returns True if thumbnail was successfully regenerated, False otherwise.
    """
    db = get_db()
    photo = db.execute(
        "SELECT filename, media_type FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    # Find original file
    original_path = UPLOADS_DIR / photo["filename"]
    if not original_path.exists():
        return False

    # Generate thumbnail
    thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
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

    Returns dict with regeneration statistics.
    """
    db = get_db()

    # Get all photos
    photos = db.execute("SELECT id, filename, media_type FROM photos").fetchall()

    regenerated = 0
    failed = 0
    skipped = 0  # Original file missing
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
        "already_exists": already_exists,
        "total": len(photos)
    }


def get_thumbnail_stats() -> dict:
    """Get thumbnail statistics for admin dashboard.

    Returns dict with counts and status information.
    """
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
