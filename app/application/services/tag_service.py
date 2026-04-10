"""Tag service - hierarchical tag management.

Handles business logic for tags including:
- Tag hierarchy operations
- Item tagging with automatic ancestor inclusion
- Search with negative filters
- Tree browsing
"""
from typing import Optional, List, Dict, Tuple

from fastapi import HTTPException

from ...infrastructure.repositories import TagsRepository


class TagService:
    """Service for tag management operations."""
    
    def __init__(self, tags_repository: TagsRepository):
        self.repo = tags_repository
    
    # ========================================================================
    # Categories
    # ========================================================================
    
    def get_categories(self) -> List[Dict]:
        """Get all tag categories."""
        return self.repo.get_categories()
    
    def get_category_by_slug(self, slug: str) -> Optional[Dict]:
        """Get category by slug."""
        return self.repo.get_category_by_slug(slug)
    
    # ========================================================================
    # Tag CRUD
    # ========================================================================
    
    def get_tag(self, tag_id: int) -> Optional[Dict]:
        """Get tag by ID."""
        return self.repo.get_by_id(tag_id)
    
    def create_tag(self, name: str, display_name: str, category_id: int,
                   parent_id: Optional[int] = None) -> Dict:
        """Create a new tag.
        
        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name
            category_id: Category ID
            parent_id: Parent tag ID (None for root)
            
        Returns:
            Created tag dict
        """
        # Validate name format
        name = name.lower().strip().replace(' ', '_')
        if not name or not name.replace('_', '').isalnum():
            raise HTTPException(400, "Invalid tag name. Use letters, numbers, underscores.")
        
        # Check if tag exists at this path
        existing = self.repo.get_by_path(
            f"{self.repo.get_by_id(parent_id)['path']}.{name}" if parent_id else name
        )
        if existing:
            raise HTTPException(400, "Tag already exists at this location")
        
        tag_id = self.repo.create(name, display_name or name.replace('_', ' ').title(),
                                  category_id, parent_id)
        return self.repo.get_by_id(tag_id)
    
    # ========================================================================
    # Hierarchy
    # ========================================================================
    
    def get_tree(self, category_slug: Optional[str] = None,
                 parent_id: Optional[int] = None) -> Dict:
        """Get tag tree for browsing.
        
        Returns:
            Dict with category and tags
        """
        category = None
        if category_slug:
            category = self.repo.get_category_by_slug(category_slug)
        
        tags = self.repo.get_tree(category_slug, parent_id)
        
        return {
            "category": category,
            "tags": tags
        }
    
    def get_tag_with_hierarchy(self, tag_id: int) -> Dict:
        """Get tag with full hierarchy info.
        
        Returns:
            Tag dict with ancestors and children
        """
        tag = self.repo.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        
        tag['ancestors'] = self.repo.get_ancestors(tag_id)
        tag['children'] = self.repo.get_children(tag_id)
        return tag
    
    # ========================================================================
    # Item Tagging
    # ========================================================================
    
    def get_item_tags(self, item_id: str) -> Dict:
        """Get tags for item - two lists: explicit (user) and inherited (calculated).
        
        Returns:
            Dict with explicit_tags, inherited_tags, and all_tags
        """
        # Explicit = stored in item_tags (user input)
        explicit = self.repo.get_item_tags_explicit(item_id)
        
        # Inherited = calculated from explicit (ancestors)
        all_with_ancestors = self.repo.get_item_tags_with_ancestors(item_id)
        inherited = [t for t in all_with_ancestors if t.get('is_inherited')]
        
        return {
            "explicit_tags": explicit,
            "inherited_tags": inherited,
            "all_tags": all_with_ancestors
        }
    
    def add_tag_to_item(self, item_id: str, tag_id: int) -> Dict:
        """Add explicit tag to item.
        
        Returns:
            Dict with updated tags
        """
        tag = self.repo.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        
        # Add explicit tag only
        self.repo.add_tag_to_item(item_id, tag_id)
        
        return {
            "added": [tag_id],
            "tags": self.get_item_tags(item_id)
        }
    
    def remove_tag_from_item(self, item_id: str, tag_id: int) -> Dict:
        """Remove explicit tag from item.
        
        Inherited tags are recalculated automatically.
        """
        tag = self.repo.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        
        # Remove explicit tag (inherited recalculated automatically)
        self.repo.remove_tag_from_item(item_id, tag_id)
        
        return {
            "removed": [tag_id],
            "tags": self.get_item_tags(item_id)
        }
    
    # ========================================================================
    # Search
    # ========================================================================
    
    def search_tags(self, query: str, limit: int = 10) -> List[Dict]:
        """Search tags with usage count."""
        if not query or len(query) < 2:
            return []
        return self.repo.search(query, limit)
    
    def parse_search_query(self, query: str) -> Tuple[List[str], List[str]]:
        """Parse search query into include and exclude tags.
        
        Args:
            query: Query string like "fox night -wolf"
            
        Returns:
            Tuple of (include_tags, exclude_tags)
        """
        parts = query.lower().split()
        include = []
        exclude = []
        
        for part in parts:
            if part.startswith('-'):
                exclude.append(part[1:])
            else:
                include.append(part)
        
        return include, exclude
    
    def search_items(self, query: str, folder_id: Optional[str] = None) -> Dict:
        """Search items by tags with negative support (AND between words, OR within word group).
        
        Args:
            query: Query string like "fox night -wolf"
            folder_id: Optional folder to search in
            
        Returns:
            Dict with items and search metadata
        """
        include, exclude = self.parse_search_query(query)
        
        if not include and not exclude:
            return {"items": [], "include": [], "exclude": [], "total": 0}
        
        # For each include word, collect tag IDs (word + descendants)
        include_groups = []  # List of sets, each set is tags for one word
        for name in include:
            tag_ids = set()
            tags = self.repo.get_by_name(name)
            for tag in tags:
                tag_ids.add(tag['id'])
                # Add descendants
                for desc in self.repo.get_descendants(tag['id']):
                    tag_ids.add(desc['id'])
            if tag_ids:
                include_groups.append(tag_ids)
        
        # For exclude, collect all tag IDs (OR logic within exclude)
        exclude_ids = set()
        for name in exclude:
            tags = self.repo.get_by_name(name)
            for tag in tags:
                exclude_ids.add(tag['id'])
                for desc in self.repo.get_descendants(tag['id']):
                    exclude_ids.add(desc['id'])
        
        # Build query
        items = self._execute_tag_search(include_groups, exclude_ids, folder_id)
        
        return {
            "items": items,
            "include": include,
            "exclude": exclude,
            "total": len(items)
        }
    
    def _execute_tag_search(self, include_groups: list, exclude_ids: set,
                           folder_id: Optional[str]) -> List[Dict]:
        """Execute tag search query with media metadata.
        
        Args:
            include_groups: List of sets, each set contains tag IDs for one search word
            exclude_ids: Set of tag IDs to exclude
        """
        items = self.repo.search_items_by_tags(include_groups, exclude_ids, folder_id)
        
        for item in items:
            item['type'] = 'photo'
        
        return items
    
    # ========================================================================
    # Bulk Operations
    # ========================================================================
    
    def batch_tag_items(self, item_ids: List[str], add_tag_ids: List[int],
                       remove_tag_ids: List[int]) -> Dict:
        """Batch add/remove tags from items.
        
        Returns:
            Dict with counts of affected items
        """
        added_count = 0
        removed_count = 0
        
        for item_id in item_ids:
            for tag_id in add_tag_ids:
                if self.repo.add_tags_with_ancestors(item_id, tag_id):
                    added_count += 1
            
            for tag_id in remove_tag_ids:
                if self.repo.remove_tag_from_item(item_id, tag_id):
                    removed_count += 1
        
        return {
            "items_processed": len(item_ids),
            "tags_added": added_count,
            "tags_removed": removed_count
        }
