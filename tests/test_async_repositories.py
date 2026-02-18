"""Tests for async repositories (Issue #15).

Tests async database operations using aiosqlite.
"""
import pytest
import pytest_asyncio
import aiosqlite
from pathlib import Path
import tempfile
import os

# Import async repositories
from app.infrastructure.repositories import (
    AsyncUserRepository,
    AsyncSessionRepository,
    AsyncFolderRepository,
    AsyncPermissionRepository,
    AsyncPhotoRepository,
    AsyncSafeRepository,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def async_db():
    """Create temporary async database for testing."""
    # Create temp database
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Connect with aiosqlite
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    
    # Create schema
    await conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            display_name TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    await conn.execute("""
        CREATE TABLE folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    await conn.execute("""
        CREATE TABLE folder_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL CHECK(permission IN ('viewer', 'editor')),
            granted_by INTEGER NOT NULL,
            granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (granted_by) REFERENCES users(id),
            UNIQUE(folder_id, user_id)
        )
    """)
    
    await conn.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await conn.execute("""
        CREATE TABLE photos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            folder_id TEXT,
            album_id TEXT,
            user_id INTEGER NOT NULL,
            taken_at TIMESTAMP,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            title TEXT,
            description TEXT,
            width INTEGER,
            height INTEGER,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL,
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    await conn.execute("""
        CREATE TABLE album_photos (
            album_id TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (album_id, photo_id),
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
    """)
    
    await conn.execute("""
        CREATE TABLE safes (
            id TEXT PRIMARY KEY,
            folder_id TEXT NOT NULL UNIQUE,
            encrypted_dek BLOB NOT NULL,
            dek_nonce BLOB NOT NULL,
            dek_salt BLOB NOT NULL,
            password_enabled BOOLEAN DEFAULT FALSE,
            hardware_key_enabled BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)
    
    await conn.commit()
    
    yield conn
    
    # Cleanup
    await conn.close()
    os.unlink(db_path)


@pytest_asyncio.fixture
async def async_user_repo(async_db):
    """Create AsyncUserRepository instance."""
    return AsyncUserRepository(async_db)


@pytest_asyncio.fixture
async def async_session_repo(async_db):
    """Create AsyncSessionRepository instance."""
    return AsyncSessionRepository(async_db)


@pytest_asyncio.fixture
async def async_folder_repo(async_db):
    """Create AsyncFolderRepository instance."""
    return AsyncFolderRepository(async_db)


# =============================================================================
# AsyncUserRepository Tests
# =============================================================================

@pytest.mark.asyncio
async def test_async_user_create(async_user_repo):
    """Test creating a user asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    
    assert isinstance(user_id, int)
    assert user_id > 0


@pytest.mark.asyncio
async def test_async_user_get_by_id(async_user_repo):
    """Test getting user by ID asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    user = await async_user_repo.get_by_id(user_id)
    
    assert user is not None
    assert user["username"] == "testuser"
    assert user["display_name"] == "Test User"


@pytest.mark.asyncio
async def test_async_user_get_by_username(async_user_repo):
    """Test getting user by username asynchronously."""
    await async_user_repo.create("testuser", "password123", "Test User")
    user = await async_user_repo.get_by_username("testuser")
    
    assert user is not None
    assert user["username"] == "testuser"


@pytest.mark.asyncio
async def test_async_user_authenticate(async_user_repo):
    """Test user authentication asynchronously."""
    await async_user_repo.create("testuser", "password123", "Test User")
    
    # Valid credentials
    user = await async_user_repo.authenticate("testuser", "password123")
    assert user is not None
    
    # Invalid password
    user = await async_user_repo.authenticate("testuser", "wrongpassword")
    assert user is None
    
    # Non-existent user
    user = await async_user_repo.authenticate("nonexistent", "password")
    assert user is None


@pytest.mark.asyncio
async def test_async_user_list_all(async_user_repo):
    """Test listing all users asynchronously."""
    await async_user_repo.create("user1", "pass1", "User One")
    await async_user_repo.create("user2", "pass2", "User Two")
    
    users = await async_user_repo.list_all()
    
    assert len(users) == 2
    usernames = [u["username"] for u in users]
    assert "user1" in usernames
    assert "user2" in usernames


# =============================================================================
# AsyncSessionRepository Tests
# =============================================================================

@pytest.mark.asyncio
async def test_async_session_create(async_session_repo, async_user_repo):
    """Test creating a session asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    session_id = await async_session_repo.create(user_id, expires_hours=24)
    
    assert isinstance(session_id, str)
    assert len(session_id) > 0


@pytest.mark.asyncio
async def test_async_session_get_valid(async_session_repo, async_user_repo):
    """Test getting valid session asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    session_id = await async_session_repo.create(user_id, expires_hours=24)
    
    session = await async_session_repo.get_valid(session_id)
    
    assert session is not None
    assert session["user_id"] == user_id
    assert session["username"] == "testuser"


@pytest.mark.asyncio
async def test_async_session_delete(async_session_repo, async_user_repo):
    """Test deleting a session asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    session_id = await async_session_repo.create(user_id, expires_hours=24)
    
    result = await async_session_repo.delete(session_id)
    assert result is True
    
    session = await async_session_repo.get_valid(session_id)
    assert session is None


# =============================================================================
# AsyncFolderRepository Tests
# =============================================================================

@pytest.mark.asyncio
async def test_async_folder_create(async_folder_repo, async_user_repo):
    """Test creating a folder asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    folder_id = await async_folder_repo.create("folder-1", "My Folder", user_id, None)
    
    assert folder_id == "folder-1"
    
    folder = await async_folder_repo.get_by_id(folder_id)
    assert folder["name"] == "My Folder"
    assert folder["user_id"] == user_id


@pytest.mark.asyncio
async def test_async_folder_get_children(async_folder_repo, async_user_repo):
    """Test getting folder children asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    parent_id = await async_folder_repo.create("parent", "Parent", user_id, None)
    await async_folder_repo.create("child1", "Child 1", user_id, parent_id)
    await async_folder_repo.create("child2", "Child 2", user_id, parent_id)
    
    children = await async_folder_repo.get_children(parent_id)
    
    assert len(children) == 2


@pytest.mark.asyncio
async def test_async_folder_delete(async_folder_repo, async_user_repo):
    """Test deleting a folder asynchronously."""
    user_id = await async_user_repo.create("testuser", "password123", "Test User")
    folder_id = await async_folder_repo.create("folder-del", "To Delete", user_id, None)
    
    result = await async_folder_repo.delete(folder_id)
    assert result is True
    
    folder = await async_folder_repo.get_by_id(folder_id)
    assert folder is None


# =============================================================================
# Performance Comparison Test
# =============================================================================

@pytest.mark.asyncio
async def test_async_concurrent_operations(async_user_repo):
    """Test that async operations work concurrently."""
    import asyncio
    
    async def create_user_task(name: str):
        return await async_user_repo.create(name, "password", f"User {name}")
    
    # Create 5 users concurrently
    tasks = [create_user_task(f"user{i}") for i in range(5)]
    user_ids = await asyncio.gather(*tasks)
    
    assert len(user_ids) == 5
    assert all(isinstance(uid, int) for uid in user_ids)
