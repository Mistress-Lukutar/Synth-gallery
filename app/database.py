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
# Migration: Polymorphic Items (Issue #24)
# =============================================================================
def _migrate_to_polymorphic_items(db):
    """Migrate photos to polymorphic items architecture.
    
    This migration:
    1. Creates items from existing photos
    2. Creates item_media from photo media data
    3. Migrates photos.album_id to album_items junction table
    4. Updates albums.cover_photo_id to cover_item_id
    """
    # Check if migration already done
    cursor = db.execute("SELECT COUNT(*) as count FROM items LIMIT 1")
    if cursor.fetchone()["count"] > 0:
        return  # Migration already done
    
    # Check if there are any photos to migrate
    cursor = db.execute("SELECT COUNT(*) as count FROM photos")
    photo_count = cursor.fetchone()["count"]
    if photo_count == 0:
        return  # Nothing to migrate
    
    print(f"[Migration] Starting polymorphic items migration for {photo_count} photos...")
    
    # Migrate photos to items + item_media
    db.execute("""
        INSERT INTO items (
            id, type, folder_id, safe_id, user_id, uploaded_at, 
            title, metadata, is_encrypted
        )
        SELECT 
            p.id,
            'media' as type,
            p.folder_id,
            p.safe_id,
            p.user_id,
            p.uploaded_at,
            p.original_name as title,
            NULL as metadata,
            p.is_encrypted
        FROM photos p
    """)
    
    # Migrate photo media details
    # Note: filename is not migrated - we use item_id as filename in extension-less storage
    db.execute("""
        INSERT INTO item_media (
            item_id, media_type, original_name, content_type,
            width, height, duration, thumb_width, thumb_height, taken_at
        )
        SELECT 
            p.id as item_id,
            CASE 
                WHEN p.media_type = 'video' THEN 'video'
                ELSE 'image'
            END as media_type,
            p.original_name,
            p.content_type,
            NULL as width,  -- Will be populated from metadata if available
            NULL as height,
            NULL as duration,
            p.thumb_width,
            p.thumb_height,
            p.taken_at
        FROM photos p
    """)
    
    # Migrate album memberships
    db.execute("""
        INSERT INTO album_items (album_id, item_id, position, added_at)
        SELECT 
            p.album_id,
            p.id as item_id,
            p.position,
            p.uploaded_at as added_at
        FROM photos p
        WHERE p.album_id IS NOT NULL
    """)
    
    # Update albums cover_photo_id to reference items
    # Note: We keep the column name for now, but it now references items.id
    
    print(f"[Migration] Migrated {photo_count} photos to polymorphic items")


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
            cover_item_id TEXT,
            safe_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (cover_item_id) REFERENCES items(id) ON DELETE SET NULL,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL
        )
    """)

    # Photos table
    db.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT,
            uploaded_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
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

    # =============================================================================
    # Tag System v2: Hierarchical Tags
    # =============================================================================
    
    # Migration: Drop old tag tables if they have v1 schema
    try:
        db.execute("SELECT path FROM tags LIMIT 1")
    except sqlite3.OperationalError:
        # Old schema - drop all tag tables and recreate
        db.execute("DROP TABLE IF EXISTS item_tags")
        db.execute("DROP TABLE IF EXISTS tags")
        db.execute("DROP TABLE IF EXISTS tag_presets")
        db.execute("DROP TABLE IF EXISTS tag_categories")
        print("[Migration] Dropped old tag tables for v2 upgrade")
    
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
    
    # Hierarchical tags (materialized path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            display_name TEXT,
            category_id INTEGER,
            parent_id INTEGER,
            path TEXT NOT NULL,
            level INTEGER DEFAULT 0,
            is_leaf INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(path),
            FOREIGN KEY (category_id) REFERENCES tag_categories(id),
            FOREIGN KEY (parent_id) REFERENCES tags(id)
        )
    """)
    
    # Item-tags relationship (many-to-many)
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            is_explicit INTEGER DEFAULT 1,  -- 1 = user added, 0 = auto-added ancestor
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, tag_id),
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)
    
    # Drop old tag tables if they exist (migration from v1)
    db.execute("DROP TABLE IF EXISTS tag_presets")
    # Note: old 'tags' table will be dropped after data migration if needed
    # For now we keep it but don't use it - Phase 5 migration handles this

    # =============================================================================
    # v1.0: Polymorphic Items Architecture (Issue #24)
    # =============================================================================
    
    # Items table - polymorphic base for all content types
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,  -- 'media', 'note', 'file', etc.
            folder_id TEXT,
            safe_id TEXT,
            user_id INTEGER,
            uploaded_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
            title TEXT,
            metadata TEXT,  -- JSON for type-specific data
            is_encrypted INTEGER DEFAULT 0,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (safe_id) REFERENCES safes(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    
    # Item media table - photo/video specific data
    # Note: filename = item_id in extension-less storage
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_media (
            item_id TEXT PRIMARY KEY,
            media_type TEXT NOT NULL,  -- 'image' or 'video'
            filename TEXT NOT NULL,  -- Same as item_id in extension-less storage
            original_name TEXT,
            content_type TEXT,
            width INTEGER,
            height INTEGER,
            duration INTEGER,  -- for video (seconds)
            thumb_width INTEGER,
            thumb_height INTEGER,
            taken_at TIMESTAMP,
            storage_mode TEXT DEFAULT 'standard',  -- 'standard' or 'legacy'
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    """)
    
    # Album items junction table - replaces photos.album_id
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
    
    # Update albums table - change cover_photo_id to cover_item_id
    # (Migration handled separately)
    
    # Indexes
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_type ON items(type)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_safe ON items(safe_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_album_items_album ON album_items(album_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_album_items_item ON album_items(item_id)")
    # Tag system v2 indexes
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_path ON tags(path)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id)")
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

    # Insert default tag categories (v2)
    _init_tag_categories_v2(db)
    
    # Insert default tag hierarchy (v2)
    _init_tag_hierarchy_v2(db)

    # Migration: Add content_type column for extension-less storage (Issue #22)
    try:
        db.execute("ALTER TABLE photos ADD COLUMN content_type TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # =============================================================================
    # Migration: Polymorphic Items Architecture (Issue #24)
    # =============================================================================
    _migrate_to_polymorphic_items(db)
    
    # Migration: Rename albums.cover_photo_id to cover_item_id
    try:
        db.execute("ALTER TABLE albums RENAME COLUMN cover_photo_id TO cover_item_id")
        print("[Migration] Renamed albums.cover_photo_id to cover_item_id")
    except sqlite3.OperationalError:
        pass  # Column already renamed or doesn't exist
    
    # Migration: Rename items.created_at to uploaded_at
    try:
        db.execute("ALTER TABLE items RENAME COLUMN created_at TO uploaded_at")
        print("[Migration] Renamed items.created_at to uploaded_at")
    except sqlite3.OperationalError:
        pass  # Column already renamed or doesn't exist

    # Migration: Add encrypted_dek column to sessions for DB-based DEK storage (Issue #18)
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN encrypted_dek BLOB")
        print("[Migration] Added sessions.encrypted_dek column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # =============================================================================
    # Create default admin user if no users exist (first run)
    # =============================================================================
    cursor = db.execute("SELECT COUNT(*) as count FROM users")
    if cursor.fetchone()["count"] == 0:
        import bcrypt
        
        default_username = "admin"
        default_password = "admin"
        
        # Hash password using bcrypt
        hashed = bcrypt.hashpw(default_password.encode('utf-8'), bcrypt.gensalt())
        
        db.execute(
            """INSERT INTO users 
               (username, password_hash, password_salt, display_name, is_admin) 
               VALUES (?, ?, ?, ?, ?)""",
            (default_username, hashed.decode('utf-8'), "", "Administrator", 1)
        )
        
        print("=" * 70)
        print("⚠️  FIRST RUN: Default admin account created")
        print("=" * 70)
        print(f"   Username: {default_username}")
        print(f"   Password: {default_password}")
        print("")
        print("   Please log in and create a new admin user immediately,")
        print("   then delete this temporary account for security.")
        print("=" * 70)

    db.commit()


def _init_tag_categories_v2(db):
    """Initialize tag categories for v2 hierarchical tag system."""
    default_categories = [
        (1, 'subject', 'Subject', '#3b82f6', 1),      # Blue
        (2, 'style', 'Style', '#8b5cf6', 2),          # Purple
        (3, 'environment', 'Environment', '#10b981', 3),  # Green
        (4, 'quality', 'Quality', '#f59e0b', 4),      # Orange
        (5, 'media_type', 'Media Type', '#6b7280', 5),    # Gray
    ]
    
    for id_, slug, name, color, order in default_categories:
        db.execute("""
            INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (id_, slug, name, color, order))


def _init_tag_hierarchy_v2(db):
    """Initialize default tag hierarchy for v2 system."""
    
    def create_tag(name, display_name, category_id, parent_id=None, path=None):
        """Helper to create a tag and return its ID."""
        level = 0 if parent_id is None else db.execute(
            "SELECT level FROM tags WHERE id = ?", (parent_id,)
        ).fetchone()[0] + 1
        
        if path is None:
            path = name
        
        try:
            cursor = db.execute("""
                INSERT INTO tags (name, display_name, category_id, parent_id, path, level, is_leaf)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, display_name, category_id, parent_id, path, level, 1))
            
            # Mark parent as non-leaf
            if parent_id:
                db.execute("UPDATE tags SET is_leaf = 0 WHERE id = ?", (parent_id,))
            
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Tag already exists
            row = db.execute("SELECT id FROM tags WHERE path = ?", (path,)).fetchone()
            return row[0] if row else None
    
    # Subject category (id=1)
    subject = create_tag('subject', 'Subject', 1, None, 'subject')
    
    # Animal branch
    animal = create_tag('animal', 'Animal', 1, subject, 'subject.animal')
    mammal = create_tag('mammal', 'Mammal', 1, animal, 'subject.animal.mammal')
    fox = create_tag('fox', 'Fox', 1, mammal, 'subject.animal.mammal.fox')
    create_tag('silver_fox', 'Silver Fox', 1, fox, 'subject.animal.mammal.fox.silver_fox')
    create_tag('red_fox', 'Red Fox', 1, fox, 'subject.animal.mammal.fox.red_fox')
    create_tag('arctic_fox', 'Arctic Fox', 1, fox, 'subject.animal.mammal.fox.arctic_fox')
    
    wolf = create_tag('wolf', 'Wolf', 1, mammal, 'subject.animal.mammal.wolf')
    create_tag('gray_wolf', 'Gray Wolf', 1, wolf, 'subject.animal.mammal.wolf.gray_wolf')
    create_tag('white_wolf', 'White Wolf', 1, wolf, 'subject.animal.mammal.wolf.white_wolf')
    
    create_tag('bear', 'Bear', 1, mammal, 'subject.animal.mammal.bear')
    create_tag('cat', 'Cat', 1, mammal, 'subject.animal.mammal.cat')
    create_tag('dog', 'Dog', 1, mammal, 'subject.animal.mammal.dog')
    
    bird = create_tag('bird', 'Bird', 1, animal, 'subject.animal.bird')
    create_tag('eagle', 'Eagle', 1, bird, 'subject.animal.bird.eagle')
    create_tag('owl', 'Owl', 1, bird, 'subject.animal.bird.owl')
    create_tag('raven', 'Raven', 1, bird, 'subject.animal.bird.raven')
    
    reptile = create_tag('reptile', 'Reptile', 1, animal, 'subject.animal.reptile')
    create_tag('snake', 'Snake', 1, reptile, 'subject.animal.reptile.snake')
    create_tag('lizard', 'Lizard', 1, reptile, 'subject.animal.reptile.lizard')
    
    # Person branch
    person = create_tag('person', 'Person', 1, subject, 'subject.person')
    create_tag('woman', 'Woman', 1, person, 'subject.person.woman')
    create_tag('man', 'Man', 1, person, 'subject.person.man')
    create_tag('child', 'Child', 1, person, 'subject.person.child')
    
    # Object branch
    obj = create_tag('object', 'Object', 1, subject, 'subject.object')
    weapon = create_tag('weapon', 'Weapon', 1, obj, 'subject.object.weapon')
    create_tag('sword', 'Sword', 1, weapon, 'subject.object.weapon.sword')
    create_tag('gun', 'Gun', 1, weapon, 'subject.object.weapon.gun')
    create_tag('bow', 'Bow', 1, weapon, 'subject.object.weapon.bow')
    
    furniture = create_tag('furniture', 'Furniture', 1, obj, 'subject.object.furniture')
    create_tag('chair', 'Chair', 1, furniture, 'subject.object.furniture.chair')
    create_tag('table', 'Table', 1, furniture, 'subject.object.furniture.table')
    create_tag('bed', 'Bed', 1, furniture, 'subject.object.furniture.bed')
    
    vehicle = create_tag('vehicle', 'Vehicle', 1, obj, 'subject.object.vehicle')
    create_tag('car', 'Car', 1, vehicle, 'subject.object.vehicle.car')
    create_tag('airplane', 'Airplane', 1, vehicle, 'subject.object.vehicle.airplane')
    create_tag('ship', 'Ship', 1, vehicle, 'subject.object.vehicle.ship')
    
    # Style category (id=2)
    style = create_tag('style', 'Style', 2, None, 'style')
    create_tag('photorealistic', 'Photorealistic', 2, style, 'style.photorealistic')
    create_tag('anime', 'Anime', 2, style, 'style.anime')
    create_tag('cartoon', 'Cartoon', 2, style, 'style.cartoon')
    create_tag('pixel_art', 'Pixel Art', 2, style, 'style.pixel_art')
    create_tag('oil_painting', 'Oil Painting', 2, style, 'style.oil_painting')
    create_tag('sketch', 'Sketch', 2, style, 'style.sketch')
    create_tag('minimalist', 'Minimalist', 2, style, 'style.minimalist')
    create_tag('vintage', 'Vintage', 2, style, 'style.vintage')
    create_tag('abstract', 'Abstract', 2, style, 'style.abstract')
    
    # Environment category (id=3)
    env = create_tag('environment', 'Environment', 3, None, 'environment')
    create_tag('indoor', 'Indoor', 3, env, 'environment.indoor')
    create_tag('outdoor', 'Outdoor', 3, env, 'environment.outdoor')
    create_tag('forest', 'Forest', 3, env, 'environment.forest')
    create_tag('city', 'City', 3, env, 'environment.city')
    create_tag('space', 'Space', 3, env, 'environment.space')
    create_tag('underwater', 'Underwater', 3, env, 'environment.underwater')
    create_tag('beach', 'Beach', 3, env, 'environment.beach')
    create_tag('mountain', 'Mountain', 3, env, 'environment.mountain')
    create_tag('night', 'Night', 3, env, 'environment.night')
    create_tag('day', 'Day', 3, env, 'environment.day')
    
    # Quality category (id=4)
    quality = create_tag('quality', 'Quality', 4, None, 'quality')
    create_tag('masterpiece', 'Masterpiece', 4, quality, 'quality.masterpiece')
    create_tag('high_quality', 'High Quality', 4, quality, 'quality.high_quality')
    create_tag('medium_quality', 'Medium Quality', 4, quality, 'quality.medium_quality')
    create_tag('low_quality', 'Low Quality', 4, quality, 'quality.low_quality')
    
    # Media Type category (id=5)
    media = create_tag('media_type', 'Media Type', 5, None, 'media_type')
    create_tag('photo', 'Photo', 5, media, 'media_type.photo')
    create_tag('illustration', 'Illustration', 5, media, 'media_type.illustration')
    create_tag('render', '3D Render', 5, media, 'media_type.render')
    create_tag('video', 'Video', 5, media, 'media_type.video')
