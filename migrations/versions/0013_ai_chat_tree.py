"""AI chat message tree: branching for edit and retry.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16

Introduces:

- ``ai_messages.parent_id``            id of the preceding message in the
                                       same branch (NULL = first message of
                                       a branch). Messages form a tree;
                                       editing/retrying an earlier user
                                       message adds a sibling branch
                                       instead of rewriting history.
- ``ai_conversations.active_leaf_id``  leaf of the branch new messages
                                       append to and history replay walks
                                       (NULL = legacy behavior: messages
                                       ordered by id).
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    if not column_exists(conn, "ai_messages", "parent_id"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_messages ADD COLUMN parent_id INTEGER"
        )
    if not column_exists(conn, "ai_conversations", "active_leaf_id"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_conversations ADD COLUMN active_leaf_id INTEGER"
        )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    recreate_table_without_column(conn, "ai_messages", "parent_id")
    recreate_table_without_column(
        conn, "ai_conversations", "active_leaf_id"
    )
    conn.commit()
