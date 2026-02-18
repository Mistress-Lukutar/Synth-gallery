#!/usr/bin/env python3
"""Test SafeRepository migration."""
import sys
sys.path.insert(0, '.')

print("Testing SafeRepository...")
from app.database import init_db, get_db
from app.infrastructure.repositories import (
    SafeRepository, UserRepository, FolderRepository
)

init_db()
db = get_db()

# Create test user
user_repo = UserRepository(db)
user_id = user_repo.create('safe_test', 'pass', 'Safe Test')
print(f"Created user: {user_id}")

# Test SafeRepository
safe_repo = SafeRepository(db)

# Create safe
safe_id = safe_repo.create(
    name="My Secret Safe",
    user_id=user_id,
    encrypted_dek=b"encrypted_key_data",
    unlock_type="password",
    salt=b"salt_data"
)
print(f"Created safe: {safe_id}")

# Get safe
safe = safe_repo.get_by_id(safe_id)
print(f"Got safe: {safe['name']} (type: {safe['unlock_type']})")

# Create session (unlock)
session_id = safe_repo.create_session(
    safe_id=safe_id,
    user_id=user_id,
    encrypted_dek=b"session_encrypted_key",
    expires_hours=24
)
print(f"Created session: {session_id[:20]}...")

# Check unlocked
is_unlocked = safe_repo.is_unlocked(safe_id, user_id)
print(f"Safe unlocked: {is_unlocked}")

# List unlocked
unlocked = safe_repo.list_unlocked(user_id)
print(f"Unlocked safes: {len(unlocked)}")

# Create folder in safe
folder_repo = FolderRepository(db)
folder_id = folder_repo.create("Secret Folder", user_id)
safe_repo.assign_folder(folder_id, safe_id)
print(f"Assigned folder to safe")

# Get folders in safe
folders = safe_repo.get_folders(safe_id)
print(f"Folders in safe: {len(folders)}")

# Lock (delete session)
safe_repo.delete_all_sessions(safe_id, user_id)
is_unlocked = safe_repo.is_unlocked(safe_id, user_id)
print(f"After lock - Safe unlocked: {is_unlocked}")

# Test old functions
print("\nTesting old functions (proxied)...")
from app.database import (
    create_safe, get_safe, get_user_safes,
    create_safe_session, is_safe_unlocked_for_user
)

old_safe = create_safe("Old Safe", user_id, b"key", "password")
print(f"Old create_safe: {old_safe[:20]}...")

old_data = get_safe(safe_id)
print(f"Old get_safe: {old_data['name']}")

old_session = create_safe_session(safe_id, user_id, b"session_key")
print(f"Old create_safe_session: {old_session[:20]}...")

print(f"Old is_safe_unlocked: {is_safe_unlocked_for_user(safe_id, user_id)}")

# Cleanup
safe_repo.delete(safe_id)
print(f"\n✅ SafeRepository migration successful!")
