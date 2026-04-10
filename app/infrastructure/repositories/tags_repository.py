"""Tags repository - hierarchical tag management."""
from typing import Optional, List, Dict

from .base import Repository


class TagsRepository(Repository):
    """Repository for hierarchical tag operations."""
    
    # =========================================================================
    # Categories
    # =========================================================================
    
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
    
    # =========================================================================
    # Tags - Basic CRUD
    # =========================================================================
    
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
    
    def get_by_path(self, path: str) -> Optional[Dict]:
        """Get tag by path."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.path = ?
        """, (path,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_by_name(self, name: str) -> List[Dict]:
        """Get tags by name (can be multiple in different hierarchies)."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.name = ?
        """, (name,))
        return [dict(row) for row in cursor.fetchall()]
    
    def create(self, name: str, display_name: str, category_id: int, 
               parent_id: Optional[int] = None) -> int:
        """Create a new tag.
        
        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name for UI
            category_id: Category ID
            parent_id: Parent tag ID (None for root)
            
        Returns:
            New tag ID
        """
        # Build path
        if parent_id:
            parent = self.get_by_id(parent_id)
            path = f"{parent['path']}.{name}"
            level = parent['level'] + 1
        else:
            # Get category slug for root path
            cat = self._execute("SELECT slug FROM tag_categories WHERE id = ?", 
                              (category_id,)).fetchone()
            path = f"{cat['slug']}.{name}" if cat else name
            level = 0
        
        cursor = self._execute("""
            INSERT INTO tags (name, display_name, category_id, parent_id, path, level, is_leaf)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (name, display_name, category_id, parent_id, path, level))
        
        tag_id = cursor.lastrowid
        
        # Mark parent as non-leaf
        if parent_id:
            self._execute("UPDATE tags SET is_leaf = 0 WHERE id = ?", (parent_id,))
        
        self._commit()
        return tag_id
    
    # =========================================================================
    # Hierarchy Operations
    # =========================================================================
    
    def get_children(self, parent_id: int) -> List[Dict]:
        """Get direct children of a tag with count."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color,
                   (SELECT COUNT(DISTINCT it.item_id) 
                    FROM item_tags it
                    JOIN tags t2 ON it.tag_id = t2.id
                    WHERE t2.path = t.path OR t2.path LIKE t.path || '.%') as count
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.parent_id = ?
            ORDER BY t.name
        """, (parent_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_descendants(self, tag_id: int) -> List[Dict]:
        """Get all descendants (children, grandchildren, etc.) of a tag."""
        tag = self.get_by_id(tag_id)
        if not tag:
            return []
        
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.path LIKE ? AND t.id != ?
            ORDER BY t.path
        """, (f"{tag['path']}.%", tag_id))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_ancestors(self, tag_id: int) -> List[Dict]:
        """Get all ancestors (parent, grandparent, etc.) of a tag."""
        tag = self.get_by_id(tag_id)
        if not tag or not tag['parent_id']:
            return []
        
        ancestors = []
        current_id = tag['parent_id']
        
        while current_id:
            parent = self.get_by_id(current_id)
            if parent:
                ancestors.insert(0, parent)
                current_id = parent['parent_id']
            else:
                break
        
        return ancestors
    
    def get_root_tags(self, category_id: Optional[int] = None) -> List[Dict]:
        """Get root tags (level 0) optionally filtered by category with count."""
        count_sql = """
            (SELECT COUNT(DISTINCT it.item_id) 
             FROM item_tags it
             JOIN tags t2 ON it.tag_id = t2.id
             WHERE t2.path = t.path OR t2.path LIKE t.path || '.%') as count
        """
        
        if category_id:
            cursor = self._execute(f"""
                SELECT t.*, c.name as category_name, c.color as category_color,
                       {count_sql}
                FROM tags t
                LEFT JOIN tag_categories c ON t.category_id = c.id
                WHERE t.level = 0 AND t.category_id = ?
                ORDER BY t.name
            """, (category_id,))
        else:
            cursor = self._execute(f"""
                SELECT t.*, c.name as category_name, c.color as category_color,
                       {count_sql}
                FROM tags t
                LEFT JOIN tag_categories c ON t.category_id = c.id
                WHERE t.level = 0
                ORDER BY t.category_id, t.name
            """)
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Search
    # =========================================================================
    
    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search tags by name with usage count (including inherited)."""
        # Count includes items where this tag OR any descendant is explicitly tagged
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color,
                   (
                       SELECT COUNT(DISTINCT it.item_id) 
                       FROM item_tags it
                       JOIN tags t2 ON it.tag_id = t2.id
                       WHERE t2.path = t.path OR t2.path LIKE t.path || '.%'
                   ) as count
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE t.name LIKE ? OR t.display_name LIKE ?
            ORDER BY count DESC, t.name
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_tree(self, category_slug: Optional[str] = None, 
                 parent_id: Optional[int] = None) -> List[Dict]:
        """Get tag tree structure.
        
        Returns hierarchical structure starting from parent_id or category roots.
        """
        if parent_id:
            return self.get_children(parent_id)
        
        if category_slug:
            cat = self.get_category_by_slug(category_slug)
            if cat:
                return self.get_root_tags(cat['id'])
        
        return self.get_root_tags()
    
    # =========================================================================
    # Item Tags - Store ONLY explicit tags (user input)
    # Ancestors are calculated on-the-fly
    # =========================================================================
    
    def get_item_tags_explicit(self, item_id: str) -> List[Dict]:
        """Get only explicit (user-added) tags for an item."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ?
            ORDER BY t.path
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_item_tags_with_ancestors(self, item_id: str) -> List[Dict]:
        """Get all tags for item including ancestors of explicit tags."""
        explicit = self.get_item_tags_explicit(item_id)
        
        # Collect all tags (explicit + ancestors)
        all_tags = []
        seen_ids = set()
        
        for tag in explicit:
            # Add ancestors first
            ancestors = self.get_ancestors(tag['id'])
            for ancestor in ancestors:
                if ancestor['id'] not in seen_ids:
                    ancestor['is_inherited'] = True
                    all_tags.append(ancestor)
                    seen_ids.add(ancestor['id'])
            
            # Add explicit tag
            if tag['id'] not in seen_ids:
                tag['is_inherited'] = False
                all_tags.append(tag)
                seen_ids.add(tag['id'])
        
        return all_tags
    
    def add_tag_to_item(self, item_id: str, tag_id: int) -> bool:
        """Add explicit tag to item. Returns True if added, False if already exists."""
        try:
            self._execute("""
                INSERT INTO item_tags (item_id, tag_id)
                VALUES (?, ?)
            """, (item_id, tag_id))
            
            # Update usage count
            self._execute("""
                UPDATE tags SET usage_count = usage_count + 1 WHERE id = ?
            """, (tag_id,))
            
            self._commit()
            return True
        except Exception:
            self._conn.rollback()
            return False
    
    def remove_tag_from_item(self, item_id: str, tag_id: int) -> bool:
        """Remove explicit tag from item."""
        self._execute("DELETE FROM item_tags WHERE item_id = ? AND tag_id = ?",
                     (item_id, tag_id))
        
        if self._conn.total_changes > 0:
            # Update usage count
            self._execute("""
                UPDATE tags SET usage_count = MAX(0, usage_count - 1) WHERE id = ?
            """, (tag_id,))
            self._commit()
            return True
        return False
    
    def get_items_by_tag(self, tag_id: int, folder_id: Optional[str] = None) -> List[Dict]:
        """Get items by tag (including descendants)."""
        tag = self.get_by_id(tag_id)
        if not tag:
            return []
        
        # Include tag and all descendants
        if folder_id:
            cursor = self._execute("""
                SELECT DISTINCT i.* 
                FROM items i
                JOIN item_tags it ON i.id = it.item_id
                JOIN tags t ON it.tag_id = t.id
                WHERE (t.id = ? OR t.path LIKE ?) AND i.folder_id = ?
                ORDER BY i.uploaded_at DESC
            """, (tag_id, f"{tag['path']}.%", folder_id))
        else:
            cursor = self._execute("""
                SELECT DISTINCT i.* 
                FROM items i
                JOIN item_tags it ON i.id = it.item_id
                JOIN tags t ON it.tag_id = t.id
                WHERE t.id = ? OR t.path LIKE ?
                ORDER BY i.uploaded_at DESC
            """, (tag_id, f"{tag['path']}.%"))
        
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
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def set_item_tags(self, item_id: str, tag_ids: List[int]) -> None:
        """Replace all explicit tags for item."""
        # Remove existing
        self._execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
        
        # Add new
        for tag_id in tag_ids:
            try:
                self._execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (item_id, tag_id))
                # Update usage count
                self._execute("UPDATE tags SET usage_count = usage_count + 1 WHERE id = ?",
                            (tag_id,))
            except Exception:
                pass  # Tag might not exist
        
        self._commit()
