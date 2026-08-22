"""Baseline schema: every table as of the pre-migration 1.x layout.

Revision ID: 0001
Revises: -
Create Date: 2026-08-21

Idempotent by construction (CREATE TABLE IF NOT EXISTS + guarded ALTERs),
so it applies cleanly both to fresh empty databases and to legacy 1.x
databases that were created by the old runtime ``init_db()``.
The v2 polymorphic rebuilds and the album NOT NULL constraint live in
later revisions.
"""
from alembic import op

from migrations.migration_utils import (
    column_exists,
    recreate_table_without_column,
    table_exists,
)

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _exec(sql: str) -> None:
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _exec("""
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

    # Security columns on legacy users tables.
    if not column_exists(op.get_bind(), "users", "failed_login_attempts"):
        _exec("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0")
    if not column_exists(op.get_bind(), "users", "locked_until"):
        _exec("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP")
    if not column_exists(op.get_bind(), "users", "last_login"):
        _exec("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")

    _exec("""
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

    # Session columns on legacy tables.
    if not column_exists(op.get_bind(), "sessions", "ip_address"):
        _exec("ALTER TABLE sessions ADD COLUMN ip_address TEXT")
    if not column_exists(op.get_bind(), "sessions", "user_agent"):
        _exec("ALTER TABLE sessions ADD COLUMN user_agent TEXT")
    if not column_exists(op.get_bind(), "sessions", "last_active_at"):
        _exec("ALTER TABLE sessions ADD COLUMN last_active_at TIMESTAMP")

    _exec("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")

    _exec("""
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

    _exec("""
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

    _exec("""
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

    _exec("""
        CREATE TABLE IF NOT EXISTS albums (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder_id TEXT,
            user_id INTEGER,
            cover_item_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (cover_item_id) REFERENCES items(id) ON DELETE SET NULL
        )
    """)

    # === Tag System v2 ===

    _exec("""
        CREATE TABLE IF NOT EXISTS tag_categories (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        )
    """)

    _exec("""
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

    # Legacy hierarchical-tag layout: drop the tree columns via recreation
    # (SQLite cannot DROP COLUMN on a table with foreign keys).
    if column_exists(op.get_bind(), "tags", "parent_id"):
        op.get_bind().commit()
        _exec("PRAGMA foreign_keys = OFF")
        _exec("""
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
        _exec("""
            INSERT INTO tags_new (id, name, display_name, category_id, usage_count, description, created_at)
            SELECT id, name, display_name, category_id, usage_count, description, created_at FROM tags
        """)
        _exec("DROP TABLE tags")
        _exec("ALTER TABLE tags_new RENAME TO tags")
        _exec("PRAGMA foreign_keys = ON")

    if not column_exists(op.get_bind(), "tags", "description"):
        _exec("ALTER TABLE tags ADD COLUMN description TEXT DEFAULT ''")

    _exec("""
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

    _exec("""
        CREATE TABLE IF NOT EXISTS tag_implications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            implies_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(tag_id, implies_tag_id)
        )
    """)

    _exec("""
        CREATE TABLE IF NOT EXISTS tag_cooccurrence (
            tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            count INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tag_a_id, tag_b_id),
            CHECK (tag_a_id < tag_b_id)
        )
    """)

    _exec("""
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

    _exec("""
        CREATE TABLE IF NOT EXISTS tag_suggestion_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            context_tag_ids TEXT NOT NULL,
            suggested_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            outcome TEXT NOT NULL CHECK(outcome IN ('accepted', 'rejected', 'dismissed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === Polymorphic item tables (pre-v2 shape; revision 0002 rebuilds) ===

    _exec("""
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

    _exec("""
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

    _exec("""
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

    _exec("CREATE INDEX IF NOT EXISTS idx_items_type ON items(type)")
    _exec("CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_album_items_album ON album_items(album_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_album_items_item ON album_items(item_id)")
    _exec("DROP INDEX IF EXISTS idx_tags_path")
    _exec("DROP INDEX IF EXISTS idx_tags_parent")
    _exec("CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_item_tags_explicit ON item_tags(item_id, is_explicit)")
    _exec("CREATE INDEX IF NOT EXISTS idx_cooccurrence_a ON tag_cooccurrence(tag_a_id, count DESC)")
    _exec("CREATE INDEX IF NOT EXISTS idx_cooccurrence_b ON tag_cooccurrence(tag_b_id, count DESC)")
    _exec("CREATE INDEX IF NOT EXISTS idx_mutex_a ON tag_mutex_pairs(tag_a_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_mutex_b ON tag_mutex_pairs(tag_b_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_feedback_suggested_tag ON tag_suggestion_feedback(suggested_tag_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_feedback_outcome ON tag_suggestion_feedback(outcome)")
    _exec("CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_albums_folder_id ON albums(folder_id)")

    _exec("""
        CREATE TABLE IF NOT EXISTS user_folder_preferences (
            user_id INTEGER NOT NULL,
            folder_id TEXT NOT NULL,
            sort_by TEXT DEFAULT 'uploaded',
            PRIMARY KEY (user_id, folder_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    _exec("""
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

    # Remove the removed safes feature: drop its tables and safe_id columns.
    for table in ("safe_sessions", "safes"):
        if table_exists(op.get_bind(), table):
            _exec(f"DROP TABLE {table}")
    for table in ("folders", "albums", "items"):
        if column_exists(op.get_bind(), table, "safe_id"):
            # Rows that belonged to a safe were encrypted with a client-side
            # key the server no longer stores; they are unrecoverable.
            _exec(f"DELETE FROM {table} WHERE safe_id IS NOT NULL")
            recreate_table_without_column(op.get_bind(), table, "safe_id")

    _exec("""
        CREATE TABLE IF NOT EXISTS user_public_keys (
            user_id INTEGER PRIMARY KEY,
            public_key BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    _exec("""
        CREATE TABLE IF NOT EXISTS folder_keys (
            folder_id TEXT PRIMARY KEY,
            encrypted_folder_dek TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Item columns added after the initial 1.x release.
    if not column_exists(op.get_bind(), "items", "description"):
        _exec("ALTER TABLE items ADD COLUMN description TEXT")
    if not column_exists(op.get_bind(), "items", "updated_at"):
        # SQLite doesn't support DEFAULT with non-constant values in ALTER TABLE
        _exec("ALTER TABLE items ADD COLUMN updated_at TIMESTAMP")
        _exec("UPDATE items SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")

    if column_exists(op.get_bind(), "items", "metadata"):
        recreate_table_without_column(op.get_bind(), "items", "metadata")

    if not column_exists(op.get_bind(), "item_media", "file_size"):
        _exec("ALTER TABLE item_media ADD COLUMN file_size INTEGER")
    if not column_exists(op.get_bind(), "item_media", "png_text_chunks"):
        _exec("ALTER TABLE item_media ADD COLUMN png_text_chunks TEXT")

    _exec("""
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

    # Columns added to ai_tagging_jobs after its initial release.
    if not column_exists(op.get_bind(), "ai_tagging_jobs", "user_id"):
        _exec("ALTER TABLE ai_tagging_jobs ADD COLUMN user_id INTEGER")
        _exec(
            "UPDATE ai_tagging_jobs SET user_id = "
            "(SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL"
        )
    if not column_exists(op.get_bind(), "ai_tagging_jobs", "processing_deadline"):
        _exec("ALTER TABLE ai_tagging_jobs ADD COLUMN processing_deadline TIMESTAMP")

    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_tagging_jobs(status)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_item ON ai_tagging_jobs(item_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_user ON ai_tagging_jobs(user_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_deadline ON ai_tagging_jobs(processing_deadline)")

    _exec("""
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

    # Columns added to ai_api_keys after its initial release.
    if not column_exists(op.get_bind(), "ai_api_keys", "user_id"):
        _exec("ALTER TABLE ai_api_keys ADD COLUMN user_id INTEGER REFERENCES users(id)")
    if not column_exists(op.get_bind(), "ai_api_keys", "created_by"):
        _exec("ALTER TABLE ai_api_keys ADD COLUMN created_by INTEGER REFERENCES users(id)")
    if not column_exists(op.get_bind(), "ai_api_keys", "expires_at"):
        _exec("ALTER TABLE ai_api_keys ADD COLUMN expires_at TIMESTAMP")
    if not column_exists(op.get_bind(), "ai_api_keys", "last_used_at"):
        _exec("ALTER TABLE ai_api_keys ADD COLUMN last_used_at TIMESTAMP")
    if not column_exists(op.get_bind(), "ai_api_keys", "rate_limit_tier"):
        _exec("ALTER TABLE ai_api_keys ADD COLUMN rate_limit_tier TEXT DEFAULT 'default'")

    op.get_bind().commit()


def downgrade() -> None:
    # Baseline schema: nothing to downgrade to.
    pass
