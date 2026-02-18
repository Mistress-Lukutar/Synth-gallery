import sqlite3
import secrets
import threading
import warnings
from pathlib import Path

import bcrypt

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "gallery.db"


# =============================================================================
# REFACTORING: Repository Pattern Migration
# =============================================================================
# New code uses UserRepository from infrastructure.repositories
# Old functions below are kept for backward compatibility but delegate to Repository
# =============================================================================

def _get_user_repo():
    """Get UserRepository instance with current DB connection."""
    # Lazy import to avoid circular dependencies
    from .infrastructure.repositories import UserRepository
    return UserRepository(get_db())


def _get_session_repo():
    """Get SessionRepository instance with current DB connection."""
    from .infrastructure.repositories import SessionRepository
    return SessionRepository(get_db())


def _get_folder_repo():
    """Get FolderRepository instance with current DB connection."""
    from .infrastructure.repositories import FolderRepository
    return FolderRepository(get_db())


def _get_permission_repo():
    """Get PermissionRepository instance with current DB connection."""
    from .infrastructure.repositories import PermissionRepository
    return PermissionRepository(get_db())


def _get_photo_repo():
    """Get PhotoRepository instance with current DB connection."""
    from .infrastructure.repositories import PhotoRepository
    return PhotoRepository(get_db())


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

# Thread-local storage for database connections
_local = threading.local()

# Flag to prevent multiple backups during single init
_migration_backup_done = False


def _backup_before_migration():
    """Create backup before running migrations (once per init)."""
    global _migration_backup_done
    if _migration_backup_done:
        return

    try:
        from .services.backup import create_backup
        if DATABASE_PATH.exists():
            create_backup("pre-migration")
            _migration_backup_done = True
    except Exception as e:
        # Don't fail init if backup fails, just log
        print(f"Warning: Could not create pre-migration backup: {e}")


def get_db() -> sqlite3.Connection:
    """Get thread-local database connection"""
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(DATABASE_PATH)
        _local.connection.row_factory = sqlite3.Row
    return _local.connection


def init_db():
    """Initialize database schema"""
    db = get_db()

    # Users table for authentication
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ai_processed INTEGER DEFAULT 0,
            album_id TEXT,
            position INTEGER DEFAULT 0,
            media_type TEXT DEFAULT 'image',
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
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

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_photo_id ON tags(photo_id)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tag_presets_category ON tag_presets(category_id)
    """)

    # Migration: add category_id to existing tags table if missing
    columns = [row[1] for row in db.execute("PRAGMA table_info(tags)").fetchall()]
    if "category_id" not in columns:
        db.execute("ALTER TABLE tags ADD COLUMN category_id INTEGER")

    # Migration: add album_id, position, and media_type to existing photos table if missing
    photo_columns = [row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()]
    if "album_id" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN album_id TEXT")
    if "position" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN position INTEGER DEFAULT 0")
    if "media_type" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN media_type TEXT DEFAULT 'image'")

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_album_id ON photos(album_id)
    """)

    # Folders indexes
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)
    """)

    # Migration: add default_folder_id and is_admin to users
    user_columns = [row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()]

    # Check if any user migrations are needed
    pending_user_migrations = []
    if "default_folder_id" not in user_columns:
        pending_user_migrations.append("default_folder_id")
    if "is_admin" not in user_columns:
        pending_user_migrations.append("is_admin")

    # Create backup before migrations
    if pending_user_migrations:
        _backup_before_migration()

    if "default_folder_id" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN default_folder_id TEXT")
    if "is_admin" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")

    # Migration: add folder_id, user_id, cover_photo_id to albums
    album_columns = [row[1] for row in db.execute("PRAGMA table_info(albums)").fetchall()]
    if "folder_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN folder_id TEXT")
    if "user_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN user_id INTEGER")
    if "cover_photo_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN cover_photo_id TEXT")

    # Migration: add folder_id and user_id to photos
    if "folder_id" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN folder_id TEXT")
    else:
        # Fix: check if folder_id has wrong type (INTEGER instead of TEXT)
        photo_column_types = {row[1]: row[2] for row in db.execute("PRAGMA table_info(photos)").fetchall()}
        if photo_column_types.get("folder_id", "").upper() == "INTEGER":
            # Need to fix column type - SQLite requires table recreation
            db.execute("""
                CREATE TABLE IF NOT EXISTS photos_new (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    original_name TEXT,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ai_processed INTEGER DEFAULT 0,
                    album_id TEXT,
                    position INTEGER DEFAULT 0,
                    media_type TEXT DEFAULT 'image',
                    folder_id TEXT,
                    user_id INTEGER,
                    FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
                )
            """)
            db.execute("""
                INSERT INTO photos_new (id, filename, original_name, uploaded_at, ai_processed, album_id, position, media_type, folder_id, user_id)
                SELECT id, filename, original_name, uploaded_at, ai_processed, album_id, position, media_type,
                       CAST(folder_id AS TEXT), user_id
                FROM photos
            """)
            db.execute("DROP TABLE photos")
            db.execute("ALTER TABLE photos_new RENAME TO photos")
            db.execute("CREATE INDEX IF NOT EXISTS idx_photos_album_id ON photos(album_id)")

    if "user_id" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN user_id INTEGER")

    # Migration: add taken_at (photo capture date from EXIF) to photos
    photo_columns = [row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()]
    if "taken_at" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN taken_at TIMESTAMP")

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_taken_at ON photos(taken_at)
    """)

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
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (default_folder_id) REFERENCES folders(id) ON DELETE SET NULL
        )
    """)

    # Migration: move default_folder_id from users to user_settings
    if "default_folder_id" in user_columns:
        # Check if migration needed (user_settings is empty but users have default_folder_id)
        existing_settings = db.execute("SELECT COUNT(*) as cnt FROM user_settings").fetchone()["cnt"]
        users_with_default = db.execute(
            "SELECT id, default_folder_id FROM users WHERE default_folder_id IS NOT NULL"
        ).fetchall()

        if existing_settings == 0 and len(users_with_default) > 0:
            _backup_before_migration()
            for user_row in users_with_default:
                db.execute(
                    "INSERT OR REPLACE INTO user_settings (user_id, default_folder_id) VALUES (?, ?)",
                    (user_row["id"], user_row["default_folder_id"])
                )
            db.commit()

    # Migration: add collapsed_folders to user_settings
    settings_columns = [row[1] for row in db.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "collapsed_folders" not in settings_columns:
        db.execute("ALTER TABLE user_settings ADD COLUMN collapsed_folders TEXT DEFAULT '[]'")

    # Migration: add encryption fields to user_settings
    if "encrypted_dek" not in settings_columns:
        _backup_before_migration()
        db.execute("ALTER TABLE user_settings ADD COLUMN encrypted_dek BLOB")
    if "dek_salt" not in settings_columns:
        db.execute("ALTER TABLE user_settings ADD COLUMN dek_salt BLOB")
    if "encryption_version" not in settings_columns:
        db.execute("ALTER TABLE user_settings ADD COLUMN encryption_version INTEGER DEFAULT 1")
    if "recovery_encrypted_dek" not in settings_columns:
        db.execute("ALTER TABLE user_settings ADD COLUMN recovery_encrypted_dek BLOB")

    # Migration: add is_encrypted to photos
    photo_columns = [row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()]
    if "is_encrypted" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN is_encrypted INTEGER DEFAULT 0")

    # Migration: add thumbnail dimensions to photos (for instant placeholder rendering)
    if "thumb_width" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN thumb_width INTEGER")
    if "thumb_height" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN thumb_height INTEGER")

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_albums_folder_id ON albums(folder_id)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_folder_id ON photos(folder_id)
    """)

    # Migration: remove access_mode from folders (replaced by folder_permissions)
    folder_columns = {row[1] for row in db.execute("PRAGMA table_info(folders)").fetchall()}
    if "access_mode" in folder_columns:
        # SQLite doesn't support DROP COLUMN, need to recreate table
        db.execute("""
            CREATE TABLE IF NOT EXISTS folders_new (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            INSERT INTO folders_new (id, name, parent_id, user_id, created_at)
            SELECT id, name, parent_id, user_id, created_at FROM folders
        """)
        db.execute("DROP TABLE folders")
        db.execute("ALTER TABLE folders_new RENAME TO folders")

    # ==========================================================================
    # SAFES - Encrypted vaults with separate keys
    # ==========================================================================
    
    # Safes table - encrypted vaults
    db.execute("""
        CREATE TABLE IF NOT EXISTS safes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- Encrypted DEK for the safe (encrypted with password or WebAuthn key)
            encrypted_dek BLOB NOT NULL,
            -- Unlock type: 'password' or 'webauthn'
            unlock_type TEXT NOT NULL CHECK(unlock_type IN ('password', 'webauthn')),
            -- For WebAuthn: which credential can unlock this safe
            credential_id BLOB,
            -- Salt for password-based key derivation
            salt BLOB,
            -- Recovery DEK encrypted with user's master DEK (optional, for recovery)
            recovery_encrypted_dek BLOB,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Index on user_id for safes
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_safes_user_id ON safes(user_id)
    """)
    
    # Migration: add safe_id to folders
    if "safe_id" not in folder_columns:
        db.execute("ALTER TABLE folders ADD COLUMN safe_id TEXT REFERENCES safes(id) ON DELETE SET NULL")
    
    # Migration: add safe_id to photos
    photo_columns = {row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()}
    if "safe_id" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN safe_id TEXT REFERENCES safes(id) ON DELETE SET NULL")
    
    # Migration: add safe_id to albums
    album_columns = {row[1] for row in db.execute("PRAGMA table_info(albums)").fetchall()}
    if "safe_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN safe_id TEXT REFERENCES safes(id) ON DELETE SET NULL")
    
    # Safe sessions - temporary unlocked safe keys (server-side session cache)
    db.execute("""
        CREATE TABLE IF NOT EXISTS safe_sessions (
            id TEXT PRIMARY KEY,
            safe_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            -- Encrypted DEK for this session (encrypted with session key)
            encrypted_dek BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_safe_sessions_safe_id ON safe_sessions(safe_id)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_safe_sessions_user_id ON safe_sessions(user_id)
    """)

    # Insert default categories if not exist
    default_categories = [
        ("Subject", "#3b82f6"),      # Blue - people, animals, objects
        ("Location", "#22c55e"),     # Green - places, settings
        ("Mood", "#f59e0b"),         # Amber - emotions, atmosphere
        ("Style", "#a855f7"),        # Purple - artistic style, technique
        ("Event", "#ef4444"),        # Red - occasions, activities
        ("Other", "#6b7280"),        # Gray - miscellaneous
    ]

    for name, color in default_categories:
        db.execute(
            "INSERT OR IGNORE INTO tag_categories (name, color) VALUES (?, ?)",
            (name, color)
        )

    # Insert some default preset tags
    default_presets = [
        # Subject
        ("person", "Subject"), ("people", "Subject"), ("portrait", "Subject"),
        ("animal", "Subject"), ("dog", "Subject"), ("cat", "Subject"),
        ("bird", "Subject"), ("flower", "Subject"), ("tree", "Subject"),
        ("car", "Subject"), ("building", "Subject"), ("food", "Subject"),
        # Location
        ("outdoor", "Location"), ("indoor", "Location"), ("city", "Location"),
        ("nature", "Location"), ("beach", "Location"), ("mountain", "Location"),
        ("forest", "Location"), ("park", "Location"), ("home", "Location"),
        ("street", "Location"), ("studio", "Location"),
        # Mood
        ("happy", "Mood"), ("calm", "Mood"), ("dramatic", "Mood"),
        ("romantic", "Mood"), ("mysterious", "Mood"), ("energetic", "Mood"),
        ("peaceful", "Mood"), ("melancholic", "Mood"),
        # Style
        ("black and white", "Style"), ("vintage", "Style"), ("minimalist", "Style"),
        ("abstract", "Style"), ("macro", "Style"), ("panorama", "Style"),
        ("long exposure", "Style"), ("bokeh", "Style"),
        # Event
        ("wedding", "Event"), ("birthday", "Event"), ("travel", "Event"),
        ("vacation", "Event"), ("concert", "Event"), ("sports", "Event"),
        ("family", "Event"), ("party", "Event"),
    ]

    for tag_name, category_name in default_presets:
        db.execute("""
            INSERT OR IGNORE INTO tag_presets (name, category_id)
            SELECT ?, id FROM tag_categories WHERE name = ?
        """, (tag_name, category_name))

    db.commit()


# === User Management Functions ===

def create_user(username: str, password: str, display_name: str) -> int:
    """Create a new user. Returns user ID.
    
    DEPRECATED: Use UserRepository.create() instead.
    """
    return _get_user_repo().create(username, password, display_name)


def get_user_by_username(username: str):
    """Get user by username
    
    DEPRECATED: Use UserRepository.get_by_username() instead.
    """
    return _get_user_repo().get_by_username(username)


def get_user_by_id(user_id: int):
    """Get user by ID
    
    DEPRECATED: Use UserRepository.get_by_id() instead.
    """
    return _get_user_repo().get_by_id(user_id)


def is_user_admin(user_id: int) -> bool:
    """Check if user is an admin.
    
    DEPRECATED: Use UserRepository.is_admin() instead.
    """
    return _get_user_repo().is_admin(user_id)


def set_user_admin(user_id: int, is_admin: bool) -> bool:
    """Set user admin status. Returns True if user exists.
    
    DEPRECATED: Use UserRepository.set_admin() instead.
    """
    return _get_user_repo().set_admin(user_id, is_admin)


def search_users(query: str, exclude_user_id: int = None, limit: int = 10) -> list:
    """Search users by username or display_name
    
    DEPRECATED: Use UserRepository.search() instead.
    """
    return _get_user_repo().search(query, exclude_user_id, limit)


def update_user_password(user_id: int, new_password: str):
    """Update user password
    
    DEPRECATED: Use UserRepository.update_password() instead.
    """
    _get_user_repo().update_password(user_id, new_password)


def update_user_display_name(user_id: int, display_name: str):
    """Update user display name
    
    DEPRECATED: Use UserRepository.update_display_name() instead.
    """
    _get_user_repo().update_display_name(user_id, display_name)


def delete_user(user_id: int):
    """Delete user and their sessions
    
    DEPRECATED: Use UserRepository.delete() instead.
    """
    _get_user_repo().delete(user_id)


def list_users():
    """List all users
    
    DEPRECATED: Use UserRepository.list_all() instead.
    """
    return _get_user_repo().list_all()


def authenticate_user(username: str, password: str):
    """Authenticate user. Returns user row if valid, None otherwise.
    
    DEPRECATED: Use UserRepository.authenticate() instead.
    """
    return _get_user_repo().authenticate(username, password)


# === Session Management ===

def create_session(user_id: int, expires_hours: int = 24 * 7) -> str:
    """Create a new session. Returns session ID.
    
    DEPRECATED: Use SessionRepository.create() instead.
    """
    return _get_session_repo().create(user_id, expires_hours)


def get_session(session_id: str):
    """Get session if valid and not expired.
    
    DEPRECATED: Use SessionRepository.get_valid() instead.
    """
    return _get_session_repo().get_valid(session_id)


def delete_session(session_id: str):
    """Delete session (logout).
    
    DEPRECATED: Use SessionRepository.delete() instead.
    """
    _get_session_repo().delete(session_id)


def cleanup_expired_sessions():
    """Remove expired sessions.
    
    DEPRECATED: Use SessionRepository.cleanup_expired() instead.
    """
    _get_session_repo().cleanup_expired()


# === Folder Management ===

def create_folder(name: str, user_id: int, parent_id: str = None) -> str:
    """Create a new folder. Returns folder ID.
    
    DEPRECATED: Use FolderRepository.create() instead.
    """
    return _get_folder_repo().create(name, user_id, parent_id)


def get_folder(folder_id: str):
    """Get folder by ID.
    
    DEPRECATED: Use FolderRepository.get_by_id() instead.
    """
    return _get_folder_repo().get_by_id(folder_id)


def update_folder(folder_id: str, name: str = None):
    """Update folder name.
    
    DEPRECATED: Use FolderRepository.update() instead.
    """
    _get_folder_repo().update(folder_id, name)


def delete_folder(folder_id: str):
    """Delete folder and all its contents.
    
    DEPRECATED: Use FolderRepository.delete() instead.
    """
    return _get_folder_repo().delete(folder_id)


def get_user_folders(user_id: int) -> list:
    """Get all folders owned by user.
    
    DEPRECATED: Use FolderRepository.list_by_user() instead.
    """
    return _get_folder_repo().list_by_user(user_id)


def get_folder_tree(user_id: int) -> list:
    """Get folder tree for sidebar (user's folders + folders with permissions + safes)"""
    db = get_db()
    
    # Cleanup expired safe sessions first
    cleanup_expired_safe_sessions()
    
    # Get unlocked safes for this user
    unlocked_safes = get_user_unlocked_safes(user_id)
    
    folders = db.execute("""
        SELECT f.*, u.display_name as owner_name,
               (
                   SELECT COUNT(*) FROM photos p
                   WHERE p.folder_id IN (
                       WITH RECURSIVE subfolder_tree AS (
                           SELECT id FROM folders WHERE id = f.id
                           UNION ALL
                           SELECT child.id FROM folders child
                           JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                       )
                       SELECT id FROM subfolder_tree
                   )
               ) as photo_count,
               CASE
                   WHEN f.user_id = ? THEN 'owner'
                   ELSE (SELECT permission FROM folder_permissions WHERE folder_id = f.id AND user_id = ?)
               END as permission,
               -- For owned folders: check sharing status
               CASE
                   WHEN f.user_id != ? THEN NULL
                   WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'editor') THEN 'has_editors'
                   WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id) THEN 'has_viewers'
                   ELSE 'private'
               END as share_status,
               -- Safe info
               f.safe_id,
               s.name as safe_name,
               s.unlock_type as safe_unlock_type,
               CASE WHEN f.safe_id IN ({}) THEN 1 ELSE 0 END as safe_is_unlocked
        FROM folders f
        JOIN users u ON f.user_id = u.id
        LEFT JOIN safes s ON f.safe_id = s.id
        WHERE (f.user_id = ? OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?))
          -- Only show folders in safes if safe is unlocked
          AND (f.safe_id IS NULL OR f.safe_id IN (SELECT id FROM safes WHERE user_id = ?))
        ORDER BY 
            CASE WHEN f.safe_id IS NULL THEN 0 ELSE 1 END,
            COALESCE(s.name, ''),
            f.name
    """.format(','.join(['?'] * len(unlocked_safes)) if unlocked_safes else 'NULL'),
    (user_id, user_id, user_id) + tuple(unlocked_safes) + (user_id, user_id, user_id)).fetchall()
    
    return [dict(f) for f in folders]


def get_folder_children(folder_id: str) -> list:
    """Get direct child folders.
    
    DEPRECATED: Use FolderRepository.get_children() instead.
    """
    return _get_folder_repo().get_children(folder_id)


def get_folder_breadcrumbs(folder_id: str) -> list:
    """Get breadcrumb path from root to folder.
    
    DEPRECATED: Use FolderRepository.get_breadcrumbs() instead.
    """
    return _get_folder_repo().get_breadcrumbs(folder_id)


def create_default_folder(user_id: int) -> str:
    """Create default folder for user and set it as default"""
    db = get_db()
    folder_id = create_folder("My Gallery", user_id, None)

    # Check if user has settings row (avoid INSERT OR REPLACE which wipes encryption keys)
    existing = db.execute(
        "SELECT user_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        db.execute(
            "UPDATE user_settings SET default_folder_id = ? WHERE user_id = ?",
            (folder_id, user_id)
        )
    else:
        db.execute(
            "INSERT INTO user_settings (user_id, default_folder_id) VALUES (?, ?)",
            (user_id, folder_id)
        )

    db.commit()
    return folder_id


def get_user_default_folder(user_id: int) -> str:
    """Get user's default folder ID, create if doesn't exist"""
    db = get_db()
    settings = db.execute(
        "SELECT default_folder_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if settings and settings["default_folder_id"]:
        # Verify folder still exists and user has access
        folder = get_folder(settings["default_folder_id"])
        if folder and can_access_folder(settings["default_folder_id"], user_id):
            return settings["default_folder_id"]

    # Create default folder if missing
    return create_default_folder(user_id)


def set_user_default_folder(user_id: int, folder_id: str) -> bool:
    """Set user's default folder. Returns True if successful."""
    db = get_db()

    # Verify folder exists and user has access
    folder = get_folder(folder_id)
    if not folder:
        return False

    if not can_access_folder(folder_id, user_id):
        return False

    # Check if user has settings row (avoid INSERT OR REPLACE which wipes encryption keys)
    existing = db.execute(
        "SELECT user_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        db.execute(
            "UPDATE user_settings SET default_folder_id = ? WHERE user_id = ?",
            (folder_id, user_id)
        )
    else:
        db.execute(
            "INSERT INTO user_settings (user_id, default_folder_id) VALUES (?, ?)",
            (user_id, folder_id)
        )

    db.commit()
    return True


def get_collapsed_folders(user_id: int) -> list[str]:
    """Get list of collapsed folder IDs for a user."""
    import json
    db = get_db()
    settings = db.execute(
        "SELECT collapsed_folders FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if settings and settings["collapsed_folders"]:
        try:
            return json.loads(settings["collapsed_folders"])
        except json.JSONDecodeError:
            return []
    return []


def set_collapsed_folders(user_id: int, folder_ids: list[str]) -> bool:
    """Set list of collapsed folder IDs for a user."""
    import json
    db = get_db()

    collapsed_json = json.dumps(folder_ids)

    # Check if user has settings row
    existing = db.execute(
        "SELECT user_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        db.execute(
            "UPDATE user_settings SET collapsed_folders = ? WHERE user_id = ?",
            (collapsed_json, user_id)
        )
    else:
        db.execute(
            "INSERT INTO user_settings (user_id, collapsed_folders) VALUES (?, ?)",
            (user_id, collapsed_json)
        )

    db.commit()
    return True


def toggle_folder_collapsed(user_id: int, folder_id: str) -> bool:
    """Toggle folder collapsed state. Returns new collapsed state."""
    collapsed = get_collapsed_folders(user_id)

    if folder_id in collapsed:
        collapsed.remove(folder_id)
        is_collapsed = False
    else:
        collapsed.append(folder_id)
        is_collapsed = True

    set_collapsed_folders(user_id, collapsed)
    return is_collapsed


# === Folder Permissions ===

def add_folder_permission(folder_id: str, user_id: int, permission: str, granted_by: int) -> bool:
    """Add or update permission for a user on a folder.
    
    DEPRECATED: Use PermissionRepository.grant() instead.
    """
    try:
        return _get_permission_repo().grant(folder_id, user_id, permission, granted_by)
    except ValueError:
        return False


def remove_folder_permission(folder_id: str, user_id: int) -> bool:
    """Remove permission for a user on a folder.
    
    DEPRECATED: Use PermissionRepository.revoke() instead.
    """
    return _get_permission_repo().revoke(folder_id, user_id)


def update_folder_permission(folder_id: str, user_id: int, permission: str) -> bool:
    """Update permission level for a user on a folder.
    
    DEPRECATED: Use PermissionRepository.update_permission() instead.
    """
    try:
        return _get_permission_repo().update_permission(folder_id, user_id, permission)
    except ValueError:
        return False


# === User Folder Preferences ===

def get_folder_sort_preference(user_id: int, folder_id: str) -> str:
    """Get user's sort preference for a folder. Returns 'uploaded' as default."""
    db = get_db()
    result = db.execute(
        "SELECT sort_by FROM user_folder_preferences WHERE user_id = ? AND folder_id = ?",
        (user_id, folder_id)
    ).fetchone()
    return result["sort_by"] if result else "uploaded"


def set_folder_sort_preference(user_id: int, folder_id: str, sort_by: str) -> bool:
    """Set user's sort preference for a folder."""
    if sort_by not in ("uploaded", "taken"):
        return False

    db = get_db()
    db.execute("""
        INSERT INTO user_folder_preferences (user_id, folder_id, sort_by)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, folder_id) DO UPDATE SET sort_by = excluded.sort_by
    """, (user_id, folder_id, sort_by))
    db.commit()
    return True


def get_folder_permissions(folder_id: str) -> list:
    """Get all permissions for a folder with user info.
    
    DEPRECATED: Use PermissionRepository.list_permissions() instead.
    """
    return _get_permission_repo().list_permissions(folder_id)


def get_user_permission(folder_id: str, user_id: int) -> str | None:
    """Get user's permission level for a folder: 'owner', 'editor', 'viewer', or None.
    
    DEPRECATED: Use PermissionRepository.get_permission() instead.
    """
    return _get_permission_repo().get_permission(folder_id, user_id)


def can_view_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can view folder (owner, viewer, or editor).
    
    DEPRECATED: Use PermissionRepository.can_view() instead.
    """
    return _get_permission_repo().can_view(folder_id, user_id)


def can_edit_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can edit folder content (owner or editor).
    
    DEPRECATED: Use PermissionRepository.can_edit() instead.
    """
    return _get_permission_repo().can_edit(folder_id, user_id)


# === Access Control ===

def can_access_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can access folder (view permission).
    
    DEPRECATED: Use PermissionRepository.can_view() instead.
    """
    return _get_permission_repo().can_view(folder_id, user_id)


def can_access_photo(photo_id: str, user_id: int) -> bool:
    """Check if user can access photo"""
    db = get_db()
    photo = db.execute(
        "SELECT folder_id, user_id, album_id FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    # Owner always has access
    if photo["user_id"] == user_id:
        return True

    # Check folder access if photo is in a folder
    if photo["folder_id"]:
        return can_access_folder(photo["folder_id"], user_id)

    # Check album's folder if photo is in album
    if photo["album_id"]:
        album = db.execute("SELECT folder_id, user_id FROM albums WHERE id = ?", (photo["album_id"],)).fetchone()
        if album:
            if album["user_id"] == user_id:
                return True
            if album["folder_id"]:
                return can_access_folder(album["folder_id"], user_id)

    # Legacy photos without folder/user - accessible to all authenticated users
    if photo["folder_id"] is None and photo["user_id"] is None:
        return True

    return False


def can_access_album(album_id: str, user_id: int) -> bool:
    """Check if user can access album"""
    db = get_db()
    album = db.execute(
        "SELECT folder_id, user_id FROM albums WHERE id = ?",
        (album_id,)
    ).fetchone()

    if not album:
        return False

    # Owner always has access
    if album["user_id"] == user_id:
        return True

    # Check folder access
    if album["folder_id"]:
        return can_access_folder(album["folder_id"], user_id)

    # Legacy albums without folder/user - accessible to all
    if album["folder_id"] is None and album["user_id"] is None:
        return True

    return False


def can_delete_photo(photo_id: str, user_id: int) -> bool:
    """Check if user can delete photo.

    Rules:
    - Photo owner can always delete
    - Folder owner can delete any photo in their folder
    - Editor can only delete photos they uploaded
    - Viewer cannot delete
    """
    db = get_db()
    photo = db.execute(
        "SELECT folder_id, user_id, album_id FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    # Photo owner can always delete
    if photo["user_id"] == user_id:
        return True

    # Check folder permissions
    folder_id = photo["folder_id"]

    # If photo is in album, get folder from album
    if not folder_id and photo["album_id"]:
        album = db.execute("SELECT folder_id, user_id FROM albums WHERE id = ?", (photo["album_id"],)).fetchone()
        if album:
            # Album owner can delete photos in their album
            if album["user_id"] == user_id:
                return True
            folder_id = album["folder_id"]

    if folder_id:
        # Get folder to check ownership
        folder = db.execute("SELECT user_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if folder and folder["user_id"] == user_id:
            # Folder owner can delete anything
            return True

        # Editors cannot delete other people's photos
        # (they can only delete their own, which is checked above)
        return False

    return False


def can_delete_album(album_id: str, user_id: int) -> bool:
    """Check if user can delete album.

    Rules:
    - Album owner can always delete
    - Folder owner can delete any album in their folder
    - Editor can only delete albums they created
    - Viewer cannot delete
    """
    db = get_db()
    album = db.execute(
        "SELECT folder_id, user_id FROM albums WHERE id = ?",
        (album_id,)
    ).fetchone()

    if not album:
        return False

    # Album owner can always delete
    if album["user_id"] == user_id:
        return True

    # Check folder ownership
    if album["folder_id"]:
        folder = db.execute("SELECT user_id FROM folders WHERE id = ?", (album["folder_id"],)).fetchone()
        if folder and folder["user_id"] == user_id:
            # Folder owner can delete anything
            return True

    return False


def get_folder_contents(folder_id: str, user_id: int) -> dict:
    """Get contents of a folder (subfolders, albums, photos)"""
    db = get_db()

    # Get subfolders (own + folders with permission)
    subfolders = db.execute("""
        SELECT * FROM folders
        WHERE parent_id = ? AND (
            user_id = ?
            OR id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
        )
        ORDER BY name
    """, (folder_id, user_id, user_id)).fetchall()

    # Get albums in this folder
    albums = db.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
               (SELECT id FROM photos WHERE album_id = a.id ORDER BY position LIMIT 1) as cover_photo_id
        FROM albums a
        WHERE a.folder_id = ?
        ORDER BY a.created_at DESC
    """, (folder_id,)).fetchall()

    # Get standalone photos in this folder
    photos = db.execute("""
        SELECT * FROM photos
        WHERE folder_id = ? AND album_id IS NULL
        ORDER BY uploaded_at DESC
    """, (folder_id,)).fetchall()

    return {
        "subfolders": [dict(f) for f in subfolders],
        "albums": [dict(a) for a in albums],
        "photos": [dict(p) for p in photos]
    }


# === Album Management ===

def can_edit_album(album_id: str, user_id: int) -> bool:
    """Check if user can edit album (owner or folder editor).

    Rules:
    - Album owner can always edit
    - Folder owner can edit any album in their folder
    - Folder editor can edit albums they created
    """
    db = get_db()
    album = db.execute(
        "SELECT folder_id, user_id FROM albums WHERE id = ?",
        (album_id,)
    ).fetchone()

    if not album:
        return False

    # Album owner can always edit
    if album["user_id"] == user_id:
        return True

    # Check folder permissions
    if album["folder_id"]:
        folder = db.execute("SELECT user_id FROM folders WHERE id = ?", (album["folder_id"],)).fetchone()
        if folder and folder["user_id"] == user_id:
            # Folder owner can edit anything
            return True

    return False


def get_album(album_id: str) -> dict | None:
    """Get album by ID with cover photo info"""
    db = get_db()
    album = db.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
               COALESCE(a.cover_photo_id,
                   (SELECT id FROM photos WHERE album_id = a.id ORDER BY position, id LIMIT 1)
               ) as effective_cover_photo_id
        FROM albums a
        WHERE a.id = ?
    """, (album_id,)).fetchone()

    return dict(album) if album else None


def set_album_cover(album_id: str, photo_id: str | None) -> bool:
    """Set cover photo for album. Pass None to reset to default (first photo)."""
    db = get_db()

    # Verify photo belongs to album if setting a cover
    if photo_id:
        photo = db.execute(
            "SELECT id FROM photos WHERE id = ? AND album_id = ?",
            (photo_id, album_id)
        ).fetchone()
        if not photo:
            return False

    db.execute("UPDATE albums SET cover_photo_id = ? WHERE id = ?", (photo_id, album_id))
    db.commit()
    return True


def add_photos_to_album(album_id: str, photo_ids: list[str]) -> int:
    """Add photos to album. Returns count of photos added."""
    db = get_db()

    # Get album info
    album = db.execute("SELECT folder_id FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album:
        return 0

    # Get current max position in album
    max_pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) as max_pos FROM photos WHERE album_id = ?",
        (album_id,)
    ).fetchone()["max_pos"]

    added = 0
    for photo_id in photo_ids:
        # Verify photo exists and is in same folder and not already in an album
        photo = db.execute(
            "SELECT id FROM photos WHERE id = ? AND folder_id = ? AND album_id IS NULL",
            (photo_id, album["folder_id"])
        ).fetchone()

        if photo:
            max_pos += 1
            db.execute(
                "UPDATE photos SET album_id = ?, position = ? WHERE id = ?",
                (album_id, max_pos, photo_id)
            )
            added += 1

    db.commit()
    return added


def remove_photos_from_album(album_id: str, photo_ids: list[str]) -> int:
    """Remove photos from album. Photos stay in folder. Returns count removed."""
    db = get_db()

    # Get album to check cover photo
    album = db.execute("SELECT cover_photo_id FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album:
        return 0

    removed = 0
    reset_cover = False

    for photo_id in photo_ids:
        result = db.execute(
            "UPDATE photos SET album_id = NULL, position = 0 WHERE id = ? AND album_id = ?",
            (photo_id, album_id)
        )
        if result.rowcount > 0:
            removed += 1
            # Check if we removed the cover photo
            if album["cover_photo_id"] == photo_id:
                reset_cover = True

    # Reset cover photo if it was removed
    if reset_cover:
        db.execute("UPDATE albums SET cover_photo_id = NULL WHERE id = ?", (album_id,))

    db.commit()
    return removed


def reorder_album_photos(album_id: str, photo_ids: list[str]) -> bool:
    """Reorder photos in album. photo_ids should be in desired order."""
    db = get_db()

    # Verify all photos belong to album
    placeholders = ",".join("?" * len(photo_ids))
    existing = db.execute(f"""
        SELECT id FROM photos WHERE album_id = ? AND id IN ({placeholders})
    """, [album_id] + photo_ids).fetchall()

    if len(existing) != len(photo_ids):
        return False

    # Update positions
    for position, photo_id in enumerate(photo_ids):
        db.execute(
            "UPDATE photos SET position = ? WHERE id = ? AND album_id = ?",
            (position, photo_id, album_id)
        )

    db.commit()
    return True


def get_available_photos_for_album(album_id: str) -> list:
    """Get photos from same folder that can be added to album (not in any album)."""
    db = get_db()

    # Get album's folder
    album = db.execute("SELECT folder_id FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album or not album["folder_id"]:
        return []

    photos = db.execute("""
        SELECT id, filename, original_name, media_type, taken_at, uploaded_at
        FROM photos
        WHERE folder_id = ? AND album_id IS NULL
        ORDER BY uploaded_at DESC
    """, (album["folder_id"],)).fetchall()

    return [dict(p) for p in photos]


def get_album_photos(album_id: str) -> list:
    """Get all photos in album ordered by position."""
    db = get_db()
    photos = db.execute("""
        SELECT id, filename, original_name, media_type, position, taken_at, uploaded_at
        FROM photos
        WHERE album_id = ?
        ORDER BY position, id
    """, (album_id,)).fetchall()

    return [dict(p) for p in photos]


# === Move Operations ===

def move_photo_to_folder(photo_id: str, target_folder_id: str) -> bool:
    """Move a standalone photo to another folder.

    Only works for photos not in albums (album_id IS NULL).
    Returns True if successful.
    """
    db = get_db()

    # Verify photo exists and is not in an album
    photo = db.execute(
        "SELECT id, album_id FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()

    if not photo:
        return False

    if photo["album_id"]:
        # Photo is in an album, cannot move independently
        return False

    # Update folder_id
    db.execute(
        "UPDATE photos SET folder_id = ? WHERE id = ?",
        (target_folder_id, photo_id)
    )
    db.commit()
    return True


def move_album_to_folder(album_id: str, target_folder_id: str) -> bool:
    """Move an album and all its photos to another folder.

    Returns True if successful.
    """
    db = get_db()

    # Verify album exists
    album = db.execute(
        "SELECT id FROM albums WHERE id = ?",
        (album_id,)
    ).fetchone()

    if not album:
        return False

    # Update album folder_id
    db.execute(
        "UPDATE albums SET folder_id = ? WHERE id = ?",
        (target_folder_id, album_id)
    )

    # Update all photos in the album
    db.execute(
        "UPDATE photos SET folder_id = ? WHERE album_id = ?",
        (target_folder_id, album_id)
    )

    db.commit()
    return True


def move_photos_to_folder(photo_ids: list[str], target_folder_id: str) -> int:
    """Move multiple standalone photos to another folder.

    Only moves photos not in albums.
    Returns count of photos moved.
    """
    db = get_db()
    moved = 0

    for photo_id in photo_ids:
        # Verify photo exists and is not in an album
        photo = db.execute(
            "SELECT id, album_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()

        if photo and not photo["album_id"]:
            db.execute(
                "UPDATE photos SET folder_id = ? WHERE id = ?",
                (target_folder_id, photo_id)
            )
            moved += 1

    db.commit()
    return moved


def move_albums_to_folder(album_ids: list[str], target_folder_id: str) -> int:
    """Move multiple albums and their photos to another folder.

    Returns count of albums moved.
    """
    db = get_db()
    moved = 0

    for album_id in album_ids:
        # Verify album exists
        album = db.execute(
            "SELECT id FROM albums WHERE id = ?",
            (album_id,)
        ).fetchone()

        if album:
            # Update album folder_id
            db.execute(
                "UPDATE albums SET folder_id = ? WHERE id = ?",
                (target_folder_id, album_id)
            )

            # Update all photos in the album
            db.execute(
                "UPDATE photos SET folder_id = ? WHERE album_id = ?",
                (target_folder_id, album_id)
            )
            moved += 1

    db.commit()
    return moved


# === Encryption Key Management ===

def get_user_encryption_keys(user_id: int) -> dict | None:
    """Get encryption metadata for user."""
    db = get_db()
    result = db.execute("""
        SELECT encrypted_dek, dek_salt, encryption_version
        FROM user_settings WHERE user_id = ?
    """, (user_id,)).fetchone()

    if result and result["encrypted_dek"]:
        return {
            "encrypted_dek": result["encrypted_dek"],
            "dek_salt": result["dek_salt"],
            "encryption_version": result["encryption_version"]
        }
    return None


def set_user_encryption_keys(user_id: int, encrypted_dek: bytes, dek_salt: bytes) -> bool:
    """Store encrypted DEK for user."""
    db = get_db()

    # Check if user has settings row
    existing = db.execute(
        "SELECT user_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        db.execute("""
            UPDATE user_settings
            SET encrypted_dek = ?, dek_salt = ?, encryption_version = 1
            WHERE user_id = ?
        """, (encrypted_dek, dek_salt, user_id))
    else:
        db.execute("""
            INSERT INTO user_settings (user_id, encrypted_dek, dek_salt, encryption_version)
            VALUES (?, ?, ?, 1)
        """, (user_id, encrypted_dek, dek_salt))

    db.commit()
    return True


def set_recovery_encrypted_dek(user_id: int, recovery_encrypted_dek: bytes) -> bool:
    """Store DEK encrypted with recovery key."""
    db = get_db()

    existing = db.execute(
        "SELECT 1 FROM user_settings WHERE user_id = ?", (user_id,)
    ).fetchone()

    if existing:
        db.execute("""
            UPDATE user_settings SET recovery_encrypted_dek = ? WHERE user_id = ?
        """, (recovery_encrypted_dek, user_id))
    else:
        db.execute("""
            INSERT INTO user_settings (user_id, recovery_encrypted_dek)
            VALUES (?, ?)
        """, (user_id, recovery_encrypted_dek))

    db.commit()
    return True


def get_recovery_encrypted_dek(user_id: int) -> bytes | None:
    """Get DEK encrypted with recovery key."""
    db = get_db()
    result = db.execute("""
        SELECT recovery_encrypted_dek FROM user_settings WHERE user_id = ?
    """, (user_id,)).fetchone()

    if result and result["recovery_encrypted_dek"]:
        return result["recovery_encrypted_dek"]
    return None


def clear_recovery_key(user_id: int) -> bool:
    """Remove recovery key (after it's been used or revoked)."""
    db = get_db()
    db.execute("""
        UPDATE user_settings SET recovery_encrypted_dek = NULL WHERE user_id = ?
    """, (user_id,))
    db.commit()
    return True


def mark_photo_encrypted(photo_id: str) -> bool:
    """Mark photo as encrypted.
    
    DEPRECATED: Use PhotoRepository.mark_encrypted() instead.
    """
    return _get_photo_repo().mark_encrypted(photo_id, encrypted=True)


def mark_photo_decrypted(photo_id: str) -> bool:
    """Mark photo as not encrypted (for migration rollback).
    
    DEPRECATED: Use PhotoRepository.mark_encrypted() instead.
    """
    return _get_photo_repo().mark_encrypted(photo_id, encrypted=False)


def get_user_unencrypted_photos(user_id: int) -> list:
    """Get all unencrypted photos for a user (for migration).
    
    DEPRECATED: Use PhotoRepository with custom query instead.
    """
    # Get all user photos and filter
    all_photos = _get_photo_repo().get_by_folder(folder_id=None, sort_by="uploaded")
    return [p for p in all_photos if p.get("user_id") == user_id and not p.get("is_encrypted")]


def get_photo_by_id(photo_id: str) -> dict | None:
    """Get photo by ID with encryption status.
    
    DEPRECATED: Use PhotoRepository.get_by_id() instead.
    """
    return _get_photo_repo().get_by_id(photo_id)


def get_photo_owner_id(photo_id: str) -> int | None:
    """Get owner user_id for a photo (for shared folder decryption).
    
    DEPRECATED: Use PhotoRepository.get_by_id() instead.
    """
    photo = _get_photo_repo().get_by_id(photo_id)
    return photo.get("user_id") if photo else None


def update_photo_thumbnail_dimensions(photo_id: str, width: int, height: int) -> bool:
    """Update thumbnail dimensions for a photo.
    
    DEPRECATED: Use PhotoRepository.update_thumbnail_dimensions() instead.
    """
    return _get_photo_repo().update_thumbnail_dimensions(photo_id, width, height)


# =============================================================================
# WebAuthn Credentials
# =============================================================================

def add_webauthn_credential(
    user_id: int,
    credential_id: bytes,
    public_key: bytes,
    name: str,
    encrypted_dek: bytes | None = None
) -> int:
    """Add a new WebAuthn credential for a user."""
    db = get_db()
    cursor = db.execute("""
        INSERT INTO webauthn_credentials (user_id, credential_id, public_key, name, encrypted_dek)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, credential_id, public_key, name, encrypted_dek))
    db.commit()
    return cursor.lastrowid


def get_webauthn_credentials(user_id: int) -> list[dict]:
    """Get all WebAuthn credentials for a user."""
    db = get_db()
    credentials = db.execute("""
        SELECT id, credential_id, public_key, sign_count, name, created_at, encrypted_dek
        FROM webauthn_credentials WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    return [dict(c) for c in credentials]


def get_webauthn_credential_by_id(credential_id: bytes) -> dict | None:
    """Get a WebAuthn credential by its credential_id."""
    db = get_db()
    credential = db.execute("""
        SELECT wc.*, u.username, u.id as user_id
        FROM webauthn_credentials wc
        JOIN users u ON wc.user_id = u.id
        WHERE wc.credential_id = ?
    """, (credential_id,)).fetchone()
    return dict(credential) if credential else None


def get_user_credential_ids(user_id: int) -> list[bytes]:
    """Get all credential IDs for a user (for WebAuthn allowCredentials)."""
    db = get_db()
    credentials = db.execute("""
        SELECT credential_id FROM webauthn_credentials WHERE user_id = ?
    """, (user_id,)).fetchall()
    return [c["credential_id"] for c in credentials]


def update_webauthn_sign_count(credential_id: bytes, new_sign_count: int) -> bool:
    """Update sign count after successful authentication."""
    db = get_db()
    db.execute("""
        UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?
    """, (new_sign_count, credential_id))
    db.commit()
    return True


def delete_webauthn_credential(credential_db_id: int, user_id: int) -> bool:
    """Delete a WebAuthn credential by its database ID."""
    db = get_db()
    result = db.execute("""
        DELETE FROM webauthn_credentials WHERE id = ? AND user_id = ?
    """, (credential_db_id, user_id))
    db.commit()
    return result.rowcount > 0


def rename_webauthn_credential(credential_db_id: int, user_id: int, new_name: str) -> bool:
    """Rename a WebAuthn credential."""
    db = get_db()
    result = db.execute("""
        UPDATE webauthn_credentials SET name = ? WHERE id = ? AND user_id = ?
    """, (new_name, credential_db_id, user_id))
    db.commit()
    return result.rowcount > 0


def user_has_webauthn_credentials(user_id: int) -> bool:
    """Check if user has any registered WebAuthn credentials."""
    db = get_db()
    result = db.execute("""
        SELECT 1 FROM webauthn_credentials WHERE user_id = ? LIMIT 1
    """, (user_id,)).fetchone()
    return result is not None


def get_all_credential_ids_for_username(username: str) -> list[bytes]:
    """Get all credential IDs for a username (for passwordless login)."""
    db = get_db()
    credentials = db.execute("""
        SELECT wc.credential_id
        FROM webauthn_credentials wc
        JOIN users u ON wc.user_id = u.id
        WHERE u.username = ?
    """, (username,)).fetchall()
    return [c["credential_id"] for c in credentials]


# =============================================================================
# SAFES - Encrypted vaults with separate authentication
# =============================================================================

def create_safe(
    name: str,
    user_id: int,
    encrypted_dek: bytes,
    unlock_type: str,
    credential_id: bytes = None,
    salt: bytes = None,
    recovery_encrypted_dek: bytes = None
) -> str:
    """Create a new safe. Returns safe ID."""
    import uuid
    db = get_db()
    safe_id = str(uuid.uuid4())
    
    db.execute("""
        INSERT INTO safes (id, name, user_id, encrypted_dek, unlock_type, 
                          credential_id, salt, recovery_encrypted_dek)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (safe_id, name.strip(), user_id, encrypted_dek, unlock_type,
          credential_id, salt, recovery_encrypted_dek))
    db.commit()
    return safe_id


def get_safe(safe_id: str) -> dict | None:
    """Get safe by ID."""
    db = get_db()
    safe = db.execute("SELECT * FROM safes WHERE id = ?", (safe_id,)).fetchone()
    return dict(safe) if safe else None


def get_user_safes(user_id: int) -> list[dict]:
    """Get all safes for a user."""
    db = get_db()
    safes = db.execute("""
        SELECT s.*, 
               (SELECT COUNT(*) FROM folders WHERE safe_id = s.id) as folder_count,
               (SELECT COUNT(*) FROM photos WHERE safe_id = s.id) as photo_count
        FROM safes s
        WHERE s.user_id = ?
        ORDER BY s.created_at DESC
    """, (user_id,)).fetchall()
    return [dict(s) for s in safes]


def update_safe(safe_id: str, name: str = None) -> bool:
    """Update safe name."""
    db = get_db()
    if name is not None:
        db.execute("UPDATE safes SET name = ? WHERE id = ?", (name.strip(), safe_id))
        db.commit()
        return True
    return False


def delete_safe(safe_id: str) -> bool:
    """Delete a safe and all its contents (folders, photos)."""
    db = get_db()
    
    # Get all folders in this safe
    folders = db.execute("SELECT id FROM folders WHERE safe_id = ?", (safe_id,)).fetchall()
    
    # Delete photos in safe
    db.execute("DELETE FROM photos WHERE safe_id = ?", (safe_id,))
    
    # Delete albums in safe
    db.execute("DELETE FROM albums WHERE safe_id = ?", (safe_id,))
    
    # Delete folders in safe
    db.execute("DELETE FROM folders WHERE safe_id = ?", (safe_id,))
    
    # Delete safe sessions
    db.execute("DELETE FROM safe_sessions WHERE safe_id = ?", (safe_id,))
    
    # Delete safe
    db.execute("DELETE FROM safes WHERE id = ?", (safe_id,))
    db.commit()
    return True


def get_safe_by_folder_id(folder_id: str) -> dict | None:
    """Get safe that contains this folder (if any)."""
    db = get_db()
    safe = db.execute("""
        SELECT s.* FROM safes s
        JOIN folders f ON f.safe_id = s.id
        WHERE f.id = ?
    """, (folder_id,)).fetchone()
    return dict(safe) if safe else None


def is_folder_in_safe(folder_id: str) -> bool:
    """Check if folder is inside a safe."""
    db = get_db()
    result = db.execute("""
        SELECT 1 FROM folders WHERE id = ? AND safe_id IS NOT NULL
    """, (folder_id,)).fetchone()
    return result is not None


def get_folder_safe_id(folder_id: str) -> str | None:
    """Get safe_id for a folder (if any)."""
    db = get_db()
    result = db.execute(
        "SELECT safe_id FROM folders WHERE id = ?", (folder_id,)
    ).fetchone()
    return result["safe_id"] if result else None


# =============================================================================
# Safe Sessions - Temporary unlocked safe keys
# =============================================================================

def create_safe_session(safe_id: str, user_id: int, encrypted_dek: bytes, expires_hours: int = 24) -> str:
    """Create a safe session for unlocked safe access."""
    db = get_db()
    session_id = secrets.token_urlsafe(32)
    db.execute("""
        INSERT INTO safe_sessions (id, safe_id, user_id, encrypted_dek, expires_at)
        VALUES (?, ?, ?, ?, datetime('now', '+' || ? || ' hours'))
    """, (session_id, safe_id, user_id, encrypted_dek, expires_hours))
    db.commit()
    return session_id


def get_safe_session(session_id: str) -> dict | None:
    """Get valid safe session."""
    db = get_db()
    session = db.execute("""
        SELECT * FROM safe_sessions 
        WHERE id = ? AND expires_at > datetime('now')
    """, (session_id,)).fetchone()
    return dict(session) if session else None


def delete_safe_session(session_id: str) -> bool:
    """Delete a safe session (lock the safe)."""
    db = get_db()
    db.execute("DELETE FROM safe_sessions WHERE id = ?", (session_id,))
    db.commit()
    return True


def cleanup_expired_safe_sessions():
    """Remove expired safe sessions."""
    db = get_db()
    db.execute("DELETE FROM safe_sessions WHERE expires_at <= datetime('now')")
    db.commit()


def get_user_unlocked_safes(user_id: int) -> list[str]:
    """Get list of safe IDs that are currently unlocked for this user."""
    db = get_db()
    sessions = db.execute("""
        SELECT safe_id FROM safe_sessions 
        WHERE user_id = ? AND expires_at > datetime('now')
    """, (user_id,)).fetchall()
    return [s["safe_id"] for s in sessions]


def is_safe_unlocked_for_user(safe_id: str, user_id: int) -> bool:
    """Check if safe is currently unlocked for user."""
    db = get_db()
    result = db.execute("""
        SELECT 1 FROM safe_sessions 
        WHERE safe_id = ? AND user_id = ? AND expires_at > datetime('now')
    """, (safe_id, user_id)).fetchone()
    return result is not None


# =============================================================================
# Folder operations with safe support
# =============================================================================

def create_folder_in_safe(name: str, user_id: int, safe_id: str, parent_id: str = None) -> str:
    """Create a new folder inside a safe."""
    import uuid
    db = get_db()
    folder_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO folders (id, name, parent_id, user_id, safe_id) VALUES (?, ?, ?, ?, ?)",
        (folder_id, name.strip(), parent_id, user_id, safe_id)
    )
    db.commit()
    return folder_id


def get_safe_folders(safe_id: str, user_id: int) -> list[dict]:
    """Get all folders in a safe."""
    db = get_db()
    folders = db.execute("""
        SELECT f.*, 
               (SELECT COUNT(*) FROM photos p 
                WHERE p.folder_id IN (
                    WITH RECURSIVE folder_tree AS (
                        SELECT id FROM folders WHERE id = f.id
                        UNION ALL
                        SELECT child.id FROM folders child
                        JOIN folder_tree ON child.parent_id = folder_tree.id
                    )
                    SELECT id FROM folder_tree
                )
               ) as photo_count
        FROM folders f
        WHERE f.safe_id = ? AND f.user_id = ?
        ORDER BY f.name
    """, (safe_id, user_id)).fetchall()
    return [dict(f) for f in folders]


def move_folder_to_safe(folder_id: str, safe_id: str) -> bool:
    """Move a folder into a safe."""
    db = get_db()
    db.execute("UPDATE folders SET safe_id = ? WHERE id = ?", (safe_id, folder_id))
    # Also move all photos in this folder to the safe
    db.execute("UPDATE photos SET safe_id = ? WHERE folder_id = ?", (safe_id, folder_id))
    db.commit()
    return True


def get_safe_tree_for_user(user_id: int) -> list[dict]:
    """Get folder tree including safes for sidebar."""
    db = get_db()
    
    # Get regular folders (not in safes) + folders that are in unlocked safes
    folders = db.execute("""
        SELECT f.*, u.display_name as owner_name,
               CASE 
                   WHEN f.safe_id IS NULL THEN 'regular'
                   ELSE 'safe'
               END as folder_type,
               f.safe_id,
               s.name as safe_name,
               (
                   SELECT COUNT(*) FROM photos p
                   WHERE p.folder_id IN (
                       WITH RECURSIVE subfolder_tree AS (
                           SELECT id FROM folders WHERE id = f.id
                           UNION ALL
                           SELECT child.id FROM folders child
                           JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                       )
                       SELECT id FROM subfolder_tree
                   )
               ) as photo_count,
               CASE
                   WHEN f.user_id = ? THEN 'owner'
                   ELSE (SELECT permission FROM folder_permissions WHERE folder_id = f.id AND user_id = ?)
               END as permission,
               CASE
                   WHEN f.user_id != ? THEN NULL
                   WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'editor') THEN 'has_editors'
                   WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id) THEN 'has_viewers'
                   ELSE 'private'
               END as share_status
        FROM folders f
        JOIN users u ON f.user_id = u.id
        LEFT JOIN safes s ON f.safe_id = s.id
        WHERE f.user_id = ?
           OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
           OR f.safe_id IN (SELECT id FROM safes WHERE user_id = ?)
        ORDER BY 
            CASE WHEN f.safe_id IS NULL THEN 0 ELSE 1 END,
            COALESCE(s.name, ''),
            f.name
    """, (user_id, user_id, user_id, user_id, user_id, user_id)).fetchall()
    
    return [dict(f) for f in folders]
