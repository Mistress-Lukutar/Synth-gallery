"""Alembic migration environment for Synth Gallery.

The engine is obtained from app.database.get_engine(), which honours the
SYNTH_DB_PATH environment variable (and the test suite's per-test path
patching), so no URL is configured in alembic.ini.

Migrations intentionally run WITHOUT an enclosing transaction: the SQLite
driver's transaction semantics (implicit commit around DDL, PRAGMA
no-ops inside transactions) are what the table-rebuild helpers rely on.
"""
from alembic import context

from app.database import get_engine


def run_migrations_online() -> None:
    """Run migrations against the live engine."""
    engine = get_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            compare_type=True,
        )
        context.run_migrations()
        connection.commit()


run_migrations_online()
