"""AI chat: per-user provider tables; drop the legacy AI tagging queue.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09

Introduces the storage for the built-in AI chat:

- ``ai_providers``      per-user LLM provider endpoints (API key stored
                        encrypted with the owner's DEK as an SGE1 blob)
- ``ai_models``         per-provider model catalogue (composite PK)
- ``ai_chat_settings``  per-user active provider/model selection
- ``ai_conversations``  chat threads
- ``ai_messages``       per-conversation messages (JSON ``parts`` column)

The legacy AI tagging queue tables (``ai_tagging_jobs``, ``ai_api_keys``)
are dropped; the application code referencing them was removed in the same
release. The downgrade recreates both with their original schema (the
columns that used to be ALTER-backfilled are folded into the CREATE).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _exec(sql: str) -> None:
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    conn = op.get_bind()
    # PRAGMA foreign_keys is a no-op inside an open transaction; commit and
    # toggle it around the DDL so DROP TABLE cannot cascade into child rows.
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            label TEXT NOT NULL,
            protocol TEXT NOT NULL CHECK (protocol IN ('openai_compatible', 'anthropic', 'google_gemini')),
            base_url TEXT NOT NULL,
            api_key_encrypted BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_providers_user ON ai_providers(user_id)")

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_models (
            provider_id INTEGER NOT NULL REFERENCES ai_providers(id),
            model_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            supports_tools INTEGER NOT NULL DEFAULT 0,
            supports_vision INTEGER NOT NULL DEFAULT 0,
            is_pinned INTEGER NOT NULL DEFAULT 0,
            context_tokens INTEGER,
            max_output_tokens INTEGER,
            limits_source TEXT,
            PRIMARY KEY (provider_id, model_id)
        )
    """)

    # active_provider_id deliberately carries no FOREIGN KEY: a provider can
    # be deleted without the delete cascading or blocking, and the service
    # layer clears the dangling reference application-side.
    _exec("""
        CREATE TABLE IF NOT EXISTS ai_chat_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            active_provider_id INTEGER,
            active_model_id TEXT,
            temperature REAL NOT NULL DEFAULT 0.7,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL DEFAULT 'New chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_conversations_user ON ai_conversations(user_id)")

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES ai_conversations(id),
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            parts TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id)")

    # The legacy AI tagging queue is replaced by the built-in AI chat.
    _exec("DROP TABLE IF EXISTS ai_tagging_jobs")
    _exec("DROP TABLE IF EXISTS ai_api_keys")

    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    conn.commit()


def downgrade() -> None:
    conn = op.get_bind()
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    # Drop children before parents.
    _exec("DROP TABLE IF EXISTS ai_messages")
    _exec("DROP TABLE IF EXISTS ai_conversations")
    _exec("DROP TABLE IF EXISTS ai_models")
    _exec("DROP TABLE IF EXISTS ai_chat_settings")
    _exec("DROP TABLE IF EXISTS ai_providers")

    # Recreate the legacy AI tagging queue exactly as the baseline schema
    # declared it (the ALTER-backfilled columns are folded into the CREATE).
    _exec("""
        CREATE TABLE IF NOT EXISTS ai_tagging_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            processing_deadline TIMESTAMP,
            result_tags TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_tagging_jobs(status)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_item ON ai_tagging_jobs(item_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_user ON ai_tagging_jobs(user_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_ai_jobs_deadline ON ai_tagging_jobs(processing_deadline)")

    _exec("""
        CREATE TABLE IF NOT EXISTS ai_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER REFERENCES users(id),
            created_by INTEGER REFERENCES users(id),
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            rate_limit_tier TEXT DEFAULT 'default'
        )
    """)

    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    conn.commit()
