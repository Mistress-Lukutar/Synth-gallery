'''
File:   ai_agent_repository.py
Brief:  AI agent repository - per-user agent definitions.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

An agent carries a system prompt and an explicit tool allow-list
(``tools_json``). Built-in agents are seeded per user by
:mod:`app.application.services.ai_agent_service` and are upserted by
``builtin_key`` so prompt improvements roll out while row ids stay stable.
Every query is scoped by ``user_id`` so ownership is enforced at the
storage layer.
'''
import json
from typing import Optional

from .base import Repository


class AiAgentRepository(Repository):
    """Repository for ``ai_agents`` rows."""

    # Columns that update() may write; never user_id/id.
    _UPDATABLE_FIELDS = (
        "name",
        "description",
        "system_prompt",
        "icon",
        "color_index",
        "tools_json",
        "sort_order",
    )

    def create(
        self,
        user_id: int,
        name: str,
        description: str = "",
        system_prompt: str = "",
        icon: str = "sparkles",
        color_index: int = 0,
        tools: Optional[list] = None,
        builtin_key: Optional[str] = None,
        is_builtin: bool = False,
        sort_order: int = 0,
    ) -> int:
        """Create an agent for a user.

        Returns:
            New agent ID.
        """
        cursor = self._execute(
            """INSERT INTO ai_agents
               (user_id, builtin_key, name, description, system_prompt,
                icon, color_index, tools_json, is_builtin, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                builtin_key,
                name,
                description,
                system_prompt,
                icon,
                color_index,
                json.dumps(tools or [], ensure_ascii=False),
                1 if is_builtin else 0,
                sort_order,
            ),
        )
        self._commit()
        return cursor.lastrowid

    def get_by_id(self, agent_id: int, user_id: int) -> Optional[dict]:
        """Return a single agent owned by the user, or None."""
        cursor = self._execute(
            "SELECT * FROM ai_agents WHERE id = ? AND user_id = ?",
            (agent_id, user_id),
        )
        return self._parse_row(self._row_to_dict(cursor.fetchone()))

    def get_by_builtin_key(self, user_id: int, builtin_key: str) -> Optional[dict]:
        """Return the user's built-in agent with the given key, or None."""
        cursor = self._execute(
            "SELECT * FROM ai_agents WHERE user_id = ? AND builtin_key = ?",
            (user_id, builtin_key),
        )
        return self._parse_row(self._row_to_dict(cursor.fetchone()))

    def list_for_user(self, user_id: int) -> list[dict]:
        """List the user's agents, ordering built-ins first by sort_order."""
        cursor = self._execute(
            "SELECT * FROM ai_agents WHERE user_id = ? "
            "ORDER BY sort_order ASC, id ASC",
            (user_id,),
        )
        return [self._parse_row(dict(row)) for row in cursor.fetchall()]

    def update(self, agent_id: int, user_id: int, fields: dict) -> bool:
        """Update selected fields of an agent; bumps ``updated_at``.

        Allowed keys are listed in ``_UPDATABLE_FIELDS``; unknown keys are
        ignored.

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
        params.append(agent_id)
        params.append(user_id)
        cursor = self._execute(
            f"UPDATE ai_agents SET {', '.join(clauses)} "
            "WHERE id = ? AND user_id = ?",
            tuple(params),
        )
        self._commit()
        return cursor.rowcount > 0

    def delete(self, agent_id: int, user_id: int) -> bool:
        """Delete a custom agent owned by the user.

        Returns:
            True if a row was deleted.
        """
        cursor = self._execute(
            "DELETE FROM ai_agents WHERE id = ? AND user_id = ? "
            "AND is_builtin = 0",
            (agent_id, user_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def clear_agent_references(self, agent_id: int, user_id: int) -> None:
        """Detach conversations and settings from a deleted agent.

        ``ai_conversations.agent_id`` and ``ai_chat_settings.active_agent_id``
        carry no foreign key, so dangling references are cleared here.
        """
        self._execute(
            "UPDATE ai_conversations SET agent_id = NULL "
            "WHERE agent_id = ? AND user_id = ?",
            (agent_id, user_id),
        )
        self._execute(
            "UPDATE ai_chat_settings SET active_agent_id = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE active_agent_id = ? AND user_id = ?",
            (agent_id, user_id),
        )
        self._commit()

    @staticmethod
    def _parse_row(row: Optional[dict]) -> Optional[dict]:
        """Decode ``tools_json`` into a ``tools`` list on a row dict."""
        if row is None:
            return None
        try:
            tools = json.loads(row.get("tools_json") or "[]")
        except (TypeError, ValueError):
            tools = []
        row["tools"] = tools if isinstance(tools, list) else []
        row["is_builtin"] = bool(row.get("is_builtin"))
        return row
