#!/usr/bin/env python3
"""Phase 5B: Data Validation Script

Checks for:
1. Orphaned album_items (items that don't exist)
2. Unmigrated photos (photos without corresponding items)
3. Album membership consistency (photos.album_id vs album_items)
4. Cover item validity
5. File existence (uploads and thumbnails)
"""
import sys
from pathlib import Path
from collections import defaultdict

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import create_connection
from app.config import UPLOADS_DIR, THUMBNAILS_DIR


def check_orphaned_album_items(db):
    """Find album_items referencing non-existent items."""
    print("=" * 60)
    print("1. CHECKING ORPHANED ALBUM ITEMS")
    print("=" * 60)
    
    cursor = db.execute("""
        SELECT ai.*, a.name as album_name
        FROM album_items ai
        LEFT JOIN items i ON ai.item_id = i.id
        LEFT JOIN albums a ON ai.album_id = a.id
        WHERE i.id IS NULL
    """)
    orphaned = cursor.fetchall()
    
    if orphaned:
        print(f"❌ Found {len(orphaned)} orphaned album_items:")
        for row in orphaned[:10]:  # Show first 10
            album_name = row['album_name'] if row['album_name'] else 'N/A'
            print(f"   - Item: {row['item_id']}, Album: {row['album_id']} ({album_name})")
        if len(orphaned) > 10:
            print(f"   ... and {len(orphaned) - 10} more")
    else:
        print("✅ No orphaned album_items found")
    
    return len(orphaned)


def check_unmigrated_photos(db):
    """Find photos that weren't migrated to items."""
    print("\n" + "=" * 60)
    print("2. CHECKING UNMIGRATED PHOTOS")
    print("=" * 60)
    
    cursor = db.execute("""
        SELECT p.id, p.original_name, p.folder_id, p.album_id, p.uploaded_at
        FROM photos p
        LEFT JOIN items i ON p.id = i.id
        WHERE i.id IS NULL
    """)
    unmigrated = cursor.fetchall()
    
    if unmigrated:
        print(f"❌ Found {len(unmigrated)} unmigrated photos:")
        for row in unmigrated[:5]:
            print(f"   - {row['id']}: {row['original_name'] if row['original_name'] else 'N/A'}")
    else:
        print("✅ All photos migrated to items")
    
    return len(unmigrated)


def check_album_membership_consistency(db):
    """Check if photos.album_id matches album_items."""
    print("\n" + "=" * 60)
    print("3. CHECKING ALBUM MEMBERSHIP CONSISTENCY")
    print("=" * 60)
    
    # Find photos with album_id not in album_items
    cursor = db.execute("""
        SELECT p.id, p.album_id, p.original_name
        FROM photos p
        WHERE p.album_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM album_items ai 
            WHERE ai.item_id = p.id AND ai.album_id = p.album_id
        )
    """)
    inconsistent = cursor.fetchall()
    
    if inconsistent:
        print(f"❌ Found {len(inconsistent)} photos with album_id not in album_items:")
        for row in inconsistent[:5]:
            print(f"   - Photo: {row['id']}, Album: {row['album_id']}")
    else:
        print("✅ All photos.album_id entries exist in album_items")
    
    # Also check for album_items without matching photos.album_id (expected after migration)
    cursor = db.execute("""
        SELECT ai.*, a.name as album_name
        FROM album_items ai
        LEFT JOIN photos p ON ai.item_id = p.id AND ai.album_id = p.album_id
        LEFT JOIN albums a ON ai.album_id = a.id
        WHERE p.id IS NULL
    """)
    new_format = cursor.fetchall()
    
    if new_format:
        print(f"ℹ️  {len(new_format)} album_items are in new format (not linked to photos table)")
    
    return len(inconsistent)


def check_cover_items(db):
    """Validate album cover_item_id references."""
    print("\n" + "=" * 60)
    print("4. CHECKING COVER ITEM VALIDITY")
    print("=" * 60)
    
    cursor = db.execute("""
        SELECT a.id, a.name, a.cover_item_id
        FROM albums a
        LEFT JOIN items i ON a.cover_item_id = i.id
        WHERE a.cover_item_id IS NOT NULL AND i.id IS NULL
    """)
    invalid_covers = cursor.fetchall()
    
    if invalid_covers:
        print(f"❌ Found {len(invalid_covers)} albums with invalid cover_item_id:")
        for row in invalid_covers:
            print(f"   - Album: {row['name']} ({row['id']}), Cover: {row['cover_item_id']}")
    else:
        print("✅ All cover_item_id references are valid")
    
    return len(invalid_covers)


def check_file_existence(db):
    """Check if files exist on disk for items."""
    print("\n" + "=" * 60)
    print("5. CHECKING FILE EXISTENCE")
    print("=" * 60)
    
    cursor = db.execute("SELECT id FROM items WHERE type = 'media'")
    items = cursor.fetchall()
    
    missing_uploads = []
    missing_thumbs = []
    
    for row in items:
        item_id = row['id']
        upload_path = UPLOADS_DIR / item_id
        thumb_path = THUMBNAILS_DIR / item_id
        
        if not upload_path.exists():
            missing_uploads.append(item_id)
        if not thumb_path.exists():
            missing_thumbs.append(item_id)
    
    if missing_uploads:
        print(f"⚠️  {len(missing_uploads)} items missing upload files")
    else:
        print("✅ All items have upload files")
    
    if missing_thumbs:
        print(f"⚠️  {len(missing_thumbs)} items missing thumbnail files (can be regenerated)")
    else:
        print("✅ All items have thumbnail files")
    
    return len(missing_uploads), len(missing_thumbs)


def generate_summary(db):
    """Generate summary statistics."""
    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)
    
    stats = {}
    
    # Table counts
    cursor = db.execute("SELECT COUNT(*) FROM items")
    stats['items'] = cursor.fetchone()[0]
    
    cursor = db.execute("SELECT COUNT(*) FROM photos")
    stats['photos'] = cursor.fetchone()[0]
    
    cursor = db.execute("SELECT COUNT(*) FROM albums")
    stats['albums'] = cursor.fetchone()[0]
    
    cursor = db.execute("SELECT COUNT(*) FROM album_items")
    stats['album_items'] = cursor.fetchone()[0]
    
    cursor = db.execute("SELECT COUNT(*) FROM item_media")
    stats['item_media'] = cursor.fetchone()[0]
    
    print(f"Items:        {stats['items']}")
    print(f"Photos:       {stats['photos']} (legacy)")
    print(f"Albums:       {stats['albums']}")
    print(f"Album items:  {stats['album_items']}")
    print(f"Item media:   {stats['item_media']}")
    
    return stats


def main():
    print("\n" + "=" * 60)
    print("PHASE 5B: DATA VALIDATION")
    print("=" * 60)
    
    db = create_connection()
    try:
        issues = defaultdict(int)
        
        # Run all checks
        issues['orphaned_album_items'] = check_orphaned_album_items(db)
        issues['unmigrated_photos'] = check_unmigrated_photos(db)
        issues['inconsistent_album'] = check_album_membership_consistency(db)
        issues['invalid_covers'] = check_cover_items(db)
        issues['missing_uploads'], issues['missing_thumbs'] = check_file_existence(db)
        
        # Summary
        stats = generate_summary(db)
        
        # Final report
        print("\n" + "=" * 60)
        print("VALIDATION COMPLETE")
        print("=" * 60)
        
        critical_issues = (
            issues['orphaned_album_items'] + 
            issues['unmigrated_photos'] + 
            issues['inconsistent_album'] +
            issues['invalid_covers']
        )
        
        if critical_issues == 0:
            print("✅ No critical issues found. Ready for Phase 5C (Database Cleanup)")
        else:
            print(f"❌ Found {critical_issues} critical issues that need fixing")
            print("\nRecommended actions:")
            if issues['orphaned_album_items'] > 0:
                print(f"   - Delete {issues['orphaned_album_items']} orphaned album_items")
            if issues['unmigrated_photos'] > 0:
                print(f"   - Migrate {issues['unmigrated_photos']} unmigrated photos")
            if issues['inconsistent_album'] > 0:
                print(f"   - Fix {issues['inconsistent_album']} inconsistent album memberships")
            if issues['invalid_covers'] > 0:
                print(f"   - Fix {issues['invalid_covers']} invalid cover references")
        
        return critical_issues == 0
        
    finally:
        db.close()


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
