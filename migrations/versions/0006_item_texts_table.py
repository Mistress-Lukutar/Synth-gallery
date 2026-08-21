"""Add the item_texts detail table for the 'note' item type.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-21

Text notes (txt/md/json/csv/yaml) are stored as polymorphic items with
``items.type = 'note'``. The plaintext content lives in storage as an
encrypted SGE1 envelope (same as media files); this table holds only the
per-type metadata: detected encoding, character/line counts, the original
MIME type and filename.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS item_texts (
            item_id TEXT PRIMARY KEY,
            content_type TEXT NOT NULL,
            original_name TEXT,
            encoding TEXT NOT NULL DEFAULT 'utf-8',
            char_count INTEGER NOT NULL DEFAULT 0,
            line_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
        """
    )
    op.get_bind().exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_item_texts_item ON item_texts(item_id)"
    )
    op.get_bind().commit()


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS item_texts")
    op.get_bind().commit()
