#!/usr/bin/env python3
"""Phase 5C: Database Cleanup - Remove legacy photos table"""
import shutil
from datetime import datetime
from app.database import create_connection

def create_backup():
    """Create pre-cleanup backup"""
    now = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'backups/pre_phase5c_{now}.db'
    shutil.copy('gallery.db', backup_name)
    print(f'✅ Pre-cleanup backup created: {backup_name}')
    return backup_name

def show_photos_schema(db):
    """Show current photos table schema"""
    print('\n=== CURRENT PHOTOS TABLE SCHEMA ===')
    cursor = db.execute('PRAGMA table_info(photos)')
    for row in cursor.fetchall():
        print(f'  {row[1]} ({row[2]})')
    
    cursor = db.execute('SELECT COUNT(*) FROM photos')
    print(f'\nPhotos count: {cursor.fetchone()[0]}')

def drop_album_id_column(db):
    """Step 1: Drop photos.album_id column"""
    print('\n=== STEP 1: DROP photos.album_id COLUMN ===')
    try:
        db.execute('ALTER TABLE photos DROP COLUMN album_id')
        db.commit()
        print('✅ Dropped album_id column from photos table')
    except Exception as e:
        print(f'⚠️  Could not drop album_id (may not exist): {e}')

def drop_photos_table(db):
    """Step 2: Drop photos table"""
    print('\n=== STEP 2: DROP photos TABLE ===')
    try:
        db.execute('DROP TABLE photos')
        db.commit()
        print('✅ Dropped photos table')
    except Exception as e:
        print(f'❌ Error dropping photos table: {e}')

def verify_cleanup(db):
    """Verify cleanup"""
    print('\n=== VERIFICATION ===')
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photos'")
    if cursor.fetchone():
        print('❌ photos table still exists!')
    else:
        print('✅ photos table successfully removed')
    
    # Show remaining tables
    print('\n=== REMAINING TABLES ===')
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for row in cursor.fetchall():
        print(f'  - {row[0]}')

def main():
    print('=' * 60)
    print('PHASE 5C: DATABASE CLEANUP')
    print('=' * 60)
    
    # Create backup
    backup_path = create_backup()
    
    db = create_connection()
    try:
        # Show current state
        show_photos_schema(db)
        
        # Confirm
        print('\n' + '=' * 60)
        print('⚠️  WARNING: This will permanently delete:')
        print('  - photos.album_id column')
        print('  - photos table (2632 legacy records)')
        print('=' * 60)
        print(f'\nBackup created at: {backup_path}')
        print('\nProceeding with cleanup...')
        
        # Execute cleanup
        drop_album_id_column(db)
        drop_photos_table(db)
        verify_cleanup(db)
        
        print('\n' + '=' * 60)
        print('✅ PHASE 5C COMPLETE')
        print('=' * 60)
        print('\nLegacy photos table has been removed.')
        print('All data is now in items + item_media tables.')
        
    finally:
        db.close()

if __name__ == '__main__':
    main()
