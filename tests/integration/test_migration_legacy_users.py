"""Regression tests for upgrading pre-Alembic legacy databases.

Databases created before the Alembic baseline carry relic columns the
baseline no longer declares (e.g. ``users.default_folder_id`` from the
pre-user_settings era). Revision 0004 rebuilds the ``users`` table and must
preserve such columns instead of crashing on the copy INSERT
("table users_new has no column named default_folder_id").
"""
import sqlite3

from alembic import command
from alembic.config import Config

import app.database as db_module
from app.config import BASE_DIR


def _alembic_cfg() -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_module.MIGRATIONS_DIR))
    return cfg


def test_upgrade_preserves_legacy_users_columns(tmp_path, monkeypatch):
    """A users table with relic columns upgrades cleanly to head."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(db_module, "DATABASE_PATH", db_path)

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0003")

    # Simulate a pre-Alembic database: relic column plus a live row.
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE users ADD COLUMN default_folder_id TEXT")
    conn.execute(
        "INSERT INTO users (username, password_hash, password_salt,"
        " display_name, default_folder_id)"
        " VALUES ('legacy', 'hash', '', 'Legacy User', 'folder-1')"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "password_salt" not in columns
        assert "default_folder_id" in columns

        row = conn.execute(
            "SELECT * FROM users WHERE username = 'legacy'"
        ).fetchone()
        assert row["display_name"] == "Legacy User"
        assert row["default_folder_id"] == "folder-1"

        # head was reached: the note detail table exists.
        assert conn.execute(
            "SELECT 1 FROM sqlite_master"
            " WHERE type = 'table' AND name = 'item_texts'"
        ).fetchone()
        conn.close()
    finally:
        # Release the engine's file handle so tmp_path cleanup works.
        db_module.get_engine().dispose()
