"""AI memory: per-user toggle + private memory folder.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-24

Introduces:

- ``ai_chat_settings.memory_enabled``    per-user memory switch
                                         (0 = off by default).
- ``ai_chat_settings.memory_folder_id``  the private gallery folder that
                                         holds MEMORY.md and the memory
                                         sections. FK-free on purpose:
                                         deleting the folder clears the
                                         reference application-side via
                                         :meth:`clear_memory_folder`.
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    if not column_exists(conn, "ai_chat_settings", "memory_enabled"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_chat_settings ADD COLUMN memory_enabled "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if not column_exists(conn, "ai_chat_settings", "memory_folder_id"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_chat_settings ADD COLUMN memory_folder_id TEXT"
        )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    recreate_table_without_column(conn, "ai_chat_settings", "memory_folder_id")
    recreate_table_without_column(conn, "ai_chat_settings", "memory_enabled")
    conn.commit()
