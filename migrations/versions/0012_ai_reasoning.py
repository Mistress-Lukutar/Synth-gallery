"""AI reasoning control: per-model capability + per-user effort choice.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11

Introduces:

- ``ai_models.reasoning_support``     manual reasoning-capability override for
                                      a model (NULL = auto-detect from the
                                      model id; 'levels' / 'toggle' /
                                      'enable_toggle' / 'none'). Validated
                                      app-side, hence no CHECK constraint.
- ``ai_chat_settings.reasoning_effort``  the reasoning level the user picked
                                      in the chat composer (NULL = provider
                                      default; semantics depend on the
                                      active model's effective style).
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    if not column_exists(conn, "ai_models", "reasoning_support"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_models ADD COLUMN reasoning_support TEXT"
        )
    if not column_exists(conn, "ai_chat_settings", "reasoning_effort"):
        conn.exec_driver_sql(
            "ALTER TABLE ai_chat_settings ADD COLUMN reasoning_effort TEXT"
        )
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    recreate_table_without_column(conn, "ai_models", "reasoning_support")
    recreate_table_without_column(conn, "ai_chat_settings", "reasoning_effort")
    conn.commit()
