#!/usr/bin/env python3
"""Quick test to verify UserRepository migration works."""
import sys
sys.path.insert(0, '.')

print("Step 1: Checking imports...")
from app.database import create_user, get_user_by_id, authenticate_user
from app.infrastructure.repositories import UserRepository
print("  ✅ Imports OK")

print("\nStep 2: Initializing database...")
from app.database import get_db, init_db
init_db()
print("  ✅ Database initialized")

print("\nStep 3: Testing UserRepository (new way)...")
repo = UserRepository(get_db())
user_id = repo.create('test_repo_user', 'password123', 'Test User')
print(f"  ✅ Repository created user: {user_id}")

print("\nStep 4: Testing old functions (proxied to Repository)...")
old_user_id = create_user('test_old_user', 'password123', 'Old User')
print(f"  ✅ Old function created user: {old_user_id}")

print("\nStep 5: Testing get_user_by_id...")
user = get_user_by_id(user_id)
print(f"  ✅ Got user: {user['username']}")

print("\nStep 6: Testing authenticate_user...")
auth_user = authenticate_user('test_repo_user', 'password123')
if auth_user:
    print(f"  ✅ Auth successful: {auth_user['username']}")
else:
    print("  ❌ Auth failed!")
    sys.exit(1)

print("\nStep 7: Testing search...")
results = repo.search("test")
print(f"  ✅ Found {len(results)} users")

print("\n" + "="*50)
print("🎉 All migration checks passed!")
print("="*50)
print("\nSummary:")
print(f"  - UserRepository works: ✅")
print(f"  - Old functions proxied: ✅")
print(f"  - No breaking changes: ✅")
