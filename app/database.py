'''
File:   database.py
Brief:  Database connection, schema initialization and password hashing.
Author: Mistress-Lukutar
Date:   2026-07-23
Version: v1.1.1
'''
import logging
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
# Schema Migration Helpers
# =============================================================================
def _column_exists(db: sqlite3.Connection, table: str, column: str) -> bool:
    """Check whether a column exists in a table."""
    cursor = db.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cursor.fetchall())


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    """Check whether a table exists in the database."""
    cursor = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return cursor.fetchone() is not None


def _recreate_table_without_column(
    db: sqlite3.Connection,
    table: str,
    column: str,
) -> None:
    """Recreate a table without the given column.

    SQLite cannot drop a column that is referenced by a table-level foreign
    key constraint, so we build the new schema dynamically and copy data over.
    """
    # Preserve foreign-key behaviour while we rewrite the table.
    db.execute("PRAGMA foreign_keys = OFF")

    cursor = db.execute(f"PRAGMA table_info({table})")
    columns = [row for row in cursor.fetchall() if row["name"] != column]
    column_names = [c["name"] for c in columns]

    # Build column definitions from PRAGMA output.
    col_defs = []
    for col in columns:
        parts = [f'"{col["name"]}"', col["type"]]
        if col["notnull"]:
            parts.append("NOT NULL")
        if col["dflt_value"] is not None:
            parts.append(f"DEFAULT ({col['dflt_value']})")
        if col["pk"]:
            parts.append("PRIMARY KEY")
        col_defs.append(" ".join(parts))

    # Keep foreign keys that do not involve the dropped column.
    cursor = db.execute(f"PRAGMA foreign_key_list({table})")
    fks = [
        row for row in cursor.fetchall()
        if row["from"] != column and row["to"] != column
    ]
    # Group by constraint id so multi-column FKs stay intact.
    fk_groups: dict[int, list[dict]] = {}
    for fk in fks:
        fk_groups.setdefault(fk["id"], []).append(fk)
    for group in fk_groups.values():
        from_cols = ", ".join(f'"{fk["from"]}"' for fk in group)
        to_table = group[0]["table"]
        to_cols = ", ".join(f'"{fk["to"]}"' for fk in group)
        on_update = group[0]["on_update"]
        on_delete = group[0]["on_delete"]
        clause = f"FOREIGN KEY ({from_cols}) REFERENCES {to_table} ({to_cols})"
        if on_update and on_update != "NO ACTION":
            clause += f" ON UPDATE {on_update}"
        if on_delete and on_delete != "NO ACTION":
            clause += f" ON DELETE {on_delete}"
        col_defs.append(clause)

    new_table = f"{table}_new"
    db.execute(f"DROP TABLE IF EXISTS {new_table}")
    db.execute(f"CREATE TABLE {new_table} ({', '.join(col_defs)})")

    placeholders = ", ".join("?" for _ in column_names)
    quoted_names = ", ".join(f'"{name}"' for name in column_names)
    db.execute(
        f"INSERT INTO {new_table} ({quoted_names}) SELECT {quoted_names} FROM {table}"
    )

    db.execute(f"DROP TABLE {table}")
    db.execute(f"ALTER TABLE {new_table} RENAME TO {table}")

    db.execute("PRAGMA foreign_keys = ON")


def _drop_safe_columns(db: sqlite3.Connection) -> None:
    """Remove safe_id columns from folders, albums and items.

    Existing rows that belonged to a safe are deleted first because their
    files are encrypted with a client-side key the server no longer stores.
    """
    tables = ("folders", "albums", "items")
    for table in tables:
        if not _column_exists(db, table, "safe_id"):
            continue

        db.execute(f"DELETE FROM {table} WHERE safe_id IS NOT NULL")
        _recreate_table_without_column(db, table, "safe_id")
    db.commit()


def _drop_safe_tables(db: sqlite3.Connection) -> None:
    """Drop safe and safe session tables if they still exist."""
    for table in ("safe_sessions", "safes"):
        if _table_exists(db, table):
            db.execute(f"DROP TABLE {table}")
    db.commit()


def _backup_database(db_path: Path, suffix: str) -> Path | None:
    """Copy the database file to ``<db_path><suffix>`` before a breaking change.

    Returns the backup path on success, or ``None`` if the source does not
    exist (e.g. an in-memory ``:memory:`` database used by tests). The backup
    is a best-effort safety net; failures are logged but do not abort the
    migration.
    """
    import shutil

    src = str(db_path)
    if not db_path.exists():
        return None
    dest = Path(f"{src}{suffix}")
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Pre-migration backup of %s failed: %s", src, exc
        )
        return None
    return dest


def _rebuild_table(
    db: sqlite3.Connection,
    table: str,
    create_ddl: str,
    keep_columns: list[str],
) -> None:
    """Rebuild ``table`` with ``create_ddl``, copying ``keep_columns`` over.

    SQLite cannot add/drop CHECK constraints or alter a column type in place,
    so changing a table's shape requires a full rebuild: create a shadow table
    with the new schema, copy the surviving columns, drop the original and
    rename. ``create_ddl`` is the full ``CREATE TABLE`` statement for the new
    shape; ``keep_columns`` lists columns that exist in both old and new
    tables and must be preserved.
    """
    db.execute("PRAGMA foreign_keys = OFF")
    new_table = f"{table}_new"
    db.execute(f"DROP TABLE IF EXISTS {new_table}")
    db.execute(create_ddl.replace(table, new_table, 1))

    quoted = ", ".join(f'"{c}"' for c in keep_columns)
    db.execute(
        f"INSERT INTO {new_table} ({quoted}) SELECT {quoted} FROM {table}"
    )

    db.execute(f"DROP TABLE {table}")
    db.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
    db.execute("PRAGMA foreign_keys = ON")


def _migrate_v2_schema(db: sqlite3.Connection) -> None:
    """One-time v2.0 schema migration for the polymorphic item tables.

    - Drops the dead ``item_media.storage_mode`` column (never read).
    - Drops the redundant ``item_media.filename`` column (always == item_id;
      the storage key is derived from item_id everywhere).
    - Adds ``CHECK (type IN ('media'))`` to ``items``.
    - Adds ``CHECK (media_type IN ('image', 'video'))`` to ``item_media``.

    All four are applied idempotently: a freshly created (v2) database has
    the new shape already and this function is a no-op. Existing pre-v2
    databases are rebuilt table-by-table with a pre-migration backup.
    """
    # ---- item_media: drop storage_mode + filename, add CHECK ----
    media_cursor = db.execute("PRAGMA table_info(item_media)")
    media_cols = [row["name"] for row in media_cursor.fetchall()]
    if "storage_mode" in media_cols or "filename" in media_cols:
        # Normalise legacy media_type values that predate the v2 image/video
        # enum (e.g. '3d', which belongs to a future items.type, not a media
        # sub-kind) so the new CHECK constraint does not reject existing rows.
        db.execute(
            "UPDATE item_media SET media_type = 'image' "
            "WHERE media_type NOT IN ('image', 'video')"
        )
        # Surviving columns after dropping storage_mode and filename.
        keep = [
            c for c in media_cols if c not in ("storage_mode", "filename")
        ]
        _rebuild_table(
            db,
            "item_media",
            """
            CREATE TABLE item_media (
                item_id TEXT PRIMARY KEY,
                media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video')),
                original_name TEXT,
                content_type TEXT,
                width INTEGER,
                height INTEGER,
                duration INTEGER,
                thumb_width INTEGER,
                thumb_height INTEGER,
                taken_at TIMESTAMP,
                file_size INTEGER,
                png_text_chunks TEXT,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
            """,
            keep,
        )

    # ---- items: add CHECK (type IN ('media')) ----
    # SQLite stores CHECK constraints only in the table's CREATE statement, so
    # detect the pre-v2 shape by inspecting the original sql.
    schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    if schema and "CHECK (type IN" not in (schema["sql"] or ""):
        # Normalise legacy items.type values to 'media' before adding the
        # CHECK constraint so existing rows are not rejected.
        db.execute(
            "UPDATE items SET type = 'media' "
            "WHERE type NOT IN ('media')"
        )
        items_cursor = db.execute("PRAGMA table_info(items)")
        items_keep = [row["name"] for row in items_cursor.fetchall()]
        _rebuild_table(
            db,
            "items",
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL CHECK (type IN ('media')),
                folder_id TEXT,
                user_id INTEGER,
                uploaded_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                title TEXT,
                description TEXT,
                updated_at TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """,
            items_keep,
        )

    db.commit()



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
            folder_id TEXT,
            user_id INTEGER,
            cover_item_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (cover_item_id) REFERENCES items(id) ON DELETE SET NULL
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

    # Tag mutex pairs: negative correlation cache (data-driven + manual)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_mutex_pairs (
            tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            phi REAL NOT NULL,
            is_auto INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tag_a_id, tag_b_id),
            CHECK (tag_a_id < tag_b_id)
        )
    """)

    # Tag suggestion feedback: user accept/reject/dismiss actions
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_suggestion_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            context_tag_ids TEXT NOT NULL,
            suggested_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            outcome TEXT NOT NULL CHECK(outcome IN ('accepted', 'rejected', 'dismissed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # =============================================================================
    # Polymorphic Items Architecture
    # =============================================================================
    
    # Items table - polymorphic base for all content types.
    # CHECK constraint guards items.type against typos; extend the allowed
    # set as new polymorphic types (note, audio, model) land.
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('media')),
            folder_id TEXT,
            user_id INTEGER,
            uploaded_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
            title TEXT,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)

    # Item media table - photo/video specific data.
    # media_type is a sub-kind within the 'media' item type.
    # ``filename`` was always equal to ``item_id`` and ``storage_mode`` was
    # never read; both were dropped in the v2.0 schema migration.
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_media (
            item_id TEXT PRIMARY KEY,
            media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video')),
            original_name TEXT,
            content_type TEXT,
            width INTEGER,
            height INTEGER,
            duration INTEGER,
            thumb_width INTEGER,
            thumb_height INTEGER,
            taken_at TIMESTAMP,
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
    db.execute("CREATE INDEX IF NOT EXISTS idx_mutex_a ON tag_mutex_pairs(tag_a_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mutex_b ON tag_mutex_pairs(tag_b_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_suggested_tag ON tag_suggestion_feedback(suggested_tag_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_outcome ON tag_suggestion_feedback(outcome)")
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

    # Migration: remove legacy safe/safe-session tables and safe_id columns.
    _drop_safe_tables(db)
    _drop_safe_columns(db)

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

    # Migration: Drop unused metadata column from items
    if 'metadata' in columns:
        _recreate_table_without_column(db, 'items', 'metadata')
    
    # Migration: Add file_size to item_media table
    cursor = db.execute("PRAGMA table_info(item_media)")
    media_columns = [row['name'] for row in cursor.fetchall()]
    if 'file_size' not in media_columns:
        db.execute("ALTER TABLE item_media ADD COLUMN file_size INTEGER")
    if 'png_text_chunks' not in media_columns:
        db.execute("ALTER TABLE item_media ADD COLUMN png_text_chunks TEXT")

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

    # v2.0 schema migration (idempotent): drop dead item_media columns and
    # add CHECK constraints on items.type / item_media.media_type. Take a
    # pre-migration backup of the database file before any breaking rebuild.
    _backup_database(DATABASE_PATH, ".v2migration-bak")
    _migrate_v2_schema(db)

    db.commit()


