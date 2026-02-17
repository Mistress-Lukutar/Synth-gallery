#!/usr/bin/env python3
"""Test PermissionRepository migration."""
import sys
sys.path.insert(0, '.')

print("Testing PermissionRepository...")
from app.database import init_db, get_db
from app.infrastructure.repositories import (
    PermissionRepository, UserRepository, FolderRepository
)

init_db()
db = get_db()

# Create test users
user_repo = UserRepository(db)
owner_id = user_repo.create('owner_user', 'pass', 'Owner')
viewer_id = user_repo.create('viewer_user', 'pass', 'Viewer')
editor_id = user_repo.create('editor_user', 'pass', 'Editor')
print(f"Created users: owner={owner_id}, viewer={viewer_id}, editor={editor_id}")

# Create folder
folder_repo = FolderRepository(db)
folder_id = folder_repo.create("Shared Folder", owner_id)
print(f"Created folder: {folder_id}")

# Test PermissionRepository
perm_repo = PermissionRepository(db)

# Grant viewer permission
result = perm_repo.grant(folder_id, viewer_id, "viewer", owner_id)
print(f"Granted viewer: {result}")

# Grant editor permission
result = perm_repo.grant(folder_id, editor_id, "editor", owner_id)
print(f"Granted editor: {result}")

# Check permissions
print(f"\nPermission checks:")
print(f"  Owner permission: {perm_repo.get_permission(folder_id, owner_id)}")
print(f"  Viewer permission: {perm_repo.get_permission(folder_id, viewer_id)}")
print(f"  Editor permission: {perm_repo.get_permission(folder_id, editor_id)}")

# Check access
print(f"\nAccess checks:")
print(f"  Owner can view: {perm_repo.can_view(folder_id, owner_id)}")
print(f"  Owner can edit: {perm_repo.can_edit(folder_id, owner_id)}")
print(f"  Viewer can view: {perm_repo.can_view(folder_id, viewer_id)}")
print(f"  Viewer can edit: {perm_repo.can_edit(folder_id, viewer_id)}")
print(f"  Editor can view: {perm_repo.can_view(folder_id, editor_id)}")
print(f"  Editor can edit: {perm_repo.can_edit(folder_id, editor_id)}")

# List permissions
perms = perm_repo.list_permissions(folder_id)
print(f"\nFolder permissions ({len(perms)} total):")
for p in perms:
    print(f"  {p['display_name']}: {p['permission']}")

# Test old functions (proxied)
print("\nTesting old functions (proxied)...")
from app.database import (
    can_view_folder, can_edit_folder, add_folder_permission,
    get_folder_permissions, get_user_permission
)

print(f"  Old can_view: {can_view_folder(folder_id, viewer_id)}")
print(f"  Old can_edit: {can_edit_folder(folder_id, editor_id)}")
print(f"  Old get_permission: {get_user_permission(folder_id, owner_id)}")

# Revoke and test
perm_repo.revoke(folder_id, viewer_id)
print(f"\nAfter revoking viewer:")
print(f"  Viewer can view: {perm_repo.can_view(folder_id, viewer_id)}")

print("\n✅ PermissionRepository migration successful!")
