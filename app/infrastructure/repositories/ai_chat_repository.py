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

# Sentinel for add_message: "resolve the parent automatically". Passing
# parent_id=None explicitly anchors a new branch root instead.
_AUTO_PARENT = object()


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
        agent_id: Optional[int] = None,
    ) -> dict:
        """Create a conversation owned by a user.

        Args:
            user_id: Owner's user ID.
            title: Conversation title.
            conversation_id: Optional explicit ID (UUID hex when omitted).
            agent_id: Optional agent the conversation runs with
                (NULL = the default assistant).

        Returns:
            The full conversation row.
        """
        conv_id = conversation_id or uuid.uuid4().hex
        self._execute(
            """INSERT INTO ai_conversations (id, user_id, title, agent_id)
               VALUES (?, ?, ?, ?)""",
            (conv_id, user_id, title, agent_id),
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

    def _active_leaf_id(self, conversation_id: str) -> Optional[int]:
        """Return the conversation's active branch leaf (or None)."""
        row = self._execute(
            "SELECT active_leaf_id FROM ai_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return row["active_leaf_id"] if row else None

    def add_message(
        self,
        conversation_id: str,
        role: str,
        parts: List[dict],
        parent_id: Optional[int] = _AUTO_PARENT,
    ) -> dict:
        """Append a message to a conversation.

        The message chains onto ``parent_id``. Omitting the argument
        continues the conversation's active branch (legacy conversations:
        the newest message); passing ``None`` explicitly creates a branch
        root. The new row becomes the active leaf, and the conversation's
        ``updated_at`` is bumped so it sorts first in listings.

        Args:
            conversation_id: Conversation ID.
            role: Message role ('user', 'assistant' or 'tool').
            parts: List of part dicts (JSON-encoded for storage).
            parent_id: Preceding message id in the branch, ``None`` for a
                branch root, omitted to auto-resolve the active leaf.

        Returns:
            Dict with ``id``, ``role``, ``parts`` (parsed), ``parent_id``
            and ``created_at``.
        """
        if parent_id is _AUTO_PARENT:
            parent_id = self._active_leaf_id(conversation_id)
            if parent_id is None:
                row = self._execute(
                    "SELECT MAX(id) AS last_id FROM ai_messages "
                    "WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                parent_id = row["last_id"] if row else None
        cursor = self._execute(
            "INSERT INTO ai_messages (conversation_id, role, parts, parent_id) "
            "VALUES (?, ?, ?, ?)",
            (
                conversation_id,
                role,
                json.dumps(parts, ensure_ascii=False),
                parent_id,
            ),
        )
        message_id = cursor.lastrowid
        self._execute(
            "UPDATE ai_conversations "
            "SET active_leaf_id = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (message_id, conversation_id),
        )
        self._commit()
        row = self._execute(
            "SELECT id, role, parts, parent_id, created_at FROM ai_messages "
            "WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:  # pragma: no cover - defensive
            raise ValueError(f"message {message_id} could not be created")
        message = dict(row)
        message["parts"] = json.loads(message["parts"])
        return message

    def get_message(
        self, conversation_id: str, message_id: int
    ) -> Optional[dict]:
        """Get one message of a conversation by id.

        Args:
            conversation_id: Conversation ID (scopes the lookup).
            message_id: Message row id.

        Returns:
            Message dict with ``parts`` parsed, or None.
        """
        cursor = self._execute(
            "SELECT id, role, parts, parent_id, created_at FROM ai_messages "
            "WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        message = dict(row)
        try:
            message["parts"] = json.loads(message["parts"])
        except (TypeError, ValueError):
            message["parts"] = []
        return message

    def get_messages(
        self, conversation_id: str, limit: int = 100
    ) -> List[dict]:
        """Get the messages of the active branch, oldest first.

        With an active leaf the returned list is the root-to-leaf path of
        that branch (capped at the last ``limit`` messages); without one
        the legacy "newest N by id" behavior applies.

        Args:
            conversation_id: Conversation ID.
            limit: Maximum number of messages to return.

        Returns:
            List of message dicts with ``parts`` parsed from JSON, oldest
            first.
        """
        leaf_id = self._active_leaf_id(conversation_id)
        if leaf_id is not None:
            path: List[dict] = []
            current: Optional[int] = leaf_id
            while current is not None:  # parent ids always precede children
                row = self._execute(
                    "SELECT id, role, parts, parent_id, created_at "
                    "FROM ai_messages WHERE id = ? AND conversation_id = ?",
                    (current, conversation_id),
                ).fetchone()
                if row is None:
                    break
                message = dict(row)
                path.append(message)
                current = message["parent_id"]
            path.reverse()
            messages = path[-limit:] if limit else path
            for message in messages:
                try:
                    message["parts"] = json.loads(message["parts"])
                except (TypeError, ValueError):
                    message["parts"] = []
            return messages

        cursor = self._execute(
            "SELECT id, role, parts, parent_id, created_at FROM ai_messages "
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

    def get_user_siblings(
        self, conversation_id: str, parent_id: Optional[int]
    ) -> List[int]:
        """Ids of a version group: user messages sharing one parent.

        Args:
            conversation_id: Conversation ID.
            parent_id: Shared parent id (None = conversation roots).

        Returns:
            Sibling message ids in creation order.
        """
        cursor = self._execute(
            "SELECT id FROM ai_messages "
            "WHERE conversation_id = ? AND role = 'user' AND parent_id IS ? "
            "ORDER BY id",
            (conversation_id, parent_id),
        )
        return [row["id"] for row in cursor.fetchall()]

    def deepest_leaf(self, conversation_id: str, message_id: int) -> int:
        """Follow the newest-child chain down from a message to its leaf.

        Args:
            conversation_id: Conversation ID.
            message_id: Any message of the branch.

        Returns:
            The id of the branch's deepest newest message.
        """
        current = message_id
        while True:
            row = self._execute(
                "SELECT id FROM ai_messages "
                "WHERE conversation_id = ? AND parent_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (conversation_id, current),
            ).fetchone()
            if row is None:
                return current
            current = row["id"]

    def set_active_leaf(self, conversation_id: str, message_id: int) -> None:
        """Point the conversation's active branch at a message.

        Args:
            conversation_id: Conversation ID.
            message_id: New leaf (typically ``deepest_leaf`` of the chosen
                version).
        """
        self._execute(
            "UPDATE ai_conversations SET active_leaf_id = ? WHERE id = ?",
            (message_id, conversation_id),
        )
        self._commit()
