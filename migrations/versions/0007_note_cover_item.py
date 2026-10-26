"""Add item_texts.cover_item_id for note cover images.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02

Notes can now reference a media item whose thumbnail acts as the note's
cover in the gallery grid (mirroring albums.cover_item_id). The cover must
be an image item; the reference is dropped (SET NULL) when the referenced
item is deleted.
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if column_exists(conn, "item_texts", "cover_item_id"):
        return
    conn.exec_driver_sql(
        "ALTER TABLE item_texts ADD COLUMN cover_item_id TEXT "
        "REFERENCES items(id) ON DELETE SET NULL"
    )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "item_texts", "cover_item_id"):
        return
    recreate_table_without_column(conn, "item_texts", "cover_item_id")
    conn.commit()
