"""v2 polymorphic item schema: drop dead item_media columns, add CHECKs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21

- Drops the dead ``item_media.storage_mode`` column (never read).
- Drops the redundant ``item_media.filename`` column (always == item_id;
  the storage key is derived from item_id everywhere).
- Adds ``CHECK (type IN ('media'))`` to ``items`` (extended to 'note' in
  revision 0005).
- Adds ``CHECK (media_type IN ('image', 'video'))`` to ``item_media``.

Applied idempotently: freshly created (post-0001) databases already have
the new shape and this revision only takes a backup when there is
something to rebuild.
"""
from alembic import op

from app.database import DATABASE_PATH
from migrations.migration_utils import backup_database, rebuild_table

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ---- item_media: drop storage_mode + filename, add CHECK ----
    media_cols = [
        row["name"]
        for row in conn.exec_driver_sql("PRAGMA table_info(item_media)").mappings().all()
    ]
    if "storage_mode" in media_cols or "filename" in media_cols:
        backup_database(DATABASE_PATH, ".v2migration-bak")
        # Normalise legacy media_type values that predate the v2 image/video
        # enum (e.g. '3d', which belongs to a future items.type, not a media
        # sub-kind) so the new CHECK constraint does not reject existing rows.
        conn.exec_driver_sql(
            "UPDATE item_media SET media_type = 'image' "
            "WHERE media_type NOT IN ('image', 'video')"
        )
        keep = [c for c in media_cols if c not in ("storage_mode", "filename")]
        rebuild_table(
            conn,
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
    # SQLite stores CHECK constraints only in the table's CREATE statement,
    # so detect the pre-v2 shape by inspecting the original sql.
    schema = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).mappings().first()
    if schema and "CHECK (type IN" not in (schema["sql"] or ""):
        if not ("storage_mode" in media_cols or "filename" in media_cols):
            # No backup was taken in the item_media branch yet.
            backup_database(DATABASE_PATH, ".v2migration-bak")
        # Normalise legacy items.type values to 'media' before adding the
        # CHECK constraint so existing rows are not rejected.
        conn.exec_driver_sql(
            "UPDATE items SET type = 'media' WHERE type NOT IN ('media')"
        )
        items_keep = [
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
            items_keep,
        )

    conn.commit()


def downgrade() -> None:
    # Column drops are not reversible (the dropped data no longer exists).
    pass
