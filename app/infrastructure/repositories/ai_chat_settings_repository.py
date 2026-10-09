"""AI chat settings repository - per-user active provider/model selection.

One row per user (``user_id`` is the primary key). ``active_provider_id``
deliberately has no foreign key: providers can be deleted freely and the
service layer clears the dangling reference application-side via
:meth:`clear_active_provider`.
"""
from .base import Repository


class AiChatSettingsRepository(Repository):
    """Repository for ``ai_chat_settings`` rows."""

    # Columns that update() may write; updated_at is bumped automatically.
    _UPDATABLE_FIELDS = ("active_provider_id", "active_model_id", "temperature")

    def ensure(self, user_id: int) -> dict:
        """Return the user's settings row, creating a default one if absent.

        Args:
            user_id: Owner's user ID.

        Returns:
            Settings dict (temperature defaults to 0.7, no active selection).
        """
        self._execute(
            "INSERT OR IGNORE INTO ai_chat_settings (user_id) VALUES (?)",
            (user_id,),
        )
        self._commit()
        cursor = self._execute(
            "SELECT * FROM ai_chat_settings WHERE user_id = ?",
            (user_id,),
        )
        return self._row_to_dict(cursor.fetchone())

    def update(self, user_id: int, fields: dict) -> bool:
        """Update selected fields of the user's settings row.

        ``updated_at`` is always bumped on a successful update.

        Args:
            user_id: Owner's user ID.
            fields: Dict of fields to set; allowed keys are
                ``active_provider_id``, ``active_model_id`` and
                ``temperature``. Unknown keys are ignored; ``None`` values
                are written as-is (they clear the selection).

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
        clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.append(user_id)
        cursor = self._execute(
            f"UPDATE ai_chat_settings SET {', '.join(clauses)} WHERE user_id = ?",
            tuple(params),
        )
        self._commit()
        return cursor.rowcount > 0

    def clear_active_provider(self, user_id: int, provider_id: int) -> None:
        """Clear the active selection if it points at the given provider.

        Used when a provider is deleted so the settings row never keeps
        pointing at a dangling provider id.

        Args:
            user_id: Owner's user ID.
            provider_id: Provider ID being removed.
        """
        self._execute(
            "UPDATE ai_chat_settings "
            "SET active_provider_id = NULL, active_model_id = NULL, "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND active_provider_id = ?",
            (user_id, provider_id),
        )
        self._commit()
