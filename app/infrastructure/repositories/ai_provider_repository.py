"""AI provider repository - per-user LLM provider endpoints.

Providers carry the user's API key as an SGE1-encrypted blob (encrypted
with the owner's DEK); this repository only stores and returns the blob.
Every query is scoped by ``user_id`` so ownership is enforced at the
storage layer.
"""
from typing import Optional

from .base import Repository


class AiProviderRepository(Repository):
    """Repository for ``ai_providers`` rows."""

    # Columns that update() may write; never user_id/id.
    _UPDATABLE_FIELDS = ("label", "protocol", "base_url", "api_key_encrypted")

    def create(
        self,
        user_id: int,
        label: str,
        protocol: str,
        base_url: str,
        api_key_encrypted: bytes,
    ) -> int:
        """Create a provider for a user.

        Args:
            user_id: Owner's user ID.
            label: Display label.
            protocol: API protocol ('openai_compatible', 'anthropic',
                'google_gemini').
            base_url: API base URL.
            api_key_encrypted: SGE1-encrypted API key blob.

        Returns:
            New provider ID.
        """
        cursor = self._execute(
            """INSERT INTO ai_providers
               (user_id, label, protocol, base_url, api_key_encrypted)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, label, protocol, base_url, api_key_encrypted),
        )
        self._commit()
        return cursor.lastrowid

    def get_by_id(self, provider_id: int, user_id: int) -> Optional[dict]:
        """Get a single provider owned by a user.

        Args:
            provider_id: Provider ID.
            user_id: Owner's user ID.

        Returns:
            Provider dict or None if not found (or not owned by the user).
        """
        cursor = self._execute(
            "SELECT * FROM ai_providers WHERE id = ? AND user_id = ?",
            (provider_id, user_id),
        )
        return self._row_to_dict(cursor.fetchone())

    def list_for_user(self, user_id: int) -> list[dict]:
        """List all providers owned by a user.

        Args:
            user_id: Owner's user ID.

        Returns:
            List of provider dicts ordered by label.
        """
        cursor = self._execute(
            "SELECT * FROM ai_providers WHERE user_id = ? ORDER BY label, id",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update(self, provider_id: int, user_id: int, fields: dict) -> bool:
        """Update selected fields of a provider.

        Args:
            provider_id: Provider ID.
            user_id: Owner's user ID.
            fields: Dict of fields to set; allowed keys are ``label``,
                ``protocol``, ``base_url`` and ``api_key_encrypted``.
                Unknown keys are ignored.

        Returns:
            True if a row was updated.
        """
        clauses = []
        params: list = []
        for key in self._UPDATABLE_FIELDS:
            if key in fields:
                clauses.append(f"{key} = ?")
                params.append(fields[key])
        if not clauses:
            return False
        params.append(provider_id)
        params.append(user_id)
        cursor = self._execute(
            f"UPDATE ai_providers SET {', '.join(clauses)} "
            "WHERE id = ? AND user_id = ?",
            tuple(params),
        )
        self._commit()
        return cursor.rowcount > 0

    def delete(self, provider_id: int, user_id: int) -> bool:
        """Delete a provider owned by a user.

        Args:
            provider_id: Provider ID.
            user_id: Owner's user ID.

        Returns:
            True if a row was deleted.
        """
        cursor = self._execute(
            "DELETE FROM ai_providers WHERE id = ? AND user_id = ?",
            (provider_id, user_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def count_models(self, provider_id: int) -> int:
        """Count the models cached for a provider.

        Args:
            provider_id: Provider ID.

        Returns:
            Number of rows in ``ai_models`` for the provider.
        """
        cursor = self._execute(
            "SELECT COUNT(*) AS model_count FROM ai_models WHERE provider_id = ?",
            (provider_id,),
        )
        row = cursor.fetchone()
        return row["model_count"] if row else 0
