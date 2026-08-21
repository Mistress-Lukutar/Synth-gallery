"""Enforce NOT NULL on albums.name.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-21

Legacy rename requests were written to the database without validation, so
old deployments may carry albums with a NULL name (they crashed batch
downloads with a TypeError). Such rows are backfilled with a deterministic
``Untitled (id8)`` name — unique per album and traceable to its id —
before the table is rebuilt with the constraint. Databases created by
revision 0001 already have the NOT NULL column and this is a no-op.
"""
from alembic import op

from app.database import DATABASE_PATH
from migrations.migration_utils import backup_database, rebuild_table

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = conn.exec_driver_sql("PRAGMA table_info(albums)").mappings().all()
    name_col = next((row for row in columns if row["name"] == "name"), None)
    if name_col is None or name_col["notnull"]:
        return

    backup_database(DATABASE_PATH, ".albumnotnull-bak")
    conn.exec_driver_sql(
        "UPDATE albums SET name = 'Untitled (' || substr(id, 1, 8) || ')' "
        "WHERE name IS NULL"
    )
    # Pre-FK schemas may reference folders that were deleted long ago;
    # null those references so the rebuilt table (which declares the FKs)
    # passes PRAGMA foreign_key_check.
    conn.exec_driver_sql(
        "UPDATE albums SET folder_id = NULL "
        "WHERE folder_id IS NOT NULL "
        "AND folder_id NOT IN (SELECT id FROM folders)"
    )
    keep = [row["name"] for row in columns]
    rebuild_table(
        conn,
        "albums",
        """
        CREATE TABLE albums (
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
        """,
        keep,
    )
    # Dropping the old table dropped its indexes; recreate the one the
    # baseline declares so it survives the rebuild.
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_albums_folder_id ON albums(folder_id)"
    )
    conn.commit()


def downgrade() -> None:
    pass
