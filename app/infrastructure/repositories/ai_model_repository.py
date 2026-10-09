"""AI model repository - per-provider model catalogue.

``ai_models`` has a composite primary key ``(provider_id, model_id)`` and
is refreshed wholesale from the provider's API; per-user adjustments
(pinned flag, manually overridden limits) are merged back by the service
layer before a replace.
"""
from typing import Optional

from .base import Repository


class AiModelRepository(Repository):
    """Repository for ``ai_models`` rows."""

    _INSERT_COLUMNS = (
        "model_id",
        "display_name",
        "supports_tools",
        "supports_vision",
        "is_pinned",
        "context_tokens",
        "max_output_tokens",
        "limits_source",
    )

    def replace_for_provider(self, provider_id: int, models: list[dict]) -> None:
        """Replace the whole model catalogue of a provider.

        Deletes every cached model for the provider and bulk-inserts the
        given list in a single transaction. Missing keys default like the
        table columns do (empty display name, flags off, NULL limits).

        Args:
            provider_id: Provider ID.
            models: List of model dicts; recognised keys are
                ``model_id``, ``display_name``, ``supports_tools``,
                ``supports_vision``, ``is_pinned``, ``context_tokens``,
                ``max_output_tokens`` and ``limits_source``.
        """
        self._execute(
            "DELETE FROM ai_models WHERE provider_id = ?",
            (provider_id,),
        )
        if models:
            self._execute_many(
                """INSERT INTO ai_models
                   (provider_id, model_id, display_name, supports_tools,
                    supports_vision, is_pinned, context_tokens,
                    max_output_tokens, limits_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        provider_id,
                        model["model_id"],
                        model.get("display_name", ""),
                        1 if model.get("supports_tools") else 0,
                        1 if model.get("supports_vision") else 0,
                        1 if model.get("is_pinned") else 0,
                        model.get("context_tokens"),
                        model.get("max_output_tokens"),
                        model.get("limits_source"),
                    )
                    for model in models
                ],
            )
        self._commit()

    def list_for_provider(self, provider_id: int) -> list[dict]:
        """List the models cached for a provider.

        Args:
            provider_id: Provider ID.

        Returns:
            List of model dicts, pinned models first, then by display name.
        """
        cursor = self._execute(
            "SELECT * FROM ai_models WHERE provider_id = ? "
            "ORDER BY is_pinned DESC, display_name, model_id",
            (provider_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_for_user(self, user_id: int) -> list[dict]:
        """List all models across the user's providers.

        Args:
            user_id: Owner's user ID.

        Returns:
            List of model dicts enriched with ``provider_label`` and
            ``protocol`` from the owning provider, ordered by provider
            label then display name.
        """
        cursor = self._execute(
            """SELECT m.provider_id, m.model_id, m.display_name,
                      m.supports_tools, m.supports_vision, m.is_pinned,
                      m.context_tokens, m.max_output_tokens, m.limits_source,
                      p.label AS provider_label, p.protocol
               FROM ai_models m
               JOIN ai_providers p ON p.id = m.provider_id
               WHERE p.user_id = ?
               ORDER BY p.label, m.display_name, m.model_id""",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get(self, provider_id: int, model_id: str) -> Optional[dict]:
        """Get a single cached model.

        Args:
            provider_id: Provider ID.
            model_id: Provider-specific model identifier.

        Returns:
            Model dict or None if not found.
        """
        cursor = self._execute(
            "SELECT * FROM ai_models WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id),
        )
        return self._row_to_dict(cursor.fetchone())

    def update_limits(
        self,
        provider_id: int,
        model_id: str,
        context_tokens: Optional[int],
        max_output_tokens: Optional[int],
        limits_source: Optional[str],
    ) -> bool:
        """Overwrite the stored limits of one model.

        Args:
            provider_id: Provider ID.
            model_id: Provider-specific model identifier.
            context_tokens: Context window in tokens, or None (unknown).
            max_output_tokens: Max output tokens, or None (unknown).
            limits_source: ``"manual"`` when the caller set either limit
                by hand, None for provider-reported values.

        Returns:
            True if a row was updated.
        """
        cursor = self._execute(
            "UPDATE ai_models SET context_tokens = ?, max_output_tokens = ?, "
            "limits_source = ? WHERE provider_id = ? AND model_id = ?",
            (
                context_tokens,
                max_output_tokens,
                limits_source,
                provider_id,
                model_id,
            ),
        )
        self._commit()
        return cursor.rowcount > 0

    def set_pinned(self, provider_id: int, model_id: str, pinned: bool) -> bool:
        """Pin or unpin a model.

        Args:
            provider_id: Provider ID.
            model_id: Provider-specific model identifier.
            pinned: New pinned state.

        Returns:
            True if a row was updated.
        """
        cursor = self._execute(
            "UPDATE ai_models SET is_pinned = ? "
            "WHERE provider_id = ? AND model_id = ?",
            (1 if pinned else 0, provider_id, model_id),
        )
        self._commit()
        return cursor.rowcount > 0
