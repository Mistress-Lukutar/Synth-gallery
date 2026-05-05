"""Tag co-occurrence repository - statistical relatedness for suggestions."""
import math
from typing import List, Dict, Optional, Tuple

from .base import Repository


class TagCooccurrenceRepository(Repository):
    """Repository for tag co-occurrence (related tags) operations."""

    def update_deltas(self, added_pairs: List[Tuple[int, int]],
                      removed_pairs: List[Tuple[int, int]]) -> None:
        """Increment counts for new pairs, decrement for removed pairs."""
        # Normalize pairs to canonical form (a < b)
        def _norm(a: int, b: int) -> Tuple[int, int]:
            return (a, b) if a < b else (b, a)

        added = [_norm(a, b) for a, b in added_pairs]
        removed = [_norm(a, b) for a, b in removed_pairs]

        # Add new pairs
        if added:
            self._execute_many(
                """
                INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                VALUES (?, ?, 1)
                ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE SET
                    count = count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                added
            )

        # Decrement removed pairs
        if removed:
            self._execute_many(
                """
                UPDATE tag_cooccurrence
                SET count = count - 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tag_a_id = ? AND tag_b_id = ?
                """,
                removed
            )
            # Delete zeroed rows
            self._execute_many(
                "DELETE FROM tag_cooccurrence WHERE tag_a_id = ? AND tag_b_id = ? AND count <= 0",
                removed
            )

        self._commit()

    def _total_items(self) -> int:
        cursor = self._execute("SELECT COUNT(DISTINCT item_id) as cnt FROM item_tags")
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def _tag_usage(self, tag_id: int) -> int:
        cursor = self._execute(
            "SELECT COUNT(*) as cnt FROM item_tags WHERE tag_id = ?", (tag_id,)
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def get_usage_counts(self, tag_ids: List[int]) -> Dict[int, int]:
        """Batch fetch usage counts for multiple tags."""
        if not tag_ids:
            return {}
        placeholders = ','.join('?' * len(tag_ids))
        cursor = self._execute(f"""
            SELECT tag_id, COUNT(*) as cnt
            FROM item_tags
            WHERE tag_id IN ({placeholders})
            GROUP BY tag_id
        """, tuple(tag_ids))
        return {row["tag_id"]: row["cnt"] for row in cursor.fetchall()}

    def get_joint_counts(self, tag_id: int, related_ids: List[int]) -> Dict[int, int]:
        """Fetch co-occurrence counts between tag_id and a list of related ids."""
        if not related_ids:
            return {}
        placeholders = ','.join('?' * len(related_ids))
        cursor = self._execute(f"""
            SELECT tag_a_id, tag_b_id, count
            FROM tag_cooccurrence
            WHERE (tag_a_id = ? AND tag_b_id IN ({placeholders}))
               OR (tag_b_id = ? AND tag_a_id IN ({placeholders}))
        """, (tag_id, *related_ids, tag_id, *related_ids))
        result = {}
        for row in cursor.fetchall():
            other = row["tag_b_id"] if row["tag_a_id"] == tag_id else row["tag_a_id"]
            result[other] = row["count"]
        return result

    def get_pmi(self, tag_a: int, tag_b: int) -> float:
        """Compute Pointwise Mutual Information for a pair.

        PMI = log2( P(A,B) / (P(A) * P(B)) )
        """
        total = self._total_items()
        if total == 0:
            return 0.0

        a, b = (tag_a, tag_b) if tag_a < tag_b else (tag_b, tag_a)
        cursor = self._execute(
            "SELECT count FROM tag_cooccurrence WHERE tag_a_id = ? AND tag_b_id = ?",
            (a, b)
        )
        row = cursor.fetchone()
        joint = row["count"] if row else 0

        usage_a = self._tag_usage(tag_a)
        usage_b = self._tag_usage(tag_b)
        if usage_a == 0 or usage_b == 0 or joint == 0:
            return 0.0

        p_ab = joint / total
        p_a = usage_a / total
        p_b = usage_b / total
        return math.log2(p_ab / (p_a * p_b))

    def get_related_by_pmi(self, tag_id: int, limit: int = 10,
                           exclude_ids: Optional[List[int]] = None) -> List[Dict]:
        """Get tags most related by PMI."""
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
        """, (tag_id, tag_id, *exclude_ids, limit * 3))
        rows = [dict(row) for row in cursor.fetchall()]

        total = self._total_items()
        usage_tag = self._tag_usage(tag_id)
        for row in rows:
            usage_related = self._tag_usage(row["id"])
            joint = row["count"]
            if total > 0 and usage_tag > 0 and usage_related > 0 and joint > 0:
                pmi = math.log2((joint * total) / (usage_tag * usage_related))
            else:
                pmi = 0.0
            row["pmi"] = round(pmi, 3)

        rows.sort(key=lambda x: x["pmi"], reverse=True)
        return rows[:limit]

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

    def rebuild_all_from_item_tags(self) -> None:
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
