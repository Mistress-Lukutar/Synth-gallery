"""Base repository protocol and utilities.

This module defines the interface that all repositories must implement.
"""
from typing import Protocol, Optional, Any
import sqlite3


class ConnectionProtocol(Protocol):
    """Protocol for database connection."""
    
    def execute(self, sql: str, parameters: tuple = ...) -> sqlite3.Cursor: ...
    def commit(self) -> None: ...


class Repository:
    """Base repository class.
    
    All repositories should inherit from this class.
    
    Example:
        class UserRepository(Repository):
            def get_by_id(self, user_id: int) -> dict | None:
                with self._execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
                    row = cursor.fetchone()
                    return dict(row) if row else None
    """
    
    def __init__(self, connection: ConnectionProtocol):
        """Initialize repository with database connection.
        
        Args:
            connection: Database connection (sqlite3.Connection or compatible)
        """
        self._conn = connection
    
    def _execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL query with parameters.
        
        Args:
            sql: SQL query string
            parameters: Query parameters (prevents SQL injection)
            
        Returns:
            sqlite3.Cursor with results
        """
        return self._conn.execute(sql, parameters)
    
    def _execute_many(self, sql: str, parameters_list: list[tuple]) -> sqlite3.Cursor:
        """Execute SQL query multiple times.
        
        Args:
            sql: SQL query string
            parameters_list: List of parameter tuples
            
        Returns:
            sqlite3.Cursor
        """
        return self._conn.executemany(sql, parameters_list)
    
    def _commit(self) -> None:
        """Commit current transaction."""
        self._conn.commit()
    
    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        """Convert sqlite3.Row to dictionary.
        
        Args:
            row: Database row or None
            
        Returns:
            Dictionary representation or None
        """
        return dict(row) if row else None


# =============================================================================
# ASYNC SUPPORT (Issue #15)
# =============================================================================

try:
    import aiosqlite
    HAS_AIosqlite = True
except ImportError:
    HAS_AIosqlite = False


class AsyncConnectionProtocol(Protocol):
    """Protocol for async database connection."""
    
    async def execute(self, sql: str, parameters: tuple = ...) -> aiosqlite.Cursor: ...
    async def commit(self) -> None: ...


class AsyncRepository:
    """Async base repository class.
    
    Provides async database operations using aiosqlite.
    
    Example:
        class AsyncUserRepository(AsyncRepository):
            async def get_by_id(self, user_id: int) -> dict | None:
                async with self._execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
                    row = await cursor.fetchone()
                    return self._row_to_dict(row)
    """
    
    def __init__(self, connection: AsyncConnectionProtocol):
        """Initialize repository with async database connection.
        
        Args:
            connection: Async database connection (aiosqlite.Connection)
        """
        self._conn = connection
    
    async def _execute(self, sql: str, parameters: tuple = ()) -> aiosqlite.Cursor:
        """Execute SQL query with parameters asynchronously.
        
        Args:
            sql: SQL query string
            parameters: Query parameters (prevents SQL injection)
            
        Returns:
            aiosqlite.Cursor with results
        """
        return await self._conn.execute(sql, parameters)
    
    async def _execute_many(self, sql: str, parameters_list: list[tuple]) -> aiosqlite.Cursor:
        """Execute SQL query multiple times asynchronously.
        
        Args:
            sql: SQL query string
            parameters_list: List of parameter tuples
            
        Returns:
            aiosqlite.Cursor
        """
        return await self._conn.executemany(sql, parameters_list)
    
    async def _commit(self) -> None:
        """Commit current transaction asynchronously."""
        await self._conn.commit()
    
    def _row_to_dict(self, row: aiosqlite.Row | None) -> dict | None:
        """Convert aiosqlite.Row to dictionary.
        
        Args:
            row: Database row or None
            
        Returns:
            Dictionary representation or None
        """
        return dict(row) if row else None
    
    async def _fetchone(self, sql: str, parameters: tuple = ()) -> dict | None:
        """Fetch single row and return as dict.
        
        Args:
            sql: SQL query
            parameters: Query parameters
            
        Returns:
            Dictionary or None
        """
        cursor = await self._execute(sql, parameters)
        row = await cursor.fetchone()
        return self._row_to_dict(row)
    
    async def _fetchall(self, sql: str, parameters: tuple = ()) -> list[dict]:
        """Fetch all rows and return as list of dicts.
        
        Args:
            sql: SQL query
            parameters: Query parameters
            
        Returns:
            List of dictionaries
        """
        cursor = await self._execute(sql, parameters)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
