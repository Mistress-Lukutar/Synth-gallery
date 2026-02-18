"""Session repository - handles all session-related database operations.

Sessions are temporary authentication tokens for logged-in users.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional
from .base import Repository, AsyncRepository


class SessionRepository(Repository):
    """Repository for session management.
    
    Sessions track logged-in users via secure tokens stored in cookies.
    
    Examples:
        >>> repo = SessionRepository(db)
        >>> session_id = repo.create(1, expires_hours=24)
        >>> session = repo.get_valid(session_id)
        >>> repo.delete(session_id)  # logout
    """
    
    def create(self, user_id: int, expires_hours: int = 24 * 7) -> str:
        """Create new session for user.
        
        Args:
            user_id: User ID to create session for
            expires_hours: Session lifetime in hours (default: 7 days)
            
        Returns:
            Secure random session ID
        """
        session_id = secrets.token_urlsafe(32)
        
        self._execute(
            """INSERT INTO sessions (id, user_id, expires_at) 
               VALUES (?, ?, datetime('now', '+' || ? || ' hours'))""",
            (session_id, user_id, expires_hours)
        )
        self._commit()
        return session_id
    
    def get_valid(self, session_id: str) -> dict | None:
        """Get session if valid (not expired).
        
        Args:
            session_id: Session ID from cookie
            
        Returns:
            Session dict with user info, or None if invalid/expired
        """
        cursor = self._execute(
            """SELECT s.*, u.username, u.display_name 
               FROM sessions s 
               JOIN users u ON s.user_id = u.id 
               WHERE s.id = ? AND s.expires_at > datetime('now')""",
            (session_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_id(self, session_id: str) -> dict | None:
        """Get session by ID (even if expired).
        
        Args:
            session_id: Session ID
            
        Returns:
            Session dict or None
        """
        cursor = self._execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def delete(self, session_id: str) -> bool:
        """Delete session (logout).
        
        Args:
            session_id: Session ID to delete
            
        Returns:
            True if session existed and was deleted
        """
        cursor = self._execute(
            "DELETE FROM sessions WHERE id = ?",
            (session_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete_all_for_user(self, user_id: int) -> int:
        """Delete all sessions for user (force logout everywhere).
        
        Args:
            user_id: User ID
            
        Returns:
            Number of sessions deleted
        """
        cursor = self._execute(
            "DELETE FROM sessions WHERE user_id = ?",
            (user_id,)
        )
        self._commit()
        return cursor.rowcount
    
    def cleanup_expired(self) -> int:
        """Delete all expired sessions.
        
        Returns:
            Number of sessions cleaned up
        """
        cursor = self._execute(
            "DELETE FROM sessions WHERE expires_at <= datetime('now')"
        )
        self._commit()
        return cursor.rowcount
    
    def extend(self, session_id: str, additional_hours: int) -> bool:
        """Extend session expiration.
        
        Args:
            session_id: Session ID
            additional_hours: Hours to add to expiration
            
        Returns:
            True if session was found and extended
        """
        cursor = self._execute(
            """UPDATE sessions 
               SET expires_at = datetime(expires_at, '+' || ? || ' hours')
               WHERE id = ? AND expires_at > datetime('now')""",
            (additional_hours, session_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def list_active_for_user(self, user_id: int) -> list[dict]:
        """List all active sessions for user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of active session dicts
        """
        cursor = self._execute(
            """SELECT id, created_at, expires_at 
               FROM sessions 
               WHERE user_id = ? AND expires_at > datetime('now')
               ORDER BY created_at DESC""",
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def count_active(self) -> int:
        """Count total active sessions.
        
        Returns:
            Number of active sessions
        """
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM sessions WHERE expires_at > datetime('now')"
        )
        row = cursor.fetchone()
        return row["count"] if row else 0


# =============================================================================
# ASYNC VERSION (Issue #15)
# =============================================================================

class AsyncSessionRepository(AsyncRepository):
    """Async repository for session management.
    
    Sessions track logged-in users via secure tokens stored in cookies.
    
    Examples:
        >>> async def create_session():
        ...     async with get_async_db() as conn:
        ...         repo = AsyncSessionRepository(conn)
        ...         return await repo.create(1, expires_hours=24)
    """
    
    async def create(self, user_id: int, expires_hours: int = 24 * 7) -> str:
        """Create new session for user asynchronously.
        
        Args:
            user_id: User ID to create session for
            expires_hours: Session lifetime in hours (default: 7 days)
            
        Returns:
            Secure random session ID
        """
        session_id = secrets.token_urlsafe(32)
        
        await self._execute(
            """INSERT INTO sessions (id, user_id, expires_at) 
               VALUES (?, ?, datetime('now', '+' || ? || ' hours'))""",
            (session_id, user_id, expires_hours)
        )
        await self._commit()
        return session_id
    
    async def get_valid(self, session_id: str) -> dict | None:
        """Get session if valid (not expired) asynchronously.
        
        Args:
            session_id: Session ID from cookie
            
        Returns:
            Session dict with user info, or None if invalid/expired
        """
        return await self._fetchone(
            """SELECT s.*, u.username, u.display_name 
               FROM sessions s 
               JOIN users u ON s.user_id = u.id 
               WHERE s.id = ? AND s.expires_at > datetime('now')""",
            (session_id,)
        )
    
    async def get_by_id(self, session_id: str) -> dict | None:
        """Get session by ID (even if expired) asynchronously.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session dict or None
        """
        return await self._fetchone(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        )
    
    async def delete(self, session_id: str) -> bool:
        """Delete session (logout) asynchronously.
        
        Args:
            session_id: Session ID to delete
            
        Returns:
            True if session existed and was deleted
        """
        cursor = await self._execute(
            "DELETE FROM sessions WHERE id = ?",
            (session_id,)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def delete_all_for_user(self, user_id: int) -> int:
        """Delete all sessions for user (force logout everywhere) asynchronously.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of sessions deleted
        """
        cursor = await self._execute(
            "DELETE FROM sessions WHERE user_id = ?",
            (user_id,)
        )
        await self._commit()
        return cursor.rowcount
    
    async def cleanup_expired(self) -> int:
        """Delete all expired sessions asynchronously.
        
        Returns:
            Number of sessions cleaned up
        """
        cursor = await self._execute(
            "DELETE FROM sessions WHERE expires_at <= datetime('now')"
        )
        await self._commit()
        return cursor.rowcount
    
    async def extend(self, session_id: str, additional_hours: int) -> bool:
        """Extend session expiration asynchronously.
        
        Args:
            session_id: Session ID
            additional_hours: Hours to add to expiration
            
        Returns:
            True if session was found and extended
        """
        cursor = await self._execute(
            """UPDATE sessions 
               SET expires_at = datetime(expires_at, '+' || ? || ' hours')
               WHERE id = ? AND expires_at > datetime('now')""",
            (additional_hours, session_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def list_active_for_user(self, user_id: int) -> list[dict]:
        """List all active sessions for user asynchronously.
        
        Args:
            user_id: User ID
            
        Returns:
            List of active session dicts
        """
        return await self._fetchall(
            """SELECT id, created_at, expires_at 
               FROM sessions 
               WHERE user_id = ? AND expires_at > datetime('now')
               ORDER BY created_at DESC""",
            (user_id,)
        )
    
    async def count_active(self) -> int:
        """Count total active sessions asynchronously.
        
        Returns:
            Number of active sessions
        """
        result = await self._fetchone(
            "SELECT COUNT(*) as count FROM sessions WHERE expires_at > datetime('now')"
        )
        return result["count"] if result else 0
