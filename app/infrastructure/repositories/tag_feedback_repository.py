"""Tag suggestion feedback repository - user accept/reject tracking."""
from typing import List, Dict, Tuple, Optional

from .base import Repository


class TagFeedbackRepository(Repository):
    """Repository for storing and querying tag suggestion feedback."""

    def record(self, item_id: str, context_tag_ids: List[int],
               suggested_tag_id: int, outcome: str) -> None:
        """Record a feedback event."""
        import json
        self._execute(
            """
            INSERT INTO tag_suggestion_feedback
                (item_id, context_tag_ids, suggested_tag_id, outcome)
            VALUES (?, ?, ?, ?)
            """,
            (item_id, json.dumps(sorted(context_tag_ids)), suggested_tag_id, outcome)
        )
        self._commit()

    def get_stats(self, suggested_tag_id: int,
                  context_tag_ids: Optional[List[int]] = None) -> Tuple[int, int]:
        """Return (accepts, rejects) for a suggested tag.

        If context_tag_ids is provided, restrict to contexts that contain
        exactly the same set of tags (simplest matching).
        """
        import json
        if context_tag_ids is not None:
            ctx = json.dumps(sorted(context_tag_ids))
            cursor = self._execute(
                """
                SELECT outcome, COUNT(*) as cnt
                FROM tag_suggestion_feedback
                WHERE suggested_tag_id = ? AND context_tag_ids = ?
                GROUP BY outcome
                """,
                (suggested_tag_id, ctx)
            )
        else:
            cursor = self._execute(
                """
                SELECT outcome, COUNT(*) as cnt
                FROM tag_suggestion_feedback
                WHERE suggested_tag_id = ?
                GROUP BY outcome
                """,
                (suggested_tag_id,)
            )
        accepts = 0
        rejects = 0
        for row in cursor.fetchall():
            if row["outcome"] == "accepted":
                accepts = row["cnt"]
            elif row["outcome"] == "rejected":
                rejects = row["cnt"]
        return accepts, rejects

    def get_stats_for_tags(self, suggested_tag_id: int,
                           current_tag_ids: List[int]) -> Tuple[int, int]:
        """Get aggregated accepts/rejects for suggested tag in contexts
        that share at least one tag with current_tag_ids (loose match).

        This is a fallback when exact context match has insufficient data.
        """
        if not current_tag_ids:
            return self.get_stats(suggested_tag_id)
        placeholders = ','.join('?' * len(current_tag_ids))
        # context_tag_ids is JSON array string; use LIKE for loose matching
        likes = [f'%"{tid}"%' for tid in current_tag_ids]
        like_placeholders = ' OR '.join(['context_tag_ids LIKE ?'] * len(likes))
        cursor = self._execute(
            f"""
            SELECT outcome, COUNT(*) as cnt
            FROM tag_suggestion_feedback
            WHERE suggested_tag_id = ?
              AND ({like_placeholders})
            GROUP BY outcome
            """,
            (suggested_tag_id, *likes)
        )
        accepts = 0
        rejects = 0
        for row in cursor.fetchall():
            if row["outcome"] == "accepted":
                accepts = row["cnt"]
            elif row["outcome"] == "rejected":
                rejects = row["cnt"]
        return accepts, rejects
