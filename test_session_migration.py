#!/usr/bin/env python3
"""Test SessionRepository migration."""
import sys
sys.path.insert(0, '.')

print("Testing SessionRepository...")
from app.database import init_db, get_db
from app.infrastructure.repositories import SessionRepository, UserRepository

init_db()
db = get_db()

# Create test user
user_repo = UserRepository(db)
user_id = user_repo.create('session_test', 'pass', 'Session Test')
print(f"Created user: {user_id}")

# Test SessionRepository
session_repo = SessionRepository(db)
session_id = session_repo.create(user_id, expires_hours=24)
print(f"Created session: {session_id[:20]}...")

# Get session
session = session_repo.get_valid(session_id)
print(f"Got session for user: {session['username']}")

# Test old function (proxied)
from app.database import create_session, get_session, delete_session
old_session = create_session(user_id, expires_hours=1)
print(f"Old function created session: {old_session[:20]}...")

old_data = get_session(old_session)
print(f"Old function got session: {old_data['username']}")

delete_session(old_session)
print("Old function deleted session")

print("\n✅ SessionRepository migration successful!")
