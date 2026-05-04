"""AI API Key repository - manages API keys for AI agents."""
from datetime import datetime
from typing import Optional, List

from .base import Repository


class AiApiKeyRepository(Repository):
    """Repository for AI API key management.

    API keys are scoped to specific users for security isolation.
    Only bcrypt hashes are stored; plaintext keys are shown once on creation.
    """

    def create(
        self,
        name: str,
        key_hash: str,
        user_id: int,
        created_by: int,
        expires_at: Optional[datetime] = None,
        rate_limit_tier: str = "default"
    ) -> int:
        """Create a new API key.

        Args:
            name: Human-readable name for the key
            key_hash: Bcrypt hash of the plaintext key
            user_id: User this key acts on behalf of
            created_by: Admin user who created the key
            expires_at: Optional expiration timestamp
            rate_limit_tier: Rate limiting tier

        Returns:
            ID of the created key
        """
        cursor = self._execute(
            """INSERT INTO ai_api_keys
               (name, key_hash, user_id, created_by, expires_at, rate_limit_tier, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (name, key_hash, user_id, created_by, expires_at, rate_limit_tier)
        )
        self._commit()
        return cursor.lastrowid

    def list_all(self) -> List[dict]:
        """List all API keys with user info.

        Returns:
            List of key dicts (without key_hash)
        """
        cursor = self._execute(
            """SELECT k.id, k.name, k.is_active, k.created_at, k.expires_at,
                      k.last_used_at, k.rate_limit_tier,
                      k.user_id, u.username as user_username, u.display_name as user_display_name,
                      k.created_by, c.username as created_by_username
               FROM ai_api_keys k
               JOIN users u ON k.user_id = u.id
               JOIN users c ON k.created_by = c.id
               ORDER BY k.created_at DESC"""
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def list_for_user(self, user_id: int) -> List[dict]:
        """List API keys for a specific user.

        Returns:
            List of active key dicts
        """
        cursor = self._execute(
            """SELECT id, name, is_active, created_at, expires_at, last_used_at, rate_limit_tier
               FROM ai_api_keys
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_by_id(self, key_id: int) -> Optional[dict]:
        """Get API key by ID.

        Returns:
            Key dict or None
        """
        cursor = self._execute(
            """SELECT k.id, k.name, k.key_hash, k.is_active, k.created_at,
                      k.expires_at, k.last_used_at, k.rate_limit_tier,
                      k.user_id, u.username as user_username, u.display_name as user_display_name
               FROM ai_api_keys k
               JOIN users u ON k.user_id = u.id
               WHERE k.id = ?""",
            (key_id,)
        )
        return self._row_to_dict(cursor.fetchone())

    def get_by_hash(self, key_hash: str) -> Optional[dict]:
        """Get API key by hash.

        Returns:
            Key dict or None
        """
        cursor = self._execute(
            """SELECT k.id, k.name, k.is_active, k.user_id, k.rate_limit_tier
               FROM ai_api_keys k
               WHERE k.key_hash = ?""",
            (key_hash,)
        )
        return self._row_to_dict(cursor.fetchone())

    def delete(self, key_id: int) -> bool:
        """Delete API key.

        Args:
            key_id: Key ID

        Returns:
            True if key existed and was deleted
        """
        cursor = self._execute(
            "DELETE FROM ai_api_keys WHERE id = ?",
            (key_id,)
        )
        self._commit()
        return cursor.rowcount > 0

    def set_active(self, key_id: int, is_active: bool) -> bool:
        """Enable or disable API key.

        Args:
            key_id: Key ID
            is_active: New active state

        Returns:
            True if key was found and updated
        """
        cursor = self._execute(
            "UPDATE ai_api_keys SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, key_id)
        )
        self._commit()
        return cursor.rowcount > 0

    def update_last_used(self, key_id: int) -> bool:
        """Update last_used_at timestamp.

        Args:
            key_id: Key ID

        Returns:
            True if key was found
        """
        cursor = self._execute(
            "UPDATE ai_api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (key_id,)
        )
        self._commit()
        return cursor.rowcount > 0
