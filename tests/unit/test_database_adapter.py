"""Unit tests for the SQLAlchemy-backed sqlite3-compatible connection adapter.

The adapter (app.database.DbConnection) is the compatibility layer that
lets repositories keep their historical calling convention
(execute/executemany/commit/total_changes/lastrowid, ``?`` placeholders,
``row['col']`` access, native sqlite3 exceptions) while every statement is
compiled through SQLAlchemy Core.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.database import DbConnection, _to_named


@pytest.fixture
def conn():
    """Adapter over a throwaway in-memory SQLAlchemy engine."""
    engine = create_engine("sqlite:///:memory:")
    sa_conn = engine.connect()
    adapter = DbConnection(sa_conn)
    adapter.exec_driver_sql(
        "CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)"
    )
    adapter.commit()
    yield adapter
    sa_conn.close()
    engine.dispose()


class TestPlaceholderTranslation:
    """``?`` placeholders and positional params become named binds."""

    def test_simple_translation(self):
        sql, params = _to_named(
            "SELECT * FROM t WHERE a = ? AND b = ?", ("x", 1)
        )
        assert sql == "SELECT * FROM t WHERE a = :p0 AND b = :p1"
        assert params == {"p0": "x", "p1": 1}

    def test_no_params(self):
        sql, params = _to_named("SELECT 1", ())
        assert sql == "SELECT 1"
        assert params == {}

    def test_named_params_pass_through(self):
        sql, params = _to_named("SELECT * FROM t WHERE a = :a", {"a": 1})
        assert sql == "SELECT * FROM t WHERE a = :a"
        assert params == {"a": 1}

    def test_executemany_list(self):
        sql, params = _to_named(
            "INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")]
        )
        assert params == [{"p0": 1, "p1": "a"}, {"p0": 2, "p1": "b"}]


class TestAdapterBehaviour:
    """The adapter mirrors the sqlite3 connection API."""

    def test_insert_and_fetch(self, conn):
        cursor = conn.execute(
            "INSERT INTO sample (name) VALUES (?)", ("first",)
        )
        conn.commit()
        assert cursor.lastrowid == 1

        row = conn.execute(
            "SELECT id, name FROM sample WHERE id = ?", (1,)
        ).fetchone()
        assert row["name"] == "first"

    def test_fetchall_mapping_access(self, conn):
        conn.executemany(
            "INSERT INTO sample (name) VALUES (?)",
            [("a",), ("b",), ("c",)],
        )
        conn.commit()
        rows = conn.execute("SELECT id, name FROM sample").fetchall()
        assert [r["name"] for r in rows] == ["a", "b", "c"]

    def test_total_changes_accumulates(self, conn):
        assert conn.total_changes == 0
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("x",))
        conn.commit()
        assert conn.total_changes == 1
        conn.execute("UPDATE sample SET name = ? WHERE id = ?", ("y", 1))
        conn.commit()
        assert conn.total_changes == 2

    def test_rowcount(self, conn):
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("x",))
        conn.commit()
        cursor = conn.execute("DELETE FROM sample WHERE id = ?", (1,))
        conn.commit()
        assert cursor.rowcount == 1

    def test_native_integrity_error_raised(self, conn):
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("dup",))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sample (id, name) VALUES (?, ?)", (1, "dup2")
            )

    def test_rollback(self, conn):
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("gone",))
        conn.rollback()
        row = conn.execute("SELECT COUNT(*) AS c FROM sample").fetchone()
        assert row["c"] == 0
