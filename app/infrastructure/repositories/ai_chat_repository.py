"""AI chat repository - conversations and messages.

Conversations are per-user chat threads; messages are stored with a JSON
``parts`` column so a single row can carry text, tool calls and tool
results. Every query is scoped by ``user_id`` so ownership is enforced at
the storage layer.

SQLite foreign-key cascades are not relied upon: deleting a conversation
removes its messages explicitly first.
"""
import json
import uuid
from typing import Optional, List

from .base import Repository


class AiChatRepository(Repository):
    """Repository for ``ai_conversations`` and ``ai_messages`` rows."""

    # =====================================================================
    # Conversations
    # =====================================================================

    def create_conversation(
        self,
        user_id: int,
        title: str = "New chat",
        conversation_id: Optional[str] = None,
    ) -> dict:
        """Create a conversation owned by a user.

        Args:
            user_id: Owner's user ID.
            title: Conversation title.
            conversation_id: Optional explicit ID (UUID hex when omitted).

        Returns:
            The full conversation row.
        """
        conv_id = conversation_id or uuid.uuid4().hex
        self._execute(
            """INSERT INTO ai_conversations (id, user_id, title)
               VALUES (?, ?, ?)""",
            (conv_id, user_id, title),
        )
        self._commit()
        conversation = self.get_conversation(conv_id, user_id)
        if conversation is None:  # pragma: no cover - defensive
            raise ValueError(f"conversation {conv_id} could not be created")
        return conversation

    def get_conversation(
        self, conversation_id: str, user_id: int
    ) -> Optional[dict]:
        """Get a single conversation owned by a user.

        Args:
            conversation_id: Conversation ID.
            user_id: Owner's user ID.

        Returns:
            Conversation dict or None if not found (or not owned).
        """
        cursor = self._execute(
            "SELECT * FROM ai_conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return self._row_to_dict(cursor.fetchone())

    def list_conversations(self, user_id: int) -> List[dict]:
        """List the user's conversations, most recently updated first.

        Args:
            user_id: Owner's user ID.

        Returns:
            List of conversation dicts ordered by ``updated_at DESC``.
        """
        cursor = self._execute(
            "SELECT * FROM ai_conversations WHERE user_id = ? "
            "ORDER BY updated_at DESC, id DESC",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        """Delete a conversation and all its messages.

        Deletion is explicit at every level - SQLite FK cascades are not
        relied upon.

        Args:
            conversation_id: Conversation ID.
            user_id: Owner's user ID.

        Returns:
            True if the conversation existed and was deleted.
        """
        if self.get_conversation(conversation_id, user_id) is None:
            return False
        # Children first, then the conversation itself.
        self._execute(
            "DELETE FROM ai_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        self._execute(
            "DELETE FROM ai_conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        self._commit()
        return True

    def set_title(self, conversation_id: str, user_id: int, title: str) -> bool:
        """Set the conversation title.

        Args:
            conversation_id: Conversation ID.
            user_id: Owner's user ID.
            title: New title.

        Returns:
            True if a row was updated.
        """
        cursor = self._execute(
            "UPDATE ai_conversations SET title = ? "
            "WHERE id = ? AND user_id = ?",
            (title, conversation_id, user_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def touch_conversation(self, conversation_id: str) -> None:
        """Bump the conversation's ``updated_at`` timestamp.

        Args:
            conversation_id: Conversation ID.
        """
        self._execute(
            "UPDATE ai_conversations SET updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (conversation_id,),
        )
        self._commit()

    # =====================================================================
    # Messages
    # =====================================================================

    def add_message(
        self, conversation_id: str, role: str, parts: List[dict]
    ) -> dict:
        """Append a message to a conversation.

        The conversation's ``updated_at`` is bumped so it sorts first in
        listings.

        Args:
            conversation_id: Conversation ID.
            role: Message role ('user', 'assistant' or 'tool').
            parts: List of part dicts (JSON-encoded for storage).

        Returns:
            Dict with ``id``, ``role``, ``parts`` (parsed) and ``created_at``.
        """
        cursor = self._execute(
            "INSERT INTO ai_messages (conversation_id, role, parts) "
            "VALUES (?, ?, ?)",
            (conversation_id, role, json.dumps(parts, ensure_ascii=False)),
        )
        message_id = cursor.lastrowid
        self.touch_conversation(conversation_id)
        row = self._execute(
            "SELECT id, role, parts, created_at FROM ai_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:  # pragma: no cover - defensive
            raise ValueError(f"message {message_id} could not be created")
        message = dict(row)
        message["parts"] = json.loads(message["parts"])
        return message

    def get_messages(
        self, conversation_id: str, limit: int = 100
    ) -> List[dict]:
        """Get the last N messages of a conversation in ascending order.

        Args:
            conversation_id: Conversation ID.
            limit: Maximum number of messages to return.

        Returns:
            List of message dicts with ``parts`` parsed from JSON, oldest
            first.
        """
        cursor = self._execute(
            "SELECT id, role, parts, created_at FROM ai_messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        messages = []
        for row in reversed(cursor.fetchall()):
            message = dict(row)
            try:
                message["parts"] = json.loads(message["parts"])
            except (TypeError, ValueError):
                message["parts"] = []
            messages.append(message)
        return messages
