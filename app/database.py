"""Database module for v1.0 - minimal utilities only.

This module contains only essential database utilities:
- Connection management (sync)
- Password hashing
- Database initialization
- Session cleanup

All CRUD operations have been moved to repositories in infrastructure/repositories/.
"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import bcrypt

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "gallery.db"


# =============================================================================
# SQLite3 datetime adapter (Python 3.12 compatibility)
# =============================================================================
def _adapt_datetime(dt: datetime) -> str:
    """Adapt datetime to ISO 8601 string for SQLite."""
    return dt.isoformat()


def _convert_datetime(val: bytes) -> datetime:
    """Convert ISO 8601 string from SQLite to datetime."""
    return datetime.fromisoformat(val.decode())


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("DATETIME", _convert_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)


# =============================================================================
# Password Hashing
# =============================================================================
def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hash password using bcrypt.

    Note: salt parameter is ignored for bcrypt (it generates its own).
    Kept for backward compatibility with existing code.
    Returns (hash, empty_string) tuple for API compatibility.
    """
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8'), ""


def verify_password(password: str, hashed: str, salt: str = None) -> bool:
    """Verify password against bcrypt hash.

    Also handles legacy SHA-256 hashes for migration.
    """
    # Check if this is a bcrypt hash (starts with $2b$)
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

    # Legacy SHA-256 verification for old passwords
    import hashlib
    if salt:
        check_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        if check_hash == hashed:
            return True

    return False


# =============================================================================
# Database Connection
# =============================================================================
_local = threading.local()
_migration_backup_done = False


def _backup_before_migration():
    """Create backup before running migrations (once per init)."""
    global _migration_backup_done
    if _migration_backup_done:
        return

    try:
        from .infrastructure.services.backup import create_backup
        if DATABASE_PATH.exists():
            create_backup("pre-migration")
            _migration_backup_done = True
    except Exception as e:
        # Don't fail init if backup fails, just log
        print(f"Warning: Could not create pre-migration backup: {e}")


def get_db() -> sqlite3.Connection:
    """Get thread-local database connection.

    WARNING: Do NOT close this connection! It's reused across the thread.
    For contexts where you need to close the connection, use create_connection().
    """
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(
            DATABASE_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        _local.connection.row_factory = sqlite3.Row
    return _local.connection


def create_connection() -> sqlite3.Connection:
    """Create a new database connection.

    Use this when you need a connection that you can safely close.
    Always close this connection when done using it.

    Example:
        db = create_connection()
        try:
            repo = UserRepository(db)
            user = repo.get_by_id(1)
        finally:
            db.close()

    Returns:
        New sqlite3.Connection with row_factory set
    """
    conn = sqlite3.connect(
        DATABASE_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES
    )
    conn.row_factory = sqlite3.Row
    return conn


def cleanup_expired_sessions():
    """Remove expired sessions from database."""
    from .infrastructure.repositories import SessionRepository
    SessionRepository(create_connection()).cleanup_expired()


# =============================================================================
# Database Schema Initialization
# =============================================================================
def init_db():
    """Initialize database schema."""
    db = get_db()

    # Users table for authentication
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0
        )
    """)

    # Sessions table for login sessions
    db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # WebAuthn credentials for hardware key authentication
    db.execute("""
        CREATE TABLE IF NOT EXISTS webauthn_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            credential_id BLOB NOT NULL UNIQUE,
            public_key BLOB NOT NULL,
            sign_count INTEGER DEFAULT 0,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            encrypted_dek BLOB,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Folders table for organizing content
    db.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT,
            user_id INTEGER NOT NULL,
            safe_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL
        )
    """)

    # Folder permissions table for sharing
    db.execute("""
        CREATE TABLE IF NOT EXISTS folder_permissions (
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

    # Albums table
    db.execute("""
        CREATE TABLE IF NOT EXISTS albums (
            id TEXT PRIMARY KEY,
            name TEXT,
            folder_id TEXT,
            user_id INTEGER,
            cover_photo_id TEXT,
            safe_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (cover_photo_id) REFERENCES photos(id) ON DELETE SET NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL
        )
    """)

    # Photos table
    db.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            folder_id TEXT,
            user_id INTEGER,
            album_id TEXT,
            position INTEGER DEFAULT 0,
            media_type TEXT DEFAULT 'image',
            is_encrypted INTEGER DEFAULT 0,
            safe_id TEXT,
            taken_at TIMESTAMP,
            thumb_width INTEGER,
            thumb_height INTEGER,
            storage_mode TEXT,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL
        )
    """)

    # Tag categories with colors
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL
        )
    """)

    # Preset tags library
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id),
            UNIQUE(name, category_id)
        )
    """)

    # Photo tags with category reference
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            category_id INTEGER,
            confidence REAL DEFAULT 1.0,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id)
        )
    """)

    # Indexes
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_photo_id ON tags(photo_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tag_presets_category ON tag_presets(category_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_photos_album_id ON photos(album_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_photos_folder_id ON photos(folder_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_albums_folder_id ON albums(folder_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_photos_taken_at ON photos(taken_at)")

    # User folder preferences (sort settings per user per folder)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_folder_preferences (
            user_id INTEGER NOT NULL,
            folder_id TEXT NOT NULL,
            sort_by TEXT DEFAULT 'uploaded',
            PRIMARY KEY (user_id, folder_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    # User settings (global user preferences like default folder)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            default_folder_id TEXT,
            encrypted_dek BLOB,
            dek_salt BLOB,
            encryption_version INTEGER DEFAULT 1,
            recovery_encrypted_dek BLOB,
            collapsed_folders TEXT DEFAULT '[]',
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (default_folder_id) REFERENCES folders(id) ON DELETE SET NULL
        )
    """)

    # Safes table - encrypted vaults
    db.execute("""
        CREATE TABLE IF NOT EXISTS safes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            encrypted_dek BLOB NOT NULL,
            unlock_type TEXT NOT NULL CHECK(unlock_type IN ('password', 'webauthn')),
            credential_id BLOB,
            salt BLOB,
            recovery_encrypted_dek BLOB,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_safes_user_id ON safes(user_id)")

    # Safe sessions - temporary unlocked safe keys
    db.execute("""
        CREATE TABLE IF NOT EXISTS safe_sessions (
            id TEXT PRIMARY KEY,
            safe_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            encrypted_dek BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    db.execute("CREATE INDEX IF NOT EXISTS idx_safe_sessions_safe_id ON safe_sessions(safe_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_safe_sessions_user_id ON safe_sessions(user_id)")

    # Envelope encryption tables
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_public_keys (
            user_id INTEGER PRIMARY KEY,
            public_key BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS photo_keys (
            photo_id TEXT PRIMARY KEY,
            encrypted_ck BLOB NOT NULL,
            thumbnail_encrypted_ck BLOB,
            shared_ck_map TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS folder_keys (
            folder_id TEXT PRIMARY KEY,
            encrypted_folder_dek TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Insert default categories if not exist
    default_categories = [
        ("Subject", "#3b82f6"),
        ("Location", "#22c55e"),
        ("Mood", "#f59e0b"),
        ("Style", "#a855f7"),
        ("Event", "#ef4444"),
        ("Other", "#6b7280"),
    ]

    for name, color in default_categories:
        db.execute(
            "INSERT OR IGNORE INTO tag_categories (name, color) VALUES (?, ?)",
            (name, color)
        )

    # Insert some default preset tags
    default_presets = [
        ("person", "Subject"), ("people", "Subject"), ("portrait", "Subject"),
        ("animal", "Subject"), ("dog", "Subject"), ("cat", "Subject"),
        ("bird", "Subject"), ("flower", "Subject"), ("tree", "Subject"),
        ("car", "Subject"), ("building", "Subject"), ("food", "Subject"),
        ("outdoor", "Location"), ("indoor", "Location"), ("city", "Location"),
        ("nature", "Location"), ("beach", "Location"), ("mountain", "Location"),
        ("forest", "Location"), ("park", "Location"), ("home", "Location"),
        ("street", "Location"), ("studio", "Location"),
        ("happy", "Mood"), ("calm", "Mood"), ("dramatic", "Mood"),
        ("romantic", "Mood"), ("mysterious", "Mood"), ("energetic", "Mood"),
        ("peaceful", "Mood"), ("melancholic", "Mood"),
        ("black and white", "Style"), ("vintage", "Style"), ("minimalist", "Style"),
        ("abstract", "Style"), ("macro", "Style"), ("panorama", "Style"),
        ("long exposure", "Style"), ("bokeh", "Style"),
        ("wedding", "Event"), ("birthday", "Event"), ("travel", "Event"),
        ("vacation", "Event"), ("concert", "Event"), ("sports", "Event"),
        ("family", "Event"), ("party", "Event"),
    ]

    for tag_name, category_name in default_presets:
        db.execute("""
            INSERT OR IGNORE INTO tag_presets (name, category_id)
            SELECT ?, id FROM tag_categories WHERE name = ?
        """, (tag_name, category_name))

    # Migration: Add content_type column for extension-less storage (Issue #22)
    try:
        db.execute("ALTER TABLE photos ADD COLUMN content_type TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    db.commit()
