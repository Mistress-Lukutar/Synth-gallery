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
    
    # Tags (flat with optional legacy path/parent for migration compatibility)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            display_name TEXT,
            category_id INTEGER,
            parent_id INTEGER,
            path TEXT DEFAULT '',
            level INTEGER DEFAULT 0,
            is_leaf INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id),
            FOREIGN KEY (parent_id) REFERENCES tags(id)
        )
    """)
    
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
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_path ON tags(path)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent_id)")
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
    """Initialize default flat tags and implications for v3 system."""

    def create_flat_tag(name, display_name, category_id):
        """Helper to create a flat tag and return its ID."""
        try:
            cursor = db.execute("""
                INSERT INTO tags (name, display_name, category_id, usage_count, path)
                VALUES (?, ?, ?, 0, ?)
            """, (name, display_name, category_id, name))
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            row = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            return row[0] if row else None

    def add_implication(from_id, to_id):
        """Helper to add implication edge."""
        if from_id and to_id and from_id != to_id:
            db.execute("""
                INSERT OR IGNORE INTO tag_implications (tag_id, implies_tag_id)
                VALUES (?, ?)
            """, (from_id, to_id))

    # Subject category tags (do NOT create 'subject' as a tag - it's a category name)
    animal = create_flat_tag('animal', 'Animal', 1)
    mammal = create_flat_tag('mammal', 'Mammal', 1)
    fox = create_flat_tag('fox', 'Fox', 1)
    silver_fox = create_flat_tag('silver_fox', 'Silver Fox', 1)
    red_fox = create_flat_tag('red_fox', 'Red Fox', 1)
    arctic_fox = create_flat_tag('arctic_fox', 'Arctic Fox', 1)
    wolf = create_flat_tag('wolf', 'Wolf', 1)
    gray_wolf = create_flat_tag('gray_wolf', 'Gray Wolf', 1)
    white_wolf = create_flat_tag('white_wolf', 'White Wolf', 1)
    bear = create_flat_tag('bear', 'Bear', 1)
    cat = create_flat_tag('cat', 'Cat', 1)
    dog = create_flat_tag('dog', 'Dog', 1)
    bird = create_flat_tag('bird', 'Bird', 1)
    eagle = create_flat_tag('eagle', 'Eagle', 1)
    owl = create_flat_tag('owl', 'Owl', 1)
    raven = create_flat_tag('raven', 'Raven', 1)
    reptile = create_flat_tag('reptile', 'Reptile', 1)
    snake = create_flat_tag('snake', 'Snake', 1)
    lizard = create_flat_tag('lizard', 'Lizard', 1)
    person = create_flat_tag('person', 'Person', 1)
    woman = create_flat_tag('woman', 'Woman', 1)
    man = create_flat_tag('man', 'Man', 1)
    child = create_flat_tag('child', 'Child', 1)
    obj = create_flat_tag('object', 'Object', 1)
    weapon = create_flat_tag('weapon', 'Weapon', 1)
    sword = create_flat_tag('sword', 'Sword', 1)
    gun = create_flat_tag('gun', 'Gun', 1)
    bow = create_flat_tag('bow', 'Bow', 1)
    furniture = create_flat_tag('furniture', 'Furniture', 1)
    chair = create_flat_tag('chair', 'Chair', 1)
    table = create_flat_tag('table', 'Table', 1)
    bed = create_flat_tag('bed', 'Bed', 1)
    vehicle = create_flat_tag('vehicle', 'Vehicle', 1)
    car = create_flat_tag('car', 'Car', 1)
    airplane = create_flat_tag('airplane', 'Airplane', 1)
    ship = create_flat_tag('ship', 'Ship', 1)

    # Style category
    photorealistic = create_flat_tag('photorealistic', 'Photorealistic', 2)
    anime = create_flat_tag('anime', 'Anime', 2)
    cartoon = create_flat_tag('cartoon', 'Cartoon', 2)
    pixel_art = create_flat_tag('pixel_art', 'Pixel Art', 2)
    oil_painting = create_flat_tag('oil_painting', 'Oil Painting', 2)
    sketch = create_flat_tag('sketch', 'Sketch', 2)
    minimalist = create_flat_tag('minimalist', 'Minimalist', 2)
    vintage = create_flat_tag('vintage', 'Vintage', 2)
    abstract_tag = create_flat_tag('abstract', 'Abstract', 2)

    # Environment category
    indoor = create_flat_tag('indoor', 'Indoor', 3)
    outdoor = create_flat_tag('outdoor', 'Outdoor', 3)
    forest = create_flat_tag('forest', 'Forest', 3)
    city = create_flat_tag('city', 'City', 3)
    space = create_flat_tag('space', 'Space', 3)
    underwater = create_flat_tag('underwater', 'Underwater', 3)
    beach = create_flat_tag('beach', 'Beach', 3)
    mountain = create_flat_tag('mountain', 'Mountain', 3)
    night = create_flat_tag('night', 'Night', 3)
    day_tag = create_flat_tag('day', 'Day', 3)
    sea = create_flat_tag('sea', 'Sea', 3)
    ocean = create_flat_tag('ocean', 'Ocean', 3)
    water = create_flat_tag('water', 'Water', 3)

    # Quality category
    masterpiece = create_flat_tag('masterpiece', 'Masterpiece', 4)
    high_quality = create_flat_tag('high_quality', 'High Quality', 4)
    medium_quality = create_flat_tag('medium_quality', 'Medium Quality', 4)
    low_quality = create_flat_tag('low_quality', 'Low Quality', 4)

    # Media Type category
    photo = create_flat_tag('photo', 'Photo', 5)
    illustration = create_flat_tag('illustration', 'Illustration', 5)
    render = create_flat_tag('render', '3D Render', 5)
    video = create_flat_tag('video', 'Video', 5)

    # Build implications (more specific -> more general)
    # NOTE: implications go between tags, NOT to category names (e.g. no 'subject' tag)
    add_implication(mammal, animal)
    add_implication(fox, mammal)
    add_implication(silver_fox, fox)
    add_implication(red_fox, fox)
    add_implication(arctic_fox, fox)
    add_implication(wolf, mammal)
    add_implication(gray_wolf, wolf)
    add_implication(white_wolf, wolf)
    add_implication(bear, mammal)
    add_implication(cat, mammal)
    add_implication(dog, mammal)
    add_implication(bird, animal)
    add_implication(eagle, bird)
    add_implication(owl, bird)
    add_implication(raven, bird)
    add_implication(reptile, animal)
    add_implication(snake, reptile)
    add_implication(lizard, reptile)
    add_implication(woman, person)
    add_implication(man, person)
    add_implication(child, person)
    add_implication(weapon, obj)
    add_implication(sword, weapon)
    add_implication(gun, weapon)
    add_implication(bow, weapon)
    add_implication(furniture, obj)
    add_implication(chair, furniture)
    add_implication(table, furniture)
    add_implication(bed, furniture)
    add_implication(vehicle, obj)
    add_implication(car, vehicle)
    add_implication(airplane, vehicle)
    add_implication(ship, vehicle)
    add_implication(sea, water)
    add_implication(ocean, water)
    add_implication(beach, water)
    add_implication(underwater, water)
