"""Tag co-occurrence repository - statistical relatedness for suggestions."""
from typing import List, Dict, Optional

from .base import Repository


class TagCooccurrenceRepository(Repository):
    """Repository for tag co-occurrence (related tags) operations."""

    def increment(self, tag_a: int, tag_b: int) -> None:
        """Increment co-occurrence count for a tag pair."""
        a, b = (tag_a, tag_b) if tag_a < tag_b else (tag_b, tag_a)
        self._execute("""
            INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE SET
                count = count + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (a, b))
        self._commit()

    def increment_many(self, tag_ids: List[int]) -> None:
        """Increment co-occurrence for all pairs in a list of tags."""
        if len(tag_ids) < 2:
            return
        # Build all canonical pairs
        pairs = []
        for i in range(len(tag_ids)):
            for j in range(i + 1, len(tag_ids)):
                a, b = (tag_ids[i], tag_ids[j]) if tag_ids[i] < tag_ids[j] else (tag_ids[j], tag_ids[i])
                pairs.append((a, b))
        if not pairs:
            return
        # Insert new pairs (ignore existing)
        self._execute_many(
            "INSERT OR IGNORE INTO tag_cooccurrence (tag_a_id, tag_b_id, count) VALUES (?, ?, 1)",
            pairs
        )
        # Increment existing pairs
        self._execute_many(
            "UPDATE tag_cooccurrence SET count = count + 1, updated_at = CURRENT_TIMESTAMP WHERE tag_a_id = ? AND tag_b_id = ?",
            pairs
        )
        self._commit()

    def get_related_tags(self, tag_id: int, limit: int = 10,
                         exclude_ids: Optional[List[int]] = None) -> List[Dict]:
        """Get tags most frequently co-occurring with tag_id."""
        exclude_ids = exclude_ids or []
        exclude_ids.append(tag_id)
        placeholders = ','.join('?' * len(exclude_ids))
        cursor = self._execute(f"""
            SELECT t.id, t.name, t.display_name, t.category_id, c.name as category_name,
                   c.color as category_color, tc.count
            FROM (
                SELECT tag_b_id as related_id, count FROM tag_cooccurrence
                WHERE tag_a_id = ?
                UNION ALL
                SELECT tag_a_id as related_id, count FROM tag_cooccurrence
                WHERE tag_b_id = ?
            ) tc
            JOIN tags t ON tc.related_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE tc.related_id NOT IN ({placeholders})
            ORDER BY tc.count DESC
            LIMIT ?
        """, (tag_id, tag_id, *exclude_ids, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_contextual_suggestions(self, selected_tag_ids: List[int],
                                   limit: int = 10,
                                   exclude_ids: Optional[List[int]] = None) -> List[Dict]:
        """Get related tags ranked by total co-occurrence with all selected tags."""
        if not selected_tag_ids:
            return []
        exclude_ids = list(set((exclude_ids or []) + selected_tag_ids))
        placeholders_sel = ','.join('?' * len(selected_tag_ids))
        placeholders_exc = ','.join('?' * len(exclude_ids))
        cursor = self._execute(f"""
            SELECT t.id, t.name, t.display_name, t.category_id, c.name as category_name,
                   c.color as category_color, SUM(tc.count) as score
            FROM (
                SELECT tag_b_id as related_id, count FROM tag_cooccurrence
                WHERE tag_a_id IN ({placeholders_sel})
                UNION ALL
                SELECT tag_a_id as related_id, count FROM tag_cooccurrence
                WHERE tag_b_id IN ({placeholders_sel})
            ) tc
            JOIN tags t ON tc.related_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE tc.related_id NOT IN ({placeholders_exc})
            GROUP BY tc.related_id
            ORDER BY score DESC
            LIMIT ?
        """, (*selected_tag_ids, *selected_tag_ids, *exclude_ids, limit))
        return [dict(row) for row in cursor.fetchall()]

    def rebuild_all(self) -> None:
        """Rebuild co-occurrence table from scratch based on current item_tags."""
        self._execute("DELETE FROM tag_cooccurrence")
        cursor = self._execute("""
            SELECT item_id, tag_id FROM item_tags ORDER BY item_id
        """)
        from collections import defaultdict
        from itertools import combinations
        item_tags = defaultdict(list)
        for row in cursor.fetchall():
            item_tags[row["item_id"]].append(row["tag_id"])
        pairs = []
        for tags in item_tags.values():
            for a, b in combinations(sorted(tags), 2):
                pairs.append((a, b))
        if pairs:
            self._execute_many("""
                INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                VALUES (?, ?, 1)
                ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE SET
                    count = count + 1
            """, pairs)
        self._commit()
