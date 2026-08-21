"""Drop the relic users.password_salt column.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-21

The column is a leftover from the SHA-256 password era: bcrypt generates
its own salt, so the value was always the empty string and nothing ever
read it. Removed via table rebuild because other tables hold foreign keys
to ``users`` (a plain DROP COLUMN with FKs enabled would cascade-delete
their rows).
"""
from alembic import op

from migrations.migration_utils import rebuild_table

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    keep = [
        row["name"]
        for row in conn.exec_driver_sql("PRAGMA table_info(users)").mappings().all()
        if row["name"] != "password_salt"
    ]
    rebuild_table(
        conn,
        "users",
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0,
            failed_login_attempts INTEGER DEFAULT 0,
            locked_until TIMESTAMP,
            last_login TIMESTAMP
        )
        """,
        keep,
    )
    conn.commit()


def downgrade() -> None:
    # Restoring a dead column is pointless; recreate it empty if needed.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE users ADD COLUMN password_salt TEXT NOT NULL DEFAULT ''"
    )
