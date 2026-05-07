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

    def get_category_by_id(self, category_id: int) -> Optional[Dict]:
        """Get category by ID."""
        cursor = self._execute(
            "SELECT id, slug, name, color, sort_order FROM tag_categories WHERE id = ?",
            (category_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_category(self, name: str, slug: str, color: str, sort_order: int) -> int:
        """Create a new tag category."""
        cursor = self._execute(
            "INSERT INTO tag_categories (name, slug, color, sort_order) VALUES (?, ?, ?, ?)",
            (name, slug, color, sort_order)
        )
        self._commit()
        return cursor.lastrowid

    def update_category(self, category_id: int, **fields) -> bool:
        """Update category fields."""
        allowed = {"name", "slug", "color", "sort_order"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._execute(
            f"UPDATE tag_categories SET {set_clause} WHERE id = ?",
            tuple(updates.values()) + (category_id,)
        )
        self._commit()
        return self._conn.total_changes > 0

    def delete_category(self, category_id: int) -> bool:
        """Delete category if no tags reference it."""
        cursor = self._execute(
            "SELECT COUNT(*) as cnt FROM tags WHERE category_id = ?", (category_id,)
        )
        row = cursor.fetchone()
        if row and row["cnt"] > 0:
            raise ValueError("Cannot delete category with existing tags")
        self._execute("DELETE FROM tag_categories WHERE id = ?", (category_id,))
        self._commit()
        return self._conn.total_changes > 0

    # ========================================================================
    # Tags - Basic CRUD
    # ========================================================================

    def get_by_id(self, tag_id: int) -> Optional[Dict]:
        """Get tag by ID with category info."""
        cursor = self._execute("""
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color, c.slug as category_slug
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.id = ?
        """, (tag_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_by_name(self, name: str) -> List[Dict]:
        """Get tags by exact name."""
        cursor = self._execute("""
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color
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
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.id IN ({placeholders})
        """, tuple(tag_ids))
        return [dict(row) for row in cursor.fetchall()]

    def list_tags(self, query: Optional[str] = None, limit: Optional[int] = 50,
                  offset: int = 0, category_id: Optional[int] = None) -> List[Dict]:
        """Get paginated tag list with category info and usage count.

        Pass limit=None to return all tags without pagination.
        """
        conditions = []
        params = []
        if query:
            conditions.append("(t.name LIKE ? OR t.display_name LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if category_id is not None:
            conditions.append("t.category_id = ?")
            params.append(category_id)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        pagination = ""
        if limit is not None and limit > 0:
            pagination = "LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        sql = f"""
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            {where}
            ORDER BY t.usage_count DESC, t.name
            {pagination}
        """
        cursor = self._execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def count_tags(self, query: Optional[str] = None, category_id: Optional[int] = None) -> int:
        """Count total tags (optionally filtered by query and category)."""
        conditions = []
        params = []
        if query:
            conditions.append("(name LIKE ? OR display_name LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if category_id is not None:
            conditions.append("category_id = ?")
            params.append(category_id)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        cursor = self._execute(f"SELECT COUNT(*) as cnt FROM tags {where}", tuple(params))
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def update_tag(self, tag_id: int, **fields) -> bool:
        """Update tag fields. Returns True if row updated."""
        allowed = {"name", "display_name", "category_id", "description"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._execute(
            f"UPDATE tags SET {set_clause} WHERE id = ?",
            tuple(updates.values()) + (tag_id,)
        )
        self._commit()
        return self._conn.total_changes > 0

    def delete_tag(self, tag_id: int) -> bool:
        """Delete tag. Cascades via FK to item_tags and tag_implications."""
        self._execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._commit()
        return self._conn.total_changes > 0

    def remap_tag(self, tag_id: int, target_tag_id: int) -> bool:
        """Replace tag_id with target_tag_id on all items, then delete tag_id.

        Items that already have target_tag_id keep it; if the old tag was
        explicit the target becomes explicit as well.
        """
        self._replace_tag_on_items(tag_id, target_tag_id)

        # Delete old tag (cascades to implications, co-occurrence, mutex via FK)
        self._execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._commit()
        return True

    def replace_tag(self, tag_id: int, target_tag_id: int) -> bool:
        """Replace tag_id with target_tag_id on all items without deleting tag_id.

        Items that already have target_tag_id keep it; if the old tag was
        explicit the target becomes explicit as well. The source tag remains
        in the database with zero usage count.
        """
        self._replace_tag_on_items(tag_id, target_tag_id)

        # Recalculate source tag usage count (should be zero after move)
        self._execute(
            """
            UPDATE tags SET usage_count = (
                SELECT COUNT(DISTINCT item_id) FROM item_tags WHERE tag_id = ?
            ) WHERE id = ?
            """,
            (tag_id, tag_id),
        )

        self._commit()
        return True

    def _replace_tag_on_items(self, tag_id: int, target_tag_id: int) -> None:
        """Core logic: move item associations from tag_id to target_tag_id.

        Promotes target to explicit where needed, moves items without the
        target tag, and deletes remaining old associations.
        """
        # Promote target to explicit on items where old tag was explicit
        self._execute(
            """
            UPDATE item_tags
            SET is_explicit = 1
            WHERE tag_id = ?
              AND is_explicit = 0
              AND item_id IN (
                  SELECT item_id FROM item_tags WHERE tag_id = ? AND is_explicit = 1
              )
            """,
            (target_tag_id, tag_id),
        )

        # Move items that don't already have the target tag
        self._execute(
            """
            UPDATE item_tags
            SET tag_id = ?
            WHERE tag_id = ?
              AND item_id NOT IN (SELECT item_id FROM item_tags WHERE tag_id = ?)
            """,
            (target_tag_id, tag_id, target_tag_id),
        )

        # Delete remaining old associations (items that had both tags)
        self._execute("DELETE FROM item_tags WHERE tag_id = ?", (tag_id,))

        # Recalculate target tag usage count
        self._execute(
            """
            UPDATE tags SET usage_count = (
                SELECT COUNT(DISTINCT item_id) FROM item_tags WHERE tag_id = ?
            ) WHERE id = ?
            """,
            (target_tag_id, target_tag_id),
        )

    def create(self, name: str, display_name: str, category_id: int, description: str = '') -> int:
        """Create a new flat tag.

        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name for UI
            category_id: Category ID
            description: Markdown description

        Returns:
            New tag ID
        """
        cursor = self._execute("""
            INSERT INTO tags (name, display_name, category_id, usage_count, description)
            VALUES (?, ?, ?, 0, ?)
        """, (name, display_name or name.replace('_', ' ').title(), category_id, description))
        self._commit()
        return cursor.lastrowid

    # ========================================================================
    # Search
    # ========================================================================

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search tags by name or display_name.

        Results are ordered by relevance:
        1. Exact name match
        2. Name starts with query
        3. Other contains matches
        Within each group tags are sorted alphabetically by name.
        """
        cursor = self._execute("""
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color, t.usage_count as count
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.name LIKE ? OR t.display_name LIKE ?
            ORDER BY
                CASE
                    WHEN t.name = ? THEN 0
                    WHEN t.name LIKE ? THEN 1
                    ELSE 2
                END,
                t.usage_count DESC,
                t.name
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", query, f"{query}%", limit))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================================================
    # Item Tags - Materialized explicit + implied
    # ========================================================================

    def get_item_tags_explicit(self, item_id: str) -> List[Dict]:
        """Get only explicit (user-added) tags for an item."""
        cursor = self._execute("""
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color
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
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color
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
            SELECT t.id, t.name, t.display_name, t.category_id, t.usage_count, t.description, t.created_at,
                   c.name as category_name, c.color as category_color, it.is_explicit
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
    # Sanitize helpers
    # ========================================================================

    def get_all_item_ids_with_explicit_tags(self) -> List[str]:
        """Get all item IDs that have at least one explicit tag."""
        cursor = self._execute("""
            SELECT DISTINCT item_id FROM item_tags WHERE is_explicit = 1
        """)
        return [row["item_id"] for row in cursor.fetchall()]

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

    def get_all_items_with_explicit_tags(self):
        """Return all item IDs that have at least one explicit tag."""
        cursor = self._execute(
            """
            SELECT DISTINCT item_id FROM item_tags WHERE is_explicit = 1
            """
        )
        return [row["item_id"] for row in cursor.fetchall()]
