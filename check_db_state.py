"""Check database state for Issue #28 Step 5 readiness"""
from app.database import create_connection

db = create_connection()

# Check tables
cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print('Tables:', tables)

# Check if photos table still exists
if 'photos' in tables:
    print('\n⚠️ Legacy photos table EXISTS')
    cursor = db.execute('PRAGMA table_info(photos)')
    cols = [row[1] for row in cursor.fetchall()]
    print('photos columns:', cols)
    if 'album_id' in cols:
        print('  ⚠️ photos.album_id column exists (should be removed)')
    
# Check items table
cursor = db.execute('PRAGMA table_info(items)')
items_cols = [row[1] for row in cursor.fetchall()]
print('\nitems columns:', items_cols)

# Check item_media
cursor = db.execute('PRAGMA table_info(item_media)')
media_cols = [row[1] for row in cursor.fetchall()]
print('item_media columns:', media_cols)

# Check if any photos still exist
try:
    cursor = db.execute('SELECT COUNT(*) FROM photos')
    count = cursor.fetchone()[0]
    if count > 0:
        print(f'\n⚠️ {count} legacy photos still exist!')
    else:
        print('\n✓ photos table is empty')
except:
    print('\n✓ photos table does not exist')

db.close()
