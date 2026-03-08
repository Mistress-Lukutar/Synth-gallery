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
            encrypted_dek BLOB,
            fingerprint TEXT,
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
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, tag_id),
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
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
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_path ON tags(path)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id)")
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

    # Insert default tag categories (v2)
    _init_tag_categories_v2(db)
    
    # Insert default tag hierarchy (v2)
    _init_tag_hierarchy_v2(db)

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


def _init_tag_categories_v2(db):
    """Initialize tag categories for v2 hierarchical tag system."""
    default_categories = [
        (1, 'subject', 'Subject', '#3b82f6', 1),
        (2, 'style', 'Style', '#8b5cf6', 2),
        (3, 'environment', 'Environment', '#10b981', 3),
        (4, 'quality', 'Quality', '#f59e0b', 4),
        (5, 'media_type', 'Media Type', '#6b7280', 5),
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
            
            if parent_id:
                db.execute("UPDATE tags SET is_leaf = 0 WHERE id = ?", (parent_id,))
            
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            row = db.execute("SELECT id FROM tags WHERE path = ?", (path,)).fetchone()
            return row[0] if row else None
    
    # Subject category
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
    
    # Style category
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
    
    # Environment category
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
    
    # Quality category
    quality = create_tag('quality', 'Quality', 4, None, 'quality')
    create_tag('masterpiece', 'Masterpiece', 4, quality, 'quality.masterpiece')
    create_tag('high_quality', 'High Quality', 4, quality, 'quality.high_quality')
    create_tag('medium_quality', 'Medium Quality', 4, quality, 'quality.medium_quality')
    create_tag('low_quality', 'Low Quality', 4, quality, 'quality.low_quality')
    
    # Media Type category
    media = create_tag('media_type', 'Media Type', 5, None, 'media_type')
    create_tag('photo', 'Photo', 5, media, 'media_type.photo')
    create_tag('illustration', 'Illustration', 5, media, 'media_type.illustration')
    create_tag('render', '3D Render', 5, media, 'media_type.render')
    create_tag('video', 'Video', 5, media, 'media_type.video')
