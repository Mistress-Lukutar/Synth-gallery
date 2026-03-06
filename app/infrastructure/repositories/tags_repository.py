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
        """Get direct children of a tag."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
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
        """Get root tags (level 0) optionally filtered by category."""
        if category_id:
            cursor = self._execute("""
                SELECT t.*, c.name as category_name, c.color as category_color
                FROM tags t
                LEFT JOIN tag_categories c ON t.category_id = c.id
                WHERE t.level = 0 AND t.category_id = ?
                ORDER BY t.name
            """, (category_id,))
        else:
            cursor = self._execute("""
                SELECT t.*, c.name as category_name, c.color as category_color
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
        """Search tags by name with usage count."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color,
                   COUNT(it.item_id) as count
            FROM tags t
            LEFT JOIN tag_categories c ON t.category_id = c.id
            LEFT JOIN item_tags it ON t.id = it.tag_id
            WHERE t.name LIKE ? OR t.display_name LIKE ?
            GROUP BY t.id
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
    # Item Tags
    # =========================================================================
    
    def get_item_tags(self, item_id: str) -> List[Dict]:
        """Get all tags for an item with full hierarchy info."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color,
                   it.is_explicit, it.added_at
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ?
            ORDER BY t.path
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def add_tag_to_item(self, item_id: str, tag_id: int, 
                        added_by_user: bool = True) -> bool:
        """Add tag to item. Returns True if added, False if already exists."""
        try:
            self._execute("""
                INSERT INTO item_tags (item_id, tag_id, is_explicit)
                VALUES (?, ?, ?)
            """, (item_id, tag_id, 1 if added_by_user else 0))
            
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
        """Remove tag from item."""
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
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def add_tags_with_ancestors(self, item_id: str, tag_id: int) -> List[int]:
        """Add tag and all its ancestors to item.
        
        Returns list of added tag IDs (including ancestors).
        """
        added = []
        
        # Get tag and all ancestors
        tag = self.get_by_id(tag_id)
        if not tag:
            return added
        
        # Add ancestors first (root to leaf) - as non-explicit
        ancestors = self.get_ancestors(tag_id)
        for ancestor in ancestors:
            if self.add_tag_to_item(item_id, ancestor['id'], added_by_user=False):
                added.append(ancestor['id'])
        
        # Add the tag itself - as explicit
        if self.add_tag_to_item(item_id, tag_id, added_by_user=True):
            added.append(tag_id)
        
        return added
    
    def get_explicit_item_tags(self, item_id: str) -> List[Dict]:
        """Get only explicitly added tags for an item."""
        cursor = self._execute("""
            SELECT t.*, c.name as category_name, c.color as category_color
            FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            LEFT JOIN tag_categories c ON t.category_id = c.id
            WHERE it.item_id = ? AND it.is_explicit = 1
            ORDER BY t.path
        """, (item_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def is_ancestor_of_explicit(self, item_id: str, tag_id: int) -> bool:
        """Check if tag is an ancestor of any explicit tag in item.
        
        Returns True if this tag is required by another explicit tag.
        """
        tag = self.get_by_id(tag_id)
        if not tag:
            return False
        
        # Find all explicit tags in item that have this tag as ancestor
        cursor = self._execute("""
            SELECT 1 FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            WHERE it.item_id = ? 
              AND it.is_explicit = 1
              AND t.path LIKE ?
              AND t.id != ?
            LIMIT 1
        """, (item_id, f"{tag['path']}.%", tag_id))
        
        return cursor.fetchone() is not None
    
    def get_descendants_in_item(self, item_id: str, tag_id: int) -> List[int]:
        """Get all descendant tag IDs that are in item."""
        tag = self.get_by_id(tag_id)
        if not tag:
            return []
        
        cursor = self._execute("""
            SELECT t.id FROM item_tags it
            JOIN tags t ON it.tag_id = t.id
            WHERE it.item_id = ? AND t.path LIKE ?
        """, (item_id, f"{tag['path']}.%"))
        
        return [row[0] for row in cursor.fetchall()]
