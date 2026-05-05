"""Tag mutex repository - negative correlation detection for suggestions."""
import math
from typing import List, Dict, Optional

from .base import Repository


class TagMutexRepository(Repository):
    """Repository for mutual-exclusivity (mutex) pairs."""

    PHI_THRESHOLD = -0.3
    MIN_USAGE = 10

    def rebuild_all(self) -> None:
        """Recalculate phi for all frequent tag pairs and repopulate table."""
        # Gather tag usage counts
        cursor = self._execute("SELECT id, usage_count FROM tags WHERE usage_count >= ?", (self.MIN_USAGE,))
        tags = {row["id"]: row["usage_count"] for row in cursor.fetchall()}
        tag_ids = sorted(tags.keys())
        if len(tag_ids) < 2:
            self._execute("DELETE FROM tag_mutex_pairs WHERE is_auto = 1")
            self._commit()
            return

        total_items = self._total_items()
        if total_items == 0:
            return

        # Load co-occurrence counts into a dict for fast lookup
        cursor = self._execute("SELECT tag_a_id, tag_b_id, count FROM tag_cooccurrence")
        cooc = {}
        for row in cursor.fetchall():
            cooc[(row["tag_a_id"], row["tag_b_id"])] = row["count"]

        mutex_pairs = []
        for i in range(len(tag_ids)):
            a = tag_ids[i]
            usage_a = tags[a]
            for j in range(i + 1, len(tag_ids)):
                b = tag_ids[j]
                usage_b = tags[b]
                key = (a, b) if a < b else (b, a)
                n11 = cooc.get(key, 0)
                n10 = usage_a - n11
                n01 = usage_b - n11
                n00 = total_items - n10 - n01 - n11

                n1_ = n11 + n10
                n0_ = n01 + n00
                n_1 = n11 + n01
                n_0 = n10 + n00

                denom = math.sqrt(n1_ * n0_ * n_1 * n_0)
                if denom == 0:
                    continue
                phi = (n11 * n00 - n10 * n01) / denom
                if phi < self.PHI_THRESHOLD:
                    mutex_pairs.append((a, b, phi))

        # Replace auto rows
        self._execute("DELETE FROM tag_mutex_pairs WHERE is_auto = 1")
        if mutex_pairs:
            self._execute_many(
                "INSERT INTO tag_mutex_pairs (tag_a_id, tag_b_id, phi, is_auto) VALUES (?, ?, ?, 1)",
                mutex_pairs
            )
        self._commit()

    def get_mutex_for_tags(self, tag_ids: List[int]) -> List[Dict]:
        """Return all mutex entries where any of the given tags is involved."""
        if not tag_ids:
            return []
        placeholders = ','.join('?' * len(tag_ids))
        cursor = self._execute(f"""
            SELECT tag_a_id, tag_b_id, phi, is_auto
            FROM tag_mutex_pairs
            WHERE tag_a_id IN ({placeholders}) OR tag_b_id IN ({placeholders})
        """, (*tag_ids, *tag_ids))
        return [dict(row) for row in cursor.fetchall()]

    def is_mutex(self, tag_a: int, tag_b: int) -> bool:
        """Quick check if a pair is marked as mutex."""
        a, b = (tag_a, tag_b) if tag_a < tag_b else (tag_b, tag_a)
        cursor = self._execute(
            "SELECT 1 FROM tag_mutex_pairs WHERE tag_a_id = ? AND tag_b_id = ?",
            (a, b)
        )
        return cursor.fetchone() is not None

    def _total_items(self) -> int:
        cursor = self._execute("SELECT COUNT(DISTINCT item_id) as cnt FROM item_tags")
        row = cursor.fetchone()
        return row["cnt"] if row else 0
