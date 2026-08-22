'''
File:   database.py
Brief:  SQLAlchemy engine, sqlite3-compatible connection adapter and
        Alembic-driven schema migrations.
Author: Mistress-Lukutar
Date:   2026-08-21
'''
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import bcrypt
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

BASE_DIR = Path(__file__).resolve().parent.parent
# Overridable via SYNTH_DB_PATH so the test suite can run against a
# throwaway database instead of the production gallery.db.
DATABASE_PATH = Path(os.environ.get("SYNTH_DB_PATH", str(BASE_DIR / "gallery.db")))

MIGRATIONS_DIR = BASE_DIR / "migrations"


# =============================================================================
# SQLite3 datetime adapter (Python 3.12 compatibility)
# =============================================================================
def _adapt_datetime(dt: datetime) -> str:
    """Adapt datetime to ISO 8601 string for SQLite."""
    return dt.isoformat()


def _convert_datetime(val: bytes) -> datetime:
    """Convert ISO 8601 string from SQLite to datetime."""
    return datetime.fromisoformat(val.decode())


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("DATETIME", _convert_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)


# =============================================================================
# Password Hashing
# =============================================================================
def verify_password(password: str, hashed: str, salt: str = None) -> bool:
    """Verify password against bcrypt hash."""
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    return False


# =============================================================================
# Engine management
# =============================================================================
_engine: Engine | None = None
_engine_key: str | None = None
_engine_lock = threading.Lock()


def _apply_connection_pragmas(dbapi_connection, connection_record) -> None:
    """Enable foreign-key enforcement on every pooled connection.

    SQLite disables FK constraints by default; the schema relies on
    ``ON DELETE CASCADE`` / ``SET NULL`` declared on its tables.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def get_engine() -> Engine:
    """Return the process-wide engine, rebuilding it if DATABASE_PATH changed.

    The cache is keyed by the resolved database path so the test suite can
    point the module at a fresh per-test file and get a matching engine.
    """
    global _engine, _engine_key
    key = str(DATABASE_PATH)
    with _engine_lock:
        if _engine is None or _engine_key != key:
            if _engine is not None:
                _engine.dispose()
            _engine = create_engine(
                f"sqlite:///{DATABASE_PATH}",
                connect_args={
                    # Preserve the TIMESTAMP<->datetime converters registered
                    # above (pysqlite applies them via cursor.description).
                    "detect_types": sqlite3.PARSE_DECLTYPES,
                    # Repositories share connections across threads via the
                    # thread-local get_db(); the engine pool serialises actual
                    # use, so relax sqlite3's same-thread guard.
                    "check_same_thread": False,
                },
            )
            event.listen(_engine, "connect", _apply_connection_pragmas)
            _engine_key = key
    return _engine


# =============================================================================
# sqlite3-compatible connection adapter
# =============================================================================
def _to_named(sql: str, parameters) -> tuple[str, dict | list]:
    """Convert ``?`` placeholders and positional params for text() binds.

    SQLAlchemy's ``text()`` construct requires named ``:param`` binds, while
    the codebase historically uses sqlite3 ``?`` placeholders. The rewrite is
    purely positional (each ``?`` becomes ``:p<N>`` in order); string literals
    containing ``?`` are not used anywhere in the codebase's SQL.
    """
    if parameters is None or (isinstance(parameters, (tuple, list)) and not parameters):
        return sql, {}
    if isinstance(parameters, dict):
        return sql, parameters

    names: list[str] = []
    out: list[str] = []
    index = 0
    for ch in sql:
        if ch == "?":
            name = f"p{index}"
            index += 1
            names.append(name)
            out.append(f":{name}")
        else:
            out.append(ch)
    converted = "".join(out)

    if parameters and isinstance(parameters[0], (tuple, list, dict)):
        # executemany-style list of parameter sequences
        param_list = []
        for seq in parameters:
            if isinstance(seq, dict):
                param_list.append(seq)
            else:
                param_list.append(dict(zip(names, seq)))
        return converted, param_list
    return converted, dict(zip(names, parameters))


class Result:
    """Cursor-like wrapper over a SQLAlchemy CursorResult.

    Rows are exposed as Mappings so legacy ``row['col']`` access keeps
    working; ``rowcount`` and ``lastrowid`` mirror the sqlite3 cursor API.
    """

    __slots__ = ("_result", "_sa")

    def __init__(self, result, sa_connection: Connection):
        self._result = result
        self._sa = sa_connection

    @property
    def rowcount(self) -> int:
        return self._result.rowcount

    @property
    def lastrowid(self) -> int:
        """ID generated by the most recent INSERT on this connection."""
        return self._sa.exec_driver_sql("SELECT last_insert_rowid()").scalar()

    def fetchone(self):
        return self._result.mappings().first()

    def fetchall(self):
        return self._result.mappings().all()

    def __iter__(self):
        return iter(self._result.mappings())


class DbConnection:
    """sqlite3-compatible façade over a SQLAlchemy Connection.

    Keeps the calling convention the repositories were written against
    (``execute``/``executemany``/``commit``/``close``/``total_changes``)
    while every statement is compiled through SQLAlchemy Core ``text()``
    with bound parameters.
    """

    def __init__(self, sa_connection: Connection):
        self._sa = sa_connection
        self._total_changes = 0

    # -- statement execution -------------------------------------------------

    def execute(self, sql: str, parameters=()) -> Result:
        converted, params = _to_named(sql, parameters)
        try:
            result = self._sa.execute(text(converted), params)
        except SQLAlchemyError as exc:
            raise self._unwrap(exc)
        if result.rowcount and result.rowcount > 0:
            self._total_changes += result.rowcount
        return Result(result, self._sa)

    def executemany(self, sql: str, parameters_list) -> Result:
        converted, params = _to_named(sql, parameters_list)
        try:
            result = self._sa.execute(text(converted), params)
        except SQLAlchemyError as exc:
            raise self._unwrap(exc)
        if result.rowcount and result.rowcount > 0:
            self._total_changes += result.rowcount
        return Result(result, self._sa)

    @staticmethod
    def _unwrap(exc: SQLAlchemyError) -> Exception:
        """Surface the underlying DBAPI exception (sqlite3.IntegrityError etc.).

        Callers throughout the codebase catch the native sqlite3 exceptions
        (locked database, constraint violations); SQLAlchemy wraps them, so
        re-raise the original to keep those handlers working.
        """
        orig = getattr(exc, "orig", None)
        return orig if orig is not None else exc

    # -- transaction / lifecycle ----------------------------------------------

    def commit(self) -> None:
        self._sa.commit()

    def rollback(self) -> None:
        self._sa.rollback()

    def close(self) -> None:
        self._sa.close()

    @property
    def total_changes(self) -> int:
        """Cumulative number of rows modified through this connection."""
        return self._total_changes

    @property
    def in_transaction(self) -> bool:
        return self._sa.in_transaction()

    # -- escape hatch for infrastructure code ---------------------------------

    @property
    def sa_connection(self) -> Connection:
        """The underlying SQLAlchemy Connection (for text()/exec_driver_sql)."""
        return self._sa

    def exec_driver_sql(self, statement: str, parameters=()):
        """Execute raw DBAPI SQL (``?`` placeholders) on the connection."""
        return self._sa.exec_driver_sql(statement, parameters)


# =============================================================================
# Database Connection
# =============================================================================
_local = threading.local()


def get_db() -> DbConnection:
    """Get thread-local database connection.

    WARNING: Do NOT close this connection! It's reused across the thread.
    For contexts where you need to close the connection, use create_connection().
    """
    conn = getattr(_local, "connection", None)
    if conn is None or conn._sa.closed:
        conn = DbConnection(get_engine().connect())
        _local.connection = conn
    return conn


def create_connection() -> DbConnection:
    """Create a new database connection.

    Use this when you need a connection that you can safely close.
    Always close this connection when done using it.

    Example:
        db = create_connection()
        try:
            repo = UserRepository(db)
            user = repo.get_by_id(1)
        finally:
            db.close()

    Returns:
        New DbConnection backed by a pooled SQLAlchemy connection
    """
    return DbConnection(get_engine().connect())


def cleanup_expired_sessions():
    """Remove expired sessions from database."""
    from .infrastructure.repositories import SessionRepository
    SessionRepository(create_connection()).cleanup_expired()


# =============================================================================
# Schema migrations (Alembic)
# =============================================================================
def _deactivate_legacy_api_keys(db: DbConnection) -> None:
    """Deactivate API keys whose hash predates the bcrypt switch.

    Legacy keys were stored as plain SHA-256 hex digests; verification only
    supports bcrypt, so such keys can never authenticate again. They are
    deactivated (not deleted) to keep the audit trail visible in the admin
    UI. Idempotent: inactive keys are left alone.
    """
    cursor = db.execute(
        "SELECT id, name FROM ai_api_keys "
        "WHERE is_active = 1 "
        "AND key_hash NOT LIKE '$2b$%' AND key_hash NOT LIKE '$2a$%'"
    )
    legacy = cursor.fetchall()
    if not legacy:
        return
    db.execute(
        "UPDATE ai_api_keys SET is_active = 0 "
        "WHERE is_active = 1 "
        "AND key_hash NOT LIKE '$2b$%' AND key_hash NOT LIKE '$2a$%'"
    )
    logger = logging.getLogger(__name__)
    for row in legacy:
        logger.warning(
            "Deactivated legacy SHA-256 API key '%s' (id=%s); "
            "bcrypt is required - create a new key in the admin UI",
            row["name"],
            row["id"],
        )


def run_db_migrations() -> None:
    """Apply all pending Alembic revisions up to head."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BASE_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    command.upgrade(alembic_cfg, "head")


def _seed_default_admin(db: DbConnection) -> None:
    """Create the temporary default admin account on an empty database."""
    cursor = db.execute("SELECT COUNT(*) as count FROM users")
    if cursor.fetchone()["count"] == 0:
        default_username = "admin"
        default_password = "admin"

        hashed = bcrypt.hashpw(default_password.encode('utf-8'), bcrypt.gensalt())

        db.execute(
            """INSERT INTO users
               (username, password_hash, display_name, is_admin)
               VALUES (?, ?, ?, ?)""",
            (default_username, hashed.decode('utf-8'), "Administrator", 1)
        )
        db.commit()

        print("=" * 70)
        print("FIRST RUN: Default admin account created")
        print("=" * 70)
        print(f"   Username: {default_username}")
        print(f"   Password: {default_password}")
        print("")
        print("   Please log in and create a new admin user immediately,")
        print("   then delete this temporary account for security.")
        print("=" * 70)


def init_db():
    """Initialize database schema and run startup data fixups."""
    run_db_migrations()

    db = get_db()
    _seed_default_admin(db)
    _deactivate_legacy_api_keys(db)
    db.commit()
