"""Drop tags.display_name.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06

Tags now have a single name: the stored name is also the displayed one,
so the redundant human-readable display_name column is removed.
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "tags", "display_name"):
        return
    recreate_table_without_column(conn, "tags", "display_name")
    # The rebuild drops table-bound indexes; restore the one the baseline
    # schema defines for tags.
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category_id)"
    )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    if column_exists(conn, "tags", "display_name"):
        return
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    conn.exec_driver_sql("ALTER TABLE tags ADD COLUMN display_name TEXT")
    # Best-effort backfill so old code keeps rendering something readable.
    conn.exec_driver_sql(
        "UPDATE tags SET display_name = REPLACE(name, '_', ' ')"
    )
    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    conn.commit()
