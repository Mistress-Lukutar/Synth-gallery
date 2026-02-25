#!/usr/bin/env python3
"""
Fix missing thumbnail dimensions in database.
Measures actual thumbnail files and updates DB records.
"""
import sqlite3
import os
from pathlib import Path
from PIL import Image

# Paths
DB_PATH = Path(__file__).parent.parent / "gallery.db"
THUMBNAILS_DIR = Path(__file__).parent.parent / "thumbnails"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Find photos with NULL dimensions
    cursor.execute('''
        SELECT id, original_name, album_id 
        FROM photos 
        WHERE thumb_width IS NULL
    ''')
    photos = cursor.fetchall()
    
    print(f"Found {len(photos)} photos with missing dimensions")
    
    fixed = 0
    missing_files = 0
    errors = 0
    
    for photo in photos:
        photo_id = photo['id']
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        
        if not thumb_path.exists():
            print(f"  [MISSING] {photo_id} - {thumb_path}")
            missing_files += 1
            continue
        
        try:
            # Open image and get dimensions
            with Image.open(thumb_path) as img:
                width, height = img.size
                aspect_ratio = width / height if height > 0 else None
                
                # Update database
                cursor.execute('''
                    UPDATE photos 
                    SET thumb_width = ?, thumb_height = ?, aspect_ratio = ?
                    WHERE id = ?
                ''', (width, height, aspect_ratio, photo_id))
                
                print(f"  [FIXED] {photo_id} - {width}x{height} ({os.path.basename(photo['original_name'] or 'unknown')})")
                fixed += 1
                
                # Commit every 100 records
                if fixed % 100 == 0:
                    conn.commit()
                    print(f"  ... committed {fixed} so far")
                    
        except Exception as e:
            print(f"  [ERROR] {photo_id} - {e}")
            errors += 1
    
    # Final commit
    conn.commit()
    
    print(f"\n=== Summary ===")
    print(f"Total photos checked: {len(photos)}")
    print(f"Fixed: {fixed}")
    print(f"Missing thumbnail files: {missing_files}")
    print(f"Errors: {errors}")
    
    # Check album covers after fix
    cursor.execute('''
        SELECT COUNT(*) 
        FROM albums a
        JOIN photos p ON p.id = (
            SELECT id FROM photos WHERE album_id = a.id ORDER BY uploaded_at DESC LIMIT 1
        )
        WHERE p.thumb_width IS NULL
    ''')
    album_covers_null = cursor.fetchone()[0]
    print(f"\nAlbum covers with NULL dimensions after fix: {album_covers_null}")
    
    conn.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
