import sqlite3
import hashlib
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "gallery.db"


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hash password with salt using SHA-256"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify password against hash"""
    check_hash, _ = hash_password(password, salt)
    return check_hash == hashed

_connection = None


def get_db() -> sqlite3.Connection:
    """Get database connection"""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection


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
    """Create a new user. Returns user ID."""
    db = get_db()
    password_hash, password_salt = hash_password(password)
    cursor = db.execute(
        "INSERT INTO users (username, password_hash, password_salt, display_name) VALUES (?, ?, ?, ?)",
        (username.lower().strip(), password_hash, password_salt, display_name.strip())
    )
    db.commit()
    return cursor.lastrowid


def get_user_by_username(username: str):
    """Get user by username"""
    db = get_db()
    return db.execute(
        "SELECT * FROM users WHERE username = ?",
        (username.lower().strip(),)
    ).fetchone()


def get_user_by_id(user_id: int):
    """Get user by ID"""
    db = get_db()
    return db.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()


def update_user_password(user_id: int, new_password: str):
    """Update user password"""
    db = get_db()
    password_hash, password_salt = hash_password(new_password)
    db.execute(
        "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
        (password_hash, password_salt, user_id)
    )
    db.commit()


def update_user_display_name(user_id: int, display_name: str):
    """Update user display name"""
    db = get_db()
    db.execute(
        "UPDATE users SET display_name = ? WHERE id = ?",
        (display_name.strip(), user_id)
    )
    db.commit()


def delete_user(user_id: int):
    """Delete user and their sessions"""
    db = get_db()
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()


def list_users():
    """List all users"""
    db = get_db()
    return db.execute(
        "SELECT id, username, display_name, created_at FROM users ORDER BY id"
    ).fetchall()


def authenticate_user(username: str, password: str):
    """Authenticate user. Returns user row if valid, None otherwise."""
    user = get_user_by_username(username)
    if user and verify_password(password, user["password_hash"], user["password_salt"]):
        return user
    return None


# === Session Management ===

def create_session(user_id: int, expires_hours: int = 24 * 7) -> str:
    """Create a new session. Returns session ID."""
    db = get_db()
    session_id = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, datetime('now', '+' || ? || ' hours'))",
        (session_id, user_id, expires_hours)
    )
    db.commit()
    return session_id


def get_session(session_id: str):
    """Get session if valid and not expired"""
    db = get_db()
    return db.execute(
        "SELECT s.*, u.username, u.display_name FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.id = ? AND s.expires_at > datetime('now')",
        (session_id,)
    ).fetchone()


def delete_session(session_id: str):
    """Delete session (logout)"""
    db = get_db()
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()


def cleanup_expired_sessions():
    """Remove expired sessions"""
    db = get_db()
    db.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    db.commit()
