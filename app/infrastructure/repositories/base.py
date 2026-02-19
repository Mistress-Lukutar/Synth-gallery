"""Base repository protocol and utilities.

This module defines the interface that all repositories must implement.
"""
import sqlite3


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
    
    def __init__(self, connection: sqlite3.Connection):
        """Initialize repository with database connection.
        
        Args:
            connection: Database connection (sqlite3.Connection)
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
