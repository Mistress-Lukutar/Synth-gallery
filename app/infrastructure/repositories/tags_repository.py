"""Tags repository - flat tag management with implication support."""
from typing import Optional, List, Dict

from .base import Repository


class TagsRepository(Repository):
    """Repository for flat tag operations."""

    # ========================================================================
    # Categories
    # ========================================================================

    def get_categories(self) -> List[Dict]:
        """Get all tag categories ordered by sort_order."""
        cursor = self._execute(
            "SELECT id, slug, name, color, sort_order FROM tag_categories ORDER BY sort_order"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_category_by_slug(self, slug: str) -> Optional[Dict]:
        """Get category by slug."""
        cursor = self._execute(
            "SELECT id, slug, name, color, sort_order FROM tag_categories WHERE slug = ?",
            (slug,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ========================================================================
    # Tags - Basic CRUD
    # ========================================================================

    def get_by_id(self, tag_id: int) -> Optional[Dict]:
        """Get tag by ID with category info."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color, c.slug as category_slug
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.id = ?
        """, (tag_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_by_name(self, name: str) -> List[Dict]:
        """Get tags by exact name."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.name = ?
        """, (name,))
        return [dict(row) for row in cursor.fetchall()]

    def get_tags_by_ids(self, tag_ids: List[int]) -> List[Dict]:
        """Batch fetch tags by IDs."""
        if not tag_ids:
            return []
        placeholders = ','.join('?' * len(tag_ids))
        cursor = self._execute(f"""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.id IN ({placeholders})
        """, tuple(tag_ids))
        return [dict(row) for row in cursor.fetchall()]

    def create(self, name: str, display_name: str, category_id: int) -> int:
        """Create a new flat tag.

        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name for UI
            category_id: Category ID

        Returns:
            New tag ID
        """
        cursor = self._execute("""
            INSERT INTO tags (name, display_name, category_id, usage_count)
            VALUES (?, ?, ?, 0)
        """, (name, display_name or name.replace('_', ' ').title(), category_id))
        self._commit()
        return cursor.lastrowid

    # ========================================================================
    # Search
    # ========================================================================

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search tags by name or display_name, ordered by usage count."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color, t.usage_count as count
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.name LIKE ? OR t.display_name LIKE ?
            ORDER BY t.usage_count DESC, t.name
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================================================
    # Item Tags - Materialized explicit + implied
    # ========================================================================

    def get_item_tags_explicit(self, item_id: str) -> List[Dict]:
        """Get only explicit (user-added) tags for an item."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ? AND it.is_explicit = 1
            ORDER BY t.name
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_item_tags_implied(self, item_id: str) -> List[Dict]:
        """Get only implied (auto-resolved) tags for an item."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ? AND it.is_explicit = 0
            ORDER BY t.name
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_item_tags_all(self, item_id: str) -> List[Dict]:
        """Get all tags for item (explicit + implied) with flag."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color,
                   it.is_explicit
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ?
            ORDER BY it.is_explicit DESC, t.name
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]

    def set_item_tags(self, item_id: str, explicit_tag_ids: List[int],
                      implied_tag_ids: List[int]) -> None:
        """Replace all tags for item with explicit + implied sets.

        Also updates usage_count incrementally.
        """
        # Get current tags to compute delta for usage_count
        current = self._execute(
            "SELECT tag_id FROM item_tags WHERE item_id = ?", (item_id,)
        ).fetchall()
        current_ids = {row["tag_id"] for row in current}
        new_ids = set(explicit_tag_ids) | set(implied_tag_ids)
        removed = current_ids - new_ids
        added = new_ids - current_ids

        # Delete existing
        self._execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))

        # Insert explicit
        for tid in explicit_tag_ids:
            self._execute(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id, is_explicit) VALUES (?, ?, 1)",
                (item_id, tid)
            )
        # Insert implied
        for tid in implied_tag_ids:
            self._execute(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id, is_explicit) VALUES (?, ?, 0)",
                (item_id, tid)
            )

        # Update usage counts
        for tid in removed:
            self._execute(
                "UPDATE tags SET usage_count = MAX(0, usage_count - 1) WHERE id = ?", (tid,)
            )
        for tid in added:
            self._execute(
                "UPDATE tags SET usage_count = usage_count + 1 WHERE id = ?", (tid,)
            )

        self._commit()

    # ========================================================================
    # Item search by tags
    # ========================================================================

    def get_items_by_tag(self, tag_id: int, folder_id: Optional[str] = None) -> List[Dict]:
        """Get items by tag (materialized: item_tags already contains implied)."""
        if folder_id:
            cursor = self._execute("""
                SELECT DISTINCT i.*
                FROM items i
                JOIN item_tags it ON i.id = it.item_id
                WHERE it.tag_id = ? AND i.folder_id = ?
                ORDER BY i.uploaded_at DESC
            """, (tag_id, folder_id))
        else:
            cursor = self._execute("""
                SELECT DISTINCT i.*
                FROM items i
                JOIN item_tags it ON i.id = it.item_id
                WHERE it.tag_id = ?
                ORDER BY i.uploaded_at DESC
            """, (tag_id,))
        return [dict(row) for row in cursor.fetchall()]

    def search_items_by_tags(
        self,
        include_groups: list[set],
        exclude_ids: set,
        folder_id: Optional[str] = None
    ) -> List[Dict]:
        """Search items by tags with include/exclude logic.

        Args:
            include_groups: List of sets, each set contains tag IDs for one search word (OR within group)
            exclude_ids: Set of tag IDs to exclude
            folder_id: Optional folder to filter by

        Returns:
            List of items with media metadata
        """
        exclude_list = list(exclude_ids) if exclude_ids else []

        if not include_groups and not exclude_list:
            return []

        placeholders_exc = ','.join('?' * len(exclude_list)) if exclude_list else '0'

        # Build AND conditions for each include group (OR within group)
        include_conditions = []
        params = []

        for i, tag_group in enumerate(include_groups):
            if not tag_group:
                continue
            placeholders = ','.join('?' * len(tag_group))
            include_conditions.append(f"""
                EXISTS (
                    SELECT 1 FROM item_tags it{i}
                    WHERE it{i}.item_id = i.id
                      AND it{i}.tag_id IN ({placeholders})
                )
            """)
            params.extend(tag_group)

        include_sql = ' AND '.join(include_conditions) if include_conditions else '1=1'

        # Build exclude condition
        exclude_sql = f"""
            NOT EXISTS (
                SELECT 1 FROM item_tags it_exc
                WHERE it_exc.item_id = i.id
                  AND it_exc.tag_id IN ({placeholders_exc})
            )
        """ if exclude_list else '1=1'

        # Build final params
        final_params = []
        if folder_id:
            final_params.append(folder_id)
        final_params.extend(params)
        final_params.extend(exclude_list)

        sql = f"""
            SELECT DISTINCT i.*, im.media_type, im.thumb_width, im.thumb_height
            FROM items i
            LEFT JOIN item_media im ON i.id = im.item_id
            WHERE i.type = 'media'
              {'AND i.folder_id = ?' if folder_id else ''}
              AND ({include_sql})
              AND ({exclude_sql})
            ORDER BY i.uploaded_at DESC
        """

        cursor = self._execute(sql, final_params)
        return [dict(row) for row in cursor.fetchall()]
