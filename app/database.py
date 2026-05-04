"""Database module for v2.0 - clean schema, no migrations.

This module contains only essential database utilities:
- Connection management (sync)
- Password hashing (bcrypt only)
- Database initialization with current schema
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
    """Verify password against bcrypt hash."""
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    return False


# =============================================================================
# Database Connection
# =============================================================================
_local = threading.local()


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
            password_salt TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0,
            failed_login_attempts INTEGER DEFAULT 0,
            locked_until TIMESTAMP,
            last_login TIMESTAMP
        )
    """)

    # Migration: Add security columns to users if not exist
    cursor = db.execute("PRAGMA table_info(users)")
    user_columns = [row['name'] for row in cursor.fetchall()]
    if 'failed_login_attempts' not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0")
    if 'locked_until' not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP")
    if 'last_login' not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")

    # Sessions table for login sessions
    db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            encrypted_dek BLOB,
            fingerprint TEXT,
            ip_address TEXT,
            user_agent TEXT,
            last_active_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migration: Add new columns to sessions if not exist
    cursor = db.execute("PRAGMA table_info(sessions)")
    session_columns = [row['name'] for row in cursor.fetchall()]
    if 'ip_address' not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN ip_address TEXT")
    if 'user_agent' not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN user_agent TEXT")
    if 'last_active_at' not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN last_active_at TIMESTAMP")

    db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")

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
            cover_item_id TEXT,
            safe_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (cover_item_id) REFERENCES items(id) ON DELETE SET NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL
        )
    """)

    # =============================================================================
    # Tag System v2: Hierarchical Tags
    # =============================================================================
    
    # Tag categories (fixed set)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_categories (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        )
    """)
    
    # Tags (flat tags grouped by category)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            display_name TEXT,
            category_id INTEGER,
            usage_count INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id)
        )
    """)

    # Migration: Drop legacy tree columns via table recreation
    # SQLite cannot DROP COLUMN when it has a self-referencing foreign key,
    # so we recreate the table and copy data.
    cursor = db.execute("PRAGMA table_info(tags)")
    tag_columns = [row['name'] for row in cursor.fetchall()]
    if 'parent_id' in tag_columns:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("""
            CREATE TABLE tags_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                display_name TEXT,
                category_id INTEGER,
                usage_count INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES tag_categories(id)
            )
        """)
        db.execute("""
            INSERT INTO tags_new (id, name, display_name, category_id, usage_count, description, created_at)
            SELECT id, name, display_name, category_id, usage_count, description, created_at FROM tags
        """)
        db.execute("DROP TABLE tags")
        db.execute("ALTER TABLE tags_new RENAME TO tags")
        db.execute("PRAGMA foreign_keys = ON")

    # Migration: Add description column to tags if not exists
    cursor = db.execute("PRAGMA table_info(tags)")
    tag_columns = [row['name'] for row in cursor.fetchall()]
    if 'description' not in tag_columns:
        db.execute("ALTER TABLE tags ADD COLUMN description TEXT DEFAULT ''")

    # Item-tags relationship (many-to-many)
    # v3: stores both explicit (user-added) and implied (auto-resolved) tags
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            is_explicit INTEGER NOT NULL DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, tag_id),
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)

    # Tag implications: directed edges for semantic inheritance (e.g. sea -> water)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_implications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            implies_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(tag_id, implies_tag_id)
        )
    """)

    # Tag co-occurrence: statistical relatedness for UX suggestions
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_cooccurrence (
            tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            count INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tag_a_id, tag_b_id),
            CHECK (tag_a_id < tag_b_id)
        )
    """)

    # =============================================================================
    # Polymorphic Items Architecture
    # =============================================================================
    
    # Items table - polymorphic base for all content types
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            folder_id TEXT,
            safe_id TEXT,
            user_id INTEGER,
            uploaded_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
            title TEXT,
            metadata TEXT,
            is_encrypted INTEGER DEFAULT 0,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    
    # Item media table - photo/video specific data
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_media (
            item_id TEXT PRIMARY KEY,
            media_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_name TEXT,
            content_type TEXT,
            width INTEGER,
            height INTEGER,
            duration INTEGER,
            thumb_width INTEGER,
            thumb_height INTEGER,
            taken_at TIMESTAMP,
            storage_mode TEXT DEFAULT 'standard',
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    """)
    
    # Album items junction table
    db.execute("""
        CREATE TABLE IF NOT EXISTS album_items (
            album_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
            PRIMARY KEY (album_id, item_id),
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    """)
    
    # Indexes
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_type ON items(type)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_safe ON items(safe_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_album_items_album ON album_items(album_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_album_items_item ON album_items(item_id)")
    db.execute("DROP INDEX IF EXISTS idx_tags_path")
    db.execute("DROP INDEX IF EXISTS idx_tags_parent")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_explicit ON item_tags(item_id, is_explicit)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cooccurrence_a ON tag_cooccurrence(tag_a_id, count DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cooccurrence_b ON tag_cooccurrence(tag_b_id, count DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_albums_folder_id ON albums(folder_id)")

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
        CREATE TABLE IF NOT EXISTS folder_keys (
            folder_id TEXT PRIMARY KEY,
            encrypted_folder_dek TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migration: Add description and updated_at columns if not exist
    cursor = db.execute("PRAGMA table_info(items)")
    columns = [row['name'] for row in cursor.fetchall()]
    if 'description' not in columns:
        db.execute("ALTER TABLE items ADD COLUMN description TEXT")
    if 'updated_at' not in columns:
        # SQLite doesn't support DEFAULT with non-constant values in ALTER TABLE
        db.execute("ALTER TABLE items ADD COLUMN updated_at TIMESTAMP")
        # Set default for existing rows
        db.execute("UPDATE items SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    
    # Migration: Add file_size to item_media table
    cursor = db.execute("PRAGMA table_info(item_media)")
    media_columns = [row['name'] for row in cursor.fetchall()]
    if 'file_size' not in media_columns:
        db.execute("ALTER TABLE item_media ADD COLUMN file_size INTEGER")

    # AI Tagging Jobs table
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_tagging_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            processing_deadline TIMESTAMP,
            result_tags TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migration: Add user_id and processing_deadline to ai_tagging_jobs if not exist
    cursor = db.execute("PRAGMA table_info(ai_tagging_jobs)")
    ai_job_columns = [row['name'] for row in cursor.fetchall()]
    if 'user_id' not in ai_job_columns:
        db.execute("ALTER TABLE ai_tagging_jobs ADD COLUMN user_id INTEGER")
        # Assign existing jobs to the first available user (or admin)
        db.execute("UPDATE ai_tagging_jobs SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL")
    if 'processing_deadline' not in ai_job_columns:
        db.execute("ALTER TABLE ai_tagging_jobs ADD COLUMN processing_deadline TIMESTAMP")

    db.execute("CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_tagging_jobs(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ai_jobs_item ON ai_tagging_jobs(item_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ai_jobs_user ON ai_tagging_jobs(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ai_jobs_deadline ON ai_tagging_jobs(processing_deadline)")

    # AI API Keys table
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER REFERENCES users(id),
            created_by INTEGER REFERENCES users(id),
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            rate_limit_tier TEXT DEFAULT 'default'
        )
    """)

    # Migration: Add new columns to ai_api_keys if not exist
    cursor = db.execute("PRAGMA table_info(ai_api_keys)")
    api_key_columns = [row['name'] for row in cursor.fetchall()]
    if 'user_id' not in api_key_columns:
        db.execute("ALTER TABLE ai_api_keys ADD COLUMN user_id INTEGER REFERENCES users(id)")
    if 'created_by' not in api_key_columns:
        db.execute("ALTER TABLE ai_api_keys ADD COLUMN created_by INTEGER REFERENCES users(id)")
    if 'expires_at' not in api_key_columns:
        db.execute("ALTER TABLE ai_api_keys ADD COLUMN expires_at TIMESTAMP")
    if 'last_used_at' not in api_key_columns:
        db.execute("ALTER TABLE ai_api_keys ADD COLUMN last_used_at TIMESTAMP")
    if 'rate_limit_tier' not in api_key_columns:
        db.execute("ALTER TABLE ai_api_keys ADD COLUMN rate_limit_tier TEXT DEFAULT 'default'")

    # Create default admin user if no users exist (first run)
    cursor = db.execute("SELECT COUNT(*) as count FROM users")
    if cursor.fetchone()["count"] == 0:
        import bcrypt
        
        default_username = "admin"
        default_password = "admin"
        
        hashed = bcrypt.hashpw(default_password.encode('utf-8'), bcrypt.gensalt())
        
        db.execute(
            """INSERT INTO users 
               (username, password_hash, password_salt, display_name, is_admin) 
               VALUES (?, ?, ?, ?, ?)""",
            (default_username, hashed.decode('utf-8'), "", "Administrator", 1)
        )
        
        print("=" * 70)
        print("FIRST RUN: Default admin account created")
        print("=" * 70)
        print(f"   Username: {default_username}")
        print(f"   Password: {default_password}")
        print("")
        print("   Please log in and create a new admin user immediately,")
        print("   then delete this temporary account for security.")
        print("=" * 70)

    db.commit()


