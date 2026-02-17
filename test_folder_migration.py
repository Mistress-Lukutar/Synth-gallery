#!/usr/bin/env python3
"""Test FolderRepository migration."""
import sys
sys.path.insert(0, '.')

print("Testing FolderRepository...")
from app.database import init_db, get_db
from app.infrastructure.repositories import FolderRepository, UserRepository

init_db()
db = get_db()

# Create test user
user_repo = UserRepository(db)
user_id = user_repo.create('folder_test', 'pass', 'Folder Test')
print(f"Created user: {user_id}")

# Test FolderRepository
folder_repo = FolderRepository(db)

# Create root folder
root_id = folder_repo.create("Root Folder", user_id)
print(f"Created root folder: {root_id}")

# Create child folder
child_id = folder_repo.create("Child Folder", user_id, parent_id=root_id)
print(f"Created child folder: {child_id}")

# Get by ID
folder = folder_repo.get_by_id(root_id)
print(f"Got folder: {folder['name']}")

# Get children
children = folder_repo.get_children(root_id)
print(f"Children count: {len(children)}")

# Get breadcrumbs
breadcrumbs = folder_repo.get_breadcrumbs(child_id)
print(f"Breadcrumbs: {' > '.join(b['name'] for b in breadcrumbs)}")

# Update
folder_repo.update(root_id, "Updated Root")
updated = folder_repo.get_by_id(root_id)
print(f"Updated name: {updated['name']}")

# Test old functions (proxied)
print("\nTesting old functions (proxied)...")
from app.database import create_folder, get_folder, delete_folder, get_user_folders

old_folder = create_folder("Old Style", user_id)
print(f"Old function created: {old_folder}")

folders = get_user_folders(user_id)
print(f"Old function listed: {len(folders)} folders")

# Cleanup
filenames = delete_folder(old_folder)
print(f"Old function deleted, files to cleanup: {len(filenames)}")

print("\n✅ FolderRepository migration successful!")
