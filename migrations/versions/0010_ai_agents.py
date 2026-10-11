"""AI agents: per-user assistants with system prompts and tool allow-lists.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-11

Introduces:

- ``ai_agents``          per-user agent definitions (system prompt, icon,
                         explicit tool allow-list); built-ins are seeded
                         per user by the application layer
- ``ai_conversations.agent_id``     the agent a conversation runs with
                                    (NULL = the default assistant)
- ``ai_chat_settings.active_agent_id``  the agent preselected for new chats

Both new columns deliberately carry no FOREIGN KEY, mirroring
``ai_chat_settings.active_provider_id``: agents can be deleted freely and
the application layer clears dangling references.
"""
from alembic import op

from migrations.migration_utils import column_exists, recreate_table_without_column

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _exec(sql: str) -> None:
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    conn = op.get_bind()
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            builtin_key TEXT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT 'sparkles',
            color_index INTEGER NOT NULL DEFAULT 0,
            tools_json TEXT NOT NULL DEFAULT '[]',
            is_builtin INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_agents_user ON ai_agents(user_id)")

    if not column_exists(conn, "ai_conversations", "agent_id"):
        _exec("ALTER TABLE ai_conversations ADD COLUMN agent_id INTEGER")
    if not column_exists(conn, "ai_chat_settings", "active_agent_id"):
        _exec("ALTER TABLE ai_chat_settings ADD COLUMN active_agent_id INTEGER")

    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    recreate_table_without_column(conn, "ai_conversations", "agent_id")
    recreate_table_without_column(conn, "ai_chat_settings", "active_agent_id")

    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    _exec("DROP TABLE IF EXISTS ai_agents")
    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    conn.commit()
