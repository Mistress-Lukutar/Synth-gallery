"""Test configuration and fixtures for Synth Gallery.

This module provides isolated test environments:
- Temporary database (SQLite)
- Temporary uploads/thumbnails/fallbacks/backups directories
- Fresh user sessions for each test

Isolation strategy
------------------
All persistent-state locations (database file, uploads, thumbnails,
fallbacks, backups) are redirected to a throwaway session directory via
environment variables set at conftest import time - BEFORE any ``app.*``
module is imported. ``app.config`` and ``app.database`` resolve their paths
at import time from these variables, so no test can ever touch the real
``gallery.db``, ``uploads/`` or ``thumbnails/`` in the project root,
regardless of which fixtures it uses.

The storage factory singleton also resolves its base path from
``config.UPLOADS_DIR`` (call-time), so file writes land in the same
throwaway directory. Background schedulers (backup, tag stats) are disabled
via ``BACKUP_SCHEDULE``/``TAG_STATS_SCHEDULE``.

Each test still gets its own fresh database file (see ``patched_config`` /
``fresh_database``) so tests cannot observe each other's DB state.
"""
import atexit
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Dict, Any

import pytest
from fastapi.testclient import TestClient

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Redirect ALL persistent state to a throwaway directory BEFORE app import.
# ---------------------------------------------------------------------------
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="synth-gallery-tests-"))
atexit.register(lambda: shutil.rmtree(TEST_DATA_ROOT, ignore_errors=True))

os.environ["SYNTH_DB_PATH"] = str(TEST_DATA_ROOT / "gallery.db")
os.environ["SYNTH_UPLOADS_DIR"] = str(TEST_DATA_ROOT / "uploads")
os.environ["SYNTH_THUMBNAILS_DIR"] = str(TEST_DATA_ROOT / "thumbnails")
os.environ["SYNTH_FALLBACKS_DIR"] = str(TEST_DATA_ROOT / "fallbacks")
os.environ["BACKUP_PATH"] = str(TEST_DATA_ROOT / "backups")
# Background schedulers must never fire during tests.
os.environ["BACKUP_SCHEDULE"] = "disabled"
os.environ["TAG_STATS_SCHEDULE"] = "disabled"
# Misc test-friendly settings.
os.environ["SYNTH_BASE_URL"] = ""
os.environ["WEBAUTHN_RP_NAME"] = "Test Synth Gallery"
os.environ["COOKIE_SECURE"] = "false"

from app.config import SESSION_COOKIE, CSRF_COOKIE_NAME

# Import repositories
from app.infrastructure.repositories import (
    UserRepository,
    FolderRepository,
)
from app.database import init_db


@pytest.fixture(scope="function", autouse=True)
def reset_rate_limiter():
    """Reset rate limiter state before each test."""
    from app.middleware import RateLimitMiddleware
    RateLimitMiddleware.reset()


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Directory containing test fixtures (images, etc.)."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="function")
def isolated_environment(tmp_path: Path) -> Dict:
    """Create isolated environment for a single test.

    Uploads/thumbnails/backups directories are session-scoped and shared
    between tests (they live under TEST_DATA_ROOT and are removed at exit).
    Only the database is per-test, which keeps tests independent where it
    matters (queryable state) without constant storage re-initialisation.

    Returns:
        Dict with paths: db, uploads, thumbnails, backups
    """
    return {
        "db_path": tmp_path / "test.db",
        "uploads_dir": Path(os.environ["SYNTH_UPLOADS_DIR"]),
        "thumbnails_dir": Path(os.environ["SYNTH_THUMBNAILS_DIR"]),
        "backups_dir": Path(os.environ["BACKUP_PATH"]),
        "base_dir": TEST_DATA_ROOT,
    }


@pytest.fixture(scope="function")
def patched_config(isolated_environment: Dict):
    """Point the database at a fresh per-test file.

    Uploads/thumbnails/fallbacks/backups paths are env-driven and stable for
    the whole session (see TEST_DATA_ROOT above), so only DATABASE_PATH
    needs patching here.
    """
    import app.database as db_module

    original = db_module.DATABASE_PATH

    # Apply patch
    db_module.DATABASE_PATH = isolated_environment["db_path"]

    yield isolated_environment

    # Restore original value
    db_module.DATABASE_PATH = original


@pytest.fixture(scope="function")
def fresh_database(patched_config: Dict):
    """Initialize fresh database with schema for each test.

    IMPORTANT: This ensures each test starts with clean state.
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
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME, "")

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
    assert SESSION_COOKIE in response.cookies, "Session cookie should be set"

    return client


@pytest.fixture(scope="function")
def csrf_token(authenticated_client: TestClient) -> str:
    """Get CSRF token for authenticated client."""
    return authenticated_client.cookies.get(CSRF_COOKIE_NAME, "")


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
        "/api/uploads",
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
def test_album(
    authenticated_client: TestClient,
    test_folder: str,
    test_image_bytes: bytes,
    csrf_token: str
) -> Dict:
    """Create a test album with photos and return its metadata.

    Returns:
        Dict with: id, name, item_ids, item_count
    """
    # Upload multiple photos
    item_ids = []
    for i in range(3):
        response = authenticated_client.post(
            "/api/uploads",
            data={"folder_id": test_folder},
            files={"file": (f"album_{i}.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        item_ids.append(response.json()["id"])

    # Create album
    response = authenticated_client.post(
        "/api/albums",
        json={
            "name": "Test Album",
            "folder_id": test_folder,
            "item_ids": item_ids
        },
        headers={"X-CSRF-Token": csrf_token}
    )

    assert response.status_code == 200
    data = response.json()

    return {
        "id": data["album_id"],
        "name": "Test Album",
        "item_ids": item_ids,
        "item_count": data["item_count"],
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
