#!/usr/bin/env python3
"""Test PhotoRepository migration."""
import sys
sys.path.insert(0, '.')

print("Testing PhotoRepository...")
from app.database import init_db, get_db
from app.infrastructure.repositories import (
    PhotoRepository, UserRepository, FolderRepository
)
from datetime import datetime

init_db()
db = get_db()

# Create test user and folder
user_repo = UserRepository(db)
user_id = user_repo.create('photo_test', 'pass', 'Photo Test')

folder_repo = FolderRepository(db)
folder_id = folder_repo.create("Photo Folder", user_id)
print(f"Created user {user_id} and folder {folder_id}")

# Test PhotoRepository
photo_repo = PhotoRepository(db)

# Create photo
photo_id = photo_repo.create(
    filename="test_123.jpg",
    folder_id=folder_id,
    user_id=user_id,
    original_name="vacation.jpg",
    media_type="image",
    thumb_width=400,
    thumb_height=300
)
print(f"Created photo: {photo_id}")

# Get by ID
photo = photo_repo.get_by_id(photo_id)
print(f"Got photo: {photo['filename']} ({photo['media_type']})")

# Update thumbnail
photo_repo.update_thumbnail_dimensions(photo_id, 800, 600)
updated = photo_repo.get_by_id(photo_id)
print(f"Updated dimensions: {updated['thumb_width']}x{updated['thumb_height']}")

# Mark encrypted
photo_repo.mark_encrypted(photo_id)
encrypted = photo_repo.get_by_id(photo_id)
print(f"Encrypted: {bool(encrypted['is_encrypted'])}")

# Count photos
count = photo_repo.count_by_folder(folder_id)
print(f"Photos in folder: {count}")

# Get stats
stats = photo_repo.get_stats()
print(f"Total photos: {stats['total']}, Encrypted: {stats['encrypted']}")

# Test old functions (proxied)
print("\nTesting old functions (proxied)...")
from app.database import (
    get_photo_by_id, get_photo_owner_id, 
    update_photo_thumbnail_dimensions, mark_photo_encrypted
)

old_photo = get_photo_by_id(photo_id)
print(f"Old get_photo_by_id: {old_photo['id'][:8]}...")

owner = get_photo_owner_id(photo_id)
print(f"Old get_photo_owner_id: {owner}")

result = update_photo_thumbnail_dimensions(photo_id, 1200, 900)
print(f"Old update_thumbnail_dimensions: {result}")

# Cleanup
photo_repo.delete(photo_id)
print(f"\n✅ PhotoRepository migration successful!")
