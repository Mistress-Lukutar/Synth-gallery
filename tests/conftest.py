"""Test configuration and fixtures for Synth Gallery.

This module provides isolated test environments:
- Temporary database (SQLite)
- Temporary uploads/thumbnails directories
- Fresh user sessions for each test
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Dict, Any

import pytest
from fastapi.testclient import TestClient

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment BEFORE importing app modules
os.environ["SYNTH_BASE_URL"] = ""
os.environ["BACKUP_PATH"] = ""
os.environ["WEBAUTHN_RP_NAME"] = "Test Synth Gallery"

# Import repositories
from app.infrastructure.repositories import (
    UserRepository,
    FolderRepository,
)
from app.database import init_db


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Directory containing test fixtures (images, etc.)."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="function")
def isolated_environment(tmp_path: Path) -> Dict:
    """Create completely isolated environment for a single test.
    
    Returns:
        Dict with paths: db, uploads, thumbnails, backups
    """
    env = {
        "db_path": tmp_path / "test.db",
        "uploads_dir": tmp_path / "uploads",
        "thumbnails_dir": tmp_path / "thumbnails",
        "backups_dir": tmp_path / "backups",
        "base_dir": tmp_path
    }
    
    # Create directories
    for dir_path in [env["uploads_dir"], env["thumbnails_dir"], env["backups_dir"]]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    return env


@pytest.fixture(scope="function")
def patched_config(isolated_environment: Dict):
    """Monkey-patch app configuration to use isolated directories."""
    import app.config as config
    import app.database as db_module
    
    # Store original values
    originals = {
        "UPLOADS_DIR": config.UPLOADS_DIR,
        "THUMBNAILS_DIR": config.THUMBNAILS_DIR,
        "BACKUP_PATH": config.BACKUP_PATH,
        "DATABASE_PATH": db_module.DATABASE_PATH,
    }
    
    # Apply patches - NOTE: We don't patch BASE_DIR to keep static files working
    config.UPLOADS_DIR = isolated_environment["uploads_dir"]
    config.THUMBNAILS_DIR = isolated_environment["thumbnails_dir"]
    config.BACKUP_PATH = isolated_environment["backups_dir"]
    db_module.DATABASE_PATH = isolated_environment["db_path"]
    db_module.BASE_DIR = isolated_environment["base_dir"]
    
    yield isolated_environment
    
    # Restore original values
    config.UPLOADS_DIR = originals["UPLOADS_DIR"]
    config.THUMBNAILS_DIR = originals["THUMBNAILS_DIR"]
    config.BACKUP_PATH = originals["BACKUP_PATH"]
    db_module.DATABASE_PATH = originals["DATABASE_PATH"]
    db_module.BASE_DIR = config.BASE_DIR  # Restore from config


@pytest.fixture(scope="function")
def fresh_database(patched_config: Dict):
    """Initialize fresh database with schema for each test.
    
    IMPORTANT: This ensures each test starts with clean state.
    After refactoring to Repository pattern, this fixture should
    still work - just change how it initializes the schema.
    """
    import app.database as db_module
    
    # Reset any existing thread-local connections
    if hasattr(db_module, '_local') and hasattr(db_module._local, 'connection'):
        try:
            if db_module._local.connection:
                db_module._local.connection.close()
        except:
            pass
        db_module._local.connection = None
    
    # Initialize fresh schema
    init_db()
    
    yield patched_config["db_path"]
    
    # Cleanup: close connections
    if hasattr(db_module, '_local') and hasattr(db_module._local, 'connection'):
        try:
            if db_module._local.connection:
                db_module._local.connection.close()
        except:
            pass
        db_module._local.connection = None


@pytest.fixture(scope="function")
def db_connection(fresh_database: Path):
    """Provide a database connection for repositories.
    
    Uses the app's thread-local connection to ensure consistency
    with the application's database access pattern.
    
    Returns:
        sqlite3.Connection with row_factory set
    """
    from app.database import get_db
    conn = get_db()
    yield conn
    # Don't close - get_db() manages its own connection lifecycle


@pytest.fixture(scope="function")
def client(fresh_database: Path) -> Generator[TestClient, None, None]:
    """Create test client with fresh isolated environment.
    
    Usage:
        def test_something(client):
            response = client.get("/")
            assert response.status_code == 200
    """
    from app.main import app
    
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="function")
def test_user(db_connection) -> Dict[str, any]:
    """Create a test user and return credentials.
    
    Returns:
        Dict with: id, username, password, display_name
    """
    credentials: Dict[str, any] = {
        "username": "testuser",
        "password": "TestPass123!",
        "display_name": "Test User"
    }
    
    user_id = UserRepository(db_connection).create(
        credentials["username"],
        credentials["password"],
        credentials["display_name"]
    )
    
    credentials["id"] = user_id
    return credentials


@pytest.fixture(scope="function")
def second_user(db_connection) -> Dict[str, any]:
    """Create a second user for permission testing."""
    credentials: Dict[str, any] = {
        "username": "seconduser",
        "password": "SecondPass123!",
        "display_name": "Second User"
    }
    
    user_id = UserRepository(db_connection).create(
        credentials["username"],
        credentials["password"],
        credentials["display_name"]
    )
    
    credentials["id"] = user_id
    return credentials


@pytest.fixture(scope="function")
def authenticated_client(client: TestClient, test_user: Dict) -> TestClient:
    """Client authenticated as test_user.
    
    Usage:
        def test_protected(authenticated_client):
            response = authenticated_client.get("/")
            assert response.status_code == 200  # Not 302 redirect to login
    """
    # First get login page to obtain CSRF token
    client.get("/login")
    csrf_token = client.cookies.get("synth_csrf", "")
    
    response = client.post(
        "/login",
        data={
            "username": test_user["username"],
            "password": test_user["password"],
            "csrf_token": csrf_token
        },
        follow_redirects=False
    )
    
    assert response.status_code == 302, "Login should redirect to gallery"
    assert "synth_session" in response.cookies, "Session cookie should be set"
    
    return client


@pytest.fixture(scope="function")
def csrf_token(authenticated_client: TestClient) -> str:
    """Get CSRF token for authenticated client."""
    return authenticated_client.cookies.get("synth_csrf", "")


@pytest.fixture(scope="function")
def test_folder(db_connection, test_user: Dict) -> str:
    """Create a test folder and return its ID.
    
    Uses Repository pattern for folder creation.
    """
    folder_id = FolderRepository(db_connection).create(
        "Test Folder", 
        test_user["id"]
    )
    return folder_id


@pytest.fixture(scope="function")
def test_image_bytes() -> bytes:
    """Create minimal valid JPEG image in memory.
    
    Returns:
        JPEG file as bytes
    """
    from PIL import Image
    import io
    
    img = Image.new('RGB', (100, 100), color='red')
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='JPEG', quality=85)
    return img_bytes.getvalue()


@pytest.fixture(scope="function")
def uploaded_photo(
    authenticated_client: TestClient,
    test_folder: str,
    test_image_bytes: bytes,
    csrf_token: str
) -> Dict:
    """Upload a test photo and return its metadata.
    
    Returns:
        Dict with: id, filename, media_type
    """
    response = authenticated_client.post(
        "/upload",
        data={"folder_id": test_folder},
        files={"file": ("test.jpg", test_image_bytes, "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token}
    )
    
    assert response.status_code == 200, f"Upload failed: {response.text}"
    data = response.json()
    
    return {
        "id": data["id"],
        "filename": data["filename"],
        "media_type": data["media_type"],
        "folder_id": test_folder
    }


@pytest.fixture(scope="function")
def encrypted_user(db_connection, client: TestClient) -> Dict[str, Any]:
    """Create user with encryption enabled (DEK in cache).
    
    This simulates production setup where encryption is enabled.
    """
    credentials: Dict[str, Any] = {
        "username": "encrypteduser",
        "password": "EncryptPass123!",
        "display_name": "Encrypted User"
    }
    
    user_id = UserRepository(db_connection).create(
        credentials["username"],
        credentials["password"],
        credentials["display_name"]
    )
    credentials["id"] = user_id
    
    # Login to trigger DEK generation
    response = client.post(
        "/login",
        data={
            "username": credentials["username"],
            "password": credentials["password"]
        },
        follow_redirects=False
    )
    
    assert response.status_code == 302
    return credentials


# ============================================================================
# Helper context managers for complex scenarios
# ============================================================================

@contextmanager
def login_as(client: TestClient, username: str, password: str):
    """Context manager to temporarily login as different user.
    
    Usage:
        with login_as(client, "other", "pass"):
            response = client.get("/api/folders/tree")
            # acting as 'other' user
    """
    # Clear existing session
    client.cookies.clear()
    
    # Login
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False
    )
    assert response.status_code == 302
    
    try:
        yield client
    finally:
        # Cleanup: logout
        client.get("/logout")


@contextmanager
def temp_folder(db_connection, client: TestClient, user_id: int, name: str = "Temp Folder"):
    """Context manager that creates and cleans up a folder.
    
    Usage:
        with temp_folder(db_connection, client, user_id, "My Folder") as folder_id:
            # use folder_id
            pass
        # folder is automatically deleted
    """
    folder_repo = FolderRepository(db_connection)
    folder_id = folder_repo.create(name, user_id)
    try:
        yield folder_id
    finally:
        folder_repo.delete(folder_id)
