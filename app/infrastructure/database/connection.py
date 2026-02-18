"""Async database connection management.

Provides async database connectivity using aiosqlite.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio

import aiosqlite

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATABASE_PATH = BASE_DIR / "gallery.db"


# =============================================================================
# SQLite3 datetime adapter (Python 3.12 compatibility)
# =============================================================================
# Note: These are also registered in app.database, but we register them here
# as well to ensure they're available when this module is imported first.
def _adapt_datetime(dt: datetime) -> str:
    """Adapt datetime to ISO 8601 string for SQLite."""
    return dt.isoformat()

def _convert_datetime(val: bytes) -> datetime:
    """Convert ISO 8601 string from SQLite to datetime."""
    return datetime.fromisoformat(val.decode())

sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("DATETIME", _convert_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)

# Global connection pool reference
_pool: Optional['AsyncConnectionPool'] = None


class AsyncConnectionPool:
    """Simple async connection pool for aiosqlite.
    
    aiosqlite doesn't require a traditional pool since connections
can be shared across coroutines, but we provide a pool interface
    for compatibility and future optimization.
    """
    
    def __init__(self, db_path: Path, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self._connections: list[aiosqlite.Connection] = []
        self._semaphore = asyncio.Semaphore(max_connections)
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> aiosqlite.Connection:
        """Acquire a connection from the pool."""
        async with self._semaphore:
            async with self._lock:
                # Return existing connection if available
                if self._connections:
                    return self._connections.pop()
            
            # Create new connection
            conn = await aiosqlite.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            conn.row_factory = aiosqlite.Row
            return conn
    
    async def release(self, conn: aiosqlite.Connection) -> None:
        """Release a connection back to the pool."""
        async with self._lock:
            if len(self._connections) < self.max_connections:
                self._connections.append(conn)
            else:
                await conn.close()
    
    async def close_all(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            for conn in self._connections:
                await conn.close()
            self._connections.clear()


async def get_async_db() -> aiosqlite.Connection:
    """Get async database connection.
    
    Returns:
        Async database connection
    """
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(DATABASE_PATH)
    return await _pool.acquire()


async def release_async_db(conn: aiosqlite.Connection) -> None:
    """Release async database connection back to pool.
    
    Args:
        conn: Connection to release
    """
    global _pool
    if _pool:
        await _pool.release(conn)


async def init_async_db() -> None:
    """Initialize database schema using async connection."""
    conn = await get_async_db()
    try:
        # Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Sessions table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # WebAuthn credentials
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS webauthn_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                credential_id BLOB NOT NULL UNIQUE,
                public_key BLOB NOT NULL,
                sign_count INTEGER DEFAULT 0,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                encrypted_dek BLOB,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Folders table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Folder permissions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS folder_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL CHECK(permission IN ('viewer', 'editor')),
                granted_by INTEGER NOT NULL,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (granted_by) REFERENCES users(id),
                UNIQUE(folder_id, user_id)
            )
        """)
        
        # Albums table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS albums (
                id TEXT PRIMARY KEY,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Photos table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                folder_id TEXT,
                album_id TEXT,
                user_id INTEGER NOT NULL,
                taken_at TIMESTAMP,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                title TEXT,
                description TEXT,
                width INTEGER,
                height INTEGER,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL,
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Album photos junction
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS album_photos (
                album_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (album_id, photo_id),
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
                FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
            )
        """)
        
        # Safes table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS safes (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL UNIQUE,
                encrypted_dek BLOB NOT NULL,
                dek_nonce BLOB NOT NULL,
                dek_salt BLOB NOT NULL,
                password_enabled BOOLEAN DEFAULT FALSE,
                hardware_key_enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
            )
        """)
        
        await conn.commit()
    finally:
        await release_async_db(conn)


async def close_async_db() -> None:
    """Close all async database connections."""
    global _pool
    if _pool:
        await _pool.close_all()
        _pool = None
