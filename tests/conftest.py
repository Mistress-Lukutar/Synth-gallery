"""Test configuration and fixtures for Synth Gallery.

This module provides isolated test environments:
- Temporary database (SQLite)
- Temporary uploads/thumbnails directories
- Fresh user sessions for each test
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Generator, Dict
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment BEFORE importing app modules
os.environ["SYNTH_BASE_URL"] = ""
os.environ["BACKUP_PATH"] = ""
os.environ["WEBAUTHN_RP_NAME"] = "Test Synth Gallery"


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
        "BASE_DIR": config.BASE_DIR,
    }
    
    # Apply patches
    config.UPLOADS_DIR = isolated_environment["uploads_dir"]
    config.THUMBNAILS_DIR = isolated_environment["thumbnails_dir"]
    config.BACKUP_PATH = isolated_environment["backups_dir"]
    config.BASE_DIR = isolated_environment["base_dir"]
    db_module.DATABASE_PATH = isolated_environment["db_path"]
    db_module.BASE_DIR = isolated_environment["base_dir"]
    
    yield isolated_environment
    
    # Restore original values
    config.UPLOADS_DIR = originals["UPLOADS_DIR"]
    config.THUMBNAILS_DIR = originals["THUMBNAILS_DIR"]
    config.BACKUP_PATH = originals["BACKUP_PATH"]
    db_module.DATABASE_PATH = originals["DATABASE_PATH"]
    db_module.BASE_DIR = originals["BASE_DIR"]


@pytest.fixture(scope="function")
def fresh_database(patched_config: Dict):
    """Initialize fresh database with schema for each test.
    
    IMPORTANT: This ensures each test starts with clean state.
    After refactoring to Repository pattern, this fixture should
    still work - just change how it initializes the schema.
    """
    from app.database import init_db
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
def test_user(client: TestClient) -> Dict:
    """Create a test user and return credentials.
    
    Returns:
        Dict with: id, username, password, display_name
    """
    from app.database import create_user
    
    credentials = {
        "username": "testuser",
        "password": "TestPass123!",
        "display_name": "Test User"
    }
    
    user_id = create_user(
        credentials["username"],
        credentials["password"],
        credentials["display_name"]
    )
    
    credentials["id"] = user_id
    return credentials


@pytest.fixture(scope="function")
def second_user(client: TestClient) -> Dict:
    """Create a second user for permission testing."""
    from app.database import create_user
    
    credentials = {
        "username": "seconduser",
        "password": "SecondPass123!",
        "display_name": "Second User"
    }
    
    user_id = create_user(
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
    response = client.post(
        "/login",
        data={
            "username": test_user["username"],
            "password": test_user["password"]
        },
        follow_redirects=False
    )
    
    assert response.status_code == 302, "Login should redirect to gallery"
    assert "synth_session" in response.cookies, "Session cookie should be set"
    
    return client


@pytest.fixture(scope="function")
def test_folder(authenticated_client: TestClient, test_user: Dict) -> str:
    """Create a test folder and return its ID.
    
    Uses direct DB call (will need updating when refactoring to Repository).
    During refactoring, replace with API call once folder creation endpoint exists.
    """
    from app.database import create_folder
    
    folder_id = create_folder("Test Folder", test_user["id"])
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
    test_image_bytes: bytes
) -> Dict:
    """Upload a test photo and return its metadata.
    
    Returns:
        Dict with: id, filename, media_type
    """
    response = authenticated_client.post(
        "/upload",
        data={"folder_id": test_folder},
        files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
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
def encrypted_user(client: TestClient) -> Dict:
    """Create user with encryption enabled (DEK in cache).
    
    This simulates production setup where encryption is enabled.
    """
    from app.database import create_user
    
    credentials = {
        "username": "encrypteduser",
        "password": "EncryptPass123!",
        "display_name": "Encrypted User"
    }
    
    user_id = create_user(
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
def temp_folder(client: TestClient, user_id: int, name: str = "Temp Folder"):
    """Context manager that creates and cleans up a folder.
    
    Usage:
        with temp_folder(client, user_id, "My Folder") as folder_id:
            # use folder_id
            pass
        # folder is automatically deleted
    """
    from app.database import create_folder, delete_folder
    
    folder_id = create_folder(name, user_id)
    try:
        yield folder_id
    finally:
        delete_folder(folder_id)
