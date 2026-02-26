#!/usr/bin/env python3
"""Migration script to convert files from extension-based to extension-less storage.

This script:
1. Renames files in uploads/ from {uuid}.{ext} to {uuid}
2. Renames thumbnails/ from {uuid}.jpg to {uuid}
3. Updates database records (content_type, filename)

Usage:
    python scripts/migrate_extensions.py [--dry-run]

Safety:
    - Creates backup before migration
    - Dry-run mode shows what would be changed
    - Idempotent - can be run multiple times
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import UPLOADS_DIR, THUMBNAILS_DIR, BASE_DIR


def get_content_type_from_ext(ext: str) -> str:
    """Map file extension to MIME type."""
    mapping = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
    }
    return mapping.get(ext.lower(), 'application/octet-stream')


def migrate_files(dry_run: bool = False) -> dict:
    """Migrate all files from extension-based to extension-less storage.
    
    Returns:
        Dict with migration stats
    """
    stats = {
        'uploads_renamed': 0,
        'thumbnails_renamed': 0,
        'db_updated': 0,
        'errors': []
    }
    
    # Connect to database
    db_path = BASE_DIR / "gallery.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print(f"Connected to database: {db_path}")
    print(f"Uploads dir: {UPLOADS_DIR}")
    print(f"Thumbnails dir: {THUMBNAILS_DIR}")
    print()
    
    # Get all photos from database
    cursor.execute("SELECT id, filename FROM photos")
    photos = cursor.fetchall()
    
    print(f"Found {len(photos)} photos in database")
    print()
    
    for photo in photos:
        photo_id = photo['id']
        old_filename = photo['filename']
        
        # Skip if already migrated (no extension)
        if '.' not in old_filename:
            print(f"  [SKIP] {photo_id} - already extension-less")
            continue
        
        new_filename = photo_id  # Just the UUID, no extension
        ext = Path(old_filename).suffix.lower()
        content_type = get_content_type_from_ext(ext)
        
        print(f"  [MIGRATE] {old_filename} -> {new_filename} ({content_type})")
        
        if dry_run:
            stats['uploads_renamed'] += 1
            stats['thumbnails_renamed'] += 1
            stats['db_updated'] += 1
            continue
        
        # Rename upload file
        old_upload_path = UPLOADS_DIR / old_filename
        new_upload_path = UPLOADS_DIR / new_filename
        
        try:
            if old_upload_path.exists():
                old_upload_path.rename(new_upload_path)
                stats['uploads_renamed'] += 1
            else:
                print(f"    [WARN] Upload file not found: {old_upload_path}")
        except Exception as e:
            stats['errors'].append(f"Upload {old_filename}: {e}")
            print(f"    [ERROR] {e}")
        
        # Rename thumbnail file
        old_thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        new_thumb_path = THUMBNAILS_DIR / photo_id
        
        try:
            if old_thumb_path.exists():
                old_thumb_path.rename(new_thumb_path)
                stats['thumbnails_renamed'] += 1
            else:
                print(f"    [WARN] Thumbnail not found: {old_thumb_path}")
        except Exception as e:
            stats['errors'].append(f"Thumbnail {photo_id}: {e}")
            print(f"    [ERROR] {e}")
        
        # Update database
        try:
            cursor.execute(
                "UPDATE photos SET filename = ?, content_type = ? WHERE id = ?",
                (new_filename, content_type, photo_id)
            )
            stats['db_updated'] += 1
        except Exception as e:
            stats['errors'].append(f"DB {photo_id}: {e}")
            print(f"    [ERROR] DB update: {e}")
    
    if not dry_run:
        conn.commit()
        print()
        print("Database changes committed")
    
    conn.close()
    return stats


def create_backup():
    """Create database backup before migration."""
    from datetime import datetime
    from shutil import copy2
    
    db_path = BASE_DIR / "gallery.db"
    backup_path = BASE_DIR / f"gallery_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    
    if db_path.exists():
        copy2(db_path, backup_path)
        print(f"Database backup created: {backup_path}")
        return backup_path
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Migrate files from extension-based to extension-less storage'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without making changes'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip creating database backup'
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("File Extension Migration Tool")
    print("=" * 60)
    print()
    
    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")
        print()
    
    # Create backup
    if not args.dry_run and not args.no_backup:
        backup_path = create_backup()
        if backup_path:
            print()
    
    # Confirm
    if not args.dry_run:
        print("This will rename all files and update the database.")
        response = input("Continue? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted")
            return
        print()
    
    # Run migration
    stats = migrate_files(dry_run=args.dry_run)
    
    # Print summary
    print()
    print("=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"  Uploads renamed:    {stats['uploads_renamed']}")
    print(f"  Thumbnails renamed: {stats['thumbnails_renamed']}")
    print(f"  DB records updated: {stats['db_updated']}")
    print(f"  Errors:             {len(stats['errors'])}")
    
    if stats['errors']:
        print()
        print("Errors:")
        for error in stats['errors']:
            print(f"  - {error}")
    
    if args.dry_run:
        print()
        print("[DRY RUN - No actual changes made]")
        print("Run without --dry-run to apply changes")


if __name__ == "__main__":
    main()
