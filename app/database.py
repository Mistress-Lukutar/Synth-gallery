import sqlite3
import hashlib
import secrets
import threading
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

# Thread-local storage for database connections
_local = threading.local()


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

    # Migration: add default_folder_id to users
    user_columns = [row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()]
    if "default_folder_id" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN default_folder_id TEXT")

    # Migration: add folder_id and user_id to albums
    album_columns = [row[1] for row in db.execute("PRAGMA table_info(albums)").fetchall()]
    if "folder_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN folder_id TEXT")
    if "user_id" not in album_columns:
        db.execute("ALTER TABLE albums ADD COLUMN user_id INTEGER")

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


def search_users(query: str, exclude_user_id: int = None, limit: int = 10) -> list:
    """Search users by username or display_name"""
    db = get_db()
    search_pattern = f"%{query.lower()}%"

    if exclude_user_id:
        users = db.execute("""
            SELECT id, username, display_name
            FROM users
            WHERE id != ? AND (LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?)
            ORDER BY display_name
            LIMIT ?
        """, (exclude_user_id, search_pattern, search_pattern, limit)).fetchall()
    else:
        users = db.execute("""
            SELECT id, username, display_name
            FROM users
            WHERE LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?
            ORDER BY display_name
            LIMIT ?
        """, (search_pattern, search_pattern, limit)).fetchall()

    return [dict(u) for u in users]


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


# === Folder Management ===

def create_folder(name: str, user_id: int, parent_id: str = None) -> str:
    """Create a new folder. Returns folder ID."""
    import uuid
    db = get_db()
    folder_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO folders (id, name, parent_id, user_id) VALUES (?, ?, ?, ?)",
        (folder_id, name.strip(), parent_id, user_id)
    )
    db.commit()
    return folder_id


def get_folder(folder_id: str):
    """Get folder by ID"""
    db = get_db()
    return db.execute(
        "SELECT * FROM folders WHERE id = ?",
        (folder_id,)
    ).fetchone()


def update_folder(folder_id: str, name: str = None):
    """Update folder name"""
    db = get_db()
    if name is not None:
        db.execute("UPDATE folders SET name = ? WHERE id = ?", (name.strip(), folder_id))
        db.commit()


def delete_folder(folder_id: str):
    """Delete folder and all its contents (cascades via FK)"""
    db = get_db()
    # SQLite CASCADE will handle child folders, but we need to clean up files
    # First collect all photo filenames for cleanup
    photos = db.execute("""
        WITH RECURSIVE folder_tree AS (
            SELECT id FROM folders WHERE id = ?
            UNION ALL
            SELECT f.id FROM folders f JOIN folder_tree ft ON f.parent_id = ft.id
        )
        SELECT p.filename FROM photos p
        WHERE p.folder_id IN (SELECT id FROM folder_tree)
           OR p.album_id IN (SELECT a.id FROM albums a WHERE a.folder_id IN (SELECT id FROM folder_tree))
    """, (folder_id,)).fetchall()

    filenames = [p["filename"] for p in photos]

    # Delete the folder (cascades to children, albums, photos via FK)
    db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    db.commit()

    return filenames  # Return for file cleanup in main.py


def get_user_folders(user_id: int) -> list:
    """Get all folders owned by user"""
    db = get_db()
    folders = db.execute(
        "SELECT * FROM folders WHERE user_id = ? ORDER BY name",
        (user_id,)
    ).fetchall()
    return [dict(f) for f in folders]


def get_folder_tree(user_id: int) -> list:
    """Get folder tree for sidebar (user's folders + folders with permissions)"""
    db = get_db()
    folders = db.execute("""
        SELECT f.*, u.display_name as owner_name,
               (SELECT COUNT(*) FROM photos p WHERE p.folder_id = f.id) as photo_count,
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
               END as share_status
        FROM folders f
        JOIN users u ON f.user_id = u.id
        WHERE f.user_id = ?
           OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
        ORDER BY f.name
    """, (user_id, user_id, user_id, user_id, user_id)).fetchall()
    return [dict(f) for f in folders]


def get_folder_children(folder_id: str) -> list:
    """Get direct child folders"""
    db = get_db()
    folders = db.execute(
        "SELECT * FROM folders WHERE parent_id = ? ORDER BY name",
        (folder_id,)
    ).fetchall()
    return [dict(f) for f in folders]


def get_folder_breadcrumbs(folder_id: str) -> list:
    """Get breadcrumb path from root to folder"""
    db = get_db()
    breadcrumbs = []
    current_id = folder_id

    while current_id:
        folder = db.execute(
            "SELECT id, name, parent_id FROM folders WHERE id = ?",
            (current_id,)
        ).fetchone()
        if folder:
            breadcrumbs.insert(0, {"id": folder["id"], "name": folder["name"]})
            current_id = folder["parent_id"]
        else:
            break

    return breadcrumbs


def create_default_folder(user_id: int) -> str:
    """Create default folder for user and set it as default"""
    db = get_db()
    folder_id = create_folder("My Gallery", user_id, None, 'private')
    db.execute("UPDATE users SET default_folder_id = ? WHERE id = ?", (folder_id, user_id))
    db.commit()
    return folder_id


def get_user_default_folder(user_id: int) -> str:
    """Get user's default folder ID, create if doesn't exist"""
    db = get_db()
    user = db.execute("SELECT default_folder_id FROM users WHERE id = ?", (user_id,)).fetchone()

    if user and user["default_folder_id"]:
        # Verify folder still exists
        folder = get_folder(user["default_folder_id"])
        if folder:
            return user["default_folder_id"]

    # Create default folder if missing
    return create_default_folder(user_id)


def set_user_default_folder(user_id: int, folder_id: str):
    """Set user's default folder"""
    db = get_db()
    db.execute("UPDATE users SET default_folder_id = ? WHERE id = ?", (folder_id, user_id))
    db.commit()


# === Folder Permissions ===

def add_folder_permission(folder_id: str, user_id: int, permission: str, granted_by: int) -> bool:
    """Add or update permission for a user on a folder"""
    if permission not in ('viewer', 'editor'):
        return False

    db = get_db()
    try:
        db.execute("""
            INSERT INTO folder_permissions (folder_id, user_id, permission, granted_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(folder_id, user_id) DO UPDATE SET permission = ?, granted_by = ?, granted_at = CURRENT_TIMESTAMP
        """, (folder_id, user_id, permission, granted_by, permission, granted_by))
        db.commit()
        return True
    except Exception:
        return False


def remove_folder_permission(folder_id: str, user_id: int) -> bool:
    """Remove permission for a user on a folder"""
    db = get_db()
    result = db.execute(
        "DELETE FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
        (folder_id, user_id)
    )
    db.commit()
    return result.rowcount > 0


def update_folder_permission(folder_id: str, user_id: int, permission: str) -> bool:
    """Update permission level for a user on a folder"""
    if permission not in ('viewer', 'editor'):
        return False

    db = get_db()
    result = db.execute(
        "UPDATE folder_permissions SET permission = ? WHERE folder_id = ? AND user_id = ?",
        (permission, folder_id, user_id)
    )
    db.commit()
    return result.rowcount > 0


def get_folder_permissions(folder_id: str) -> list:
    """Get all permissions for a folder with user info"""
    db = get_db()
    permissions = db.execute("""
        SELECT fp.user_id, fp.permission, fp.granted_at,
               u.username, u.display_name
        FROM folder_permissions fp
        JOIN users u ON fp.user_id = u.id
        WHERE fp.folder_id = ?
        ORDER BY u.display_name
    """, (folder_id,)).fetchall()
    return [dict(p) for p in permissions]


def get_user_permission(folder_id: str, user_id: int) -> str | None:
    """Get user's permission level for a folder: 'owner', 'editor', 'viewer', or None"""
    db = get_db()

    # Check if user is owner
    folder = db.execute("SELECT user_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if folder and folder["user_id"] == user_id:
        return 'owner'

    # Check explicit permissions
    perm = db.execute(
        "SELECT permission FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
        (folder_id, user_id)
    ).fetchone()

    return perm["permission"] if perm else None


def can_view_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can view folder (owner, viewer, or editor)"""
    if not folder_id:
        return True

    permission = get_user_permission(folder_id, user_id)
    return permission in ('owner', 'viewer', 'editor')


def can_edit_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can edit folder content (owner or editor)"""
    if not folder_id:
        return False

    permission = get_user_permission(folder_id, user_id)
    return permission in ('owner', 'editor')


# === Access Control ===

def can_access_folder(folder_id: str, user_id: int) -> bool:
    """Check if user can access folder (view permission)"""
    return can_view_folder(folder_id, user_id)


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
