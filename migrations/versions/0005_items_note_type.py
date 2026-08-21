"""Extend the items.type CHECK to allow the 'note' item type.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

The registry-driven polymorphic item architecture routes behaviour by
``items.type``; text notes arrive as a new coarse type alongside 'media'.
SQLite cannot alter a CHECK constraint in place, so the table is rebuilt.
"""
from alembic import op

from migrations.migration_utils import rebuild_table

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    keep = [
        row["name"]
        for row in conn.exec_driver_sql("PRAGMA table_info(items)").mappings().all()
    ]
    rebuild_table(
        conn,
        "items",
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('media', 'note')),
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
        keep,
    )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DELETE FROM items WHERE type = 'note'")
    keep = [
        row["name"]
        for row in conn.exec_driver_sql("PRAGMA table_info(items)").mappings().all()
    ]
    rebuild_table(
        conn,
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
        keep,
    )
    conn.commit()
