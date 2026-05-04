"""Tag service - flat tags with implication-based inheritance and suggestions."""
from typing import Optional, List, Dict, Tuple

from fastapi import HTTPException

from ...infrastructure.repositories import (
    TagsRepository,
    TagImplicationRepository,
    TagCooccurrenceRepository,
)


class TagService:
    """Service for tag management operations."""

    def __init__(self,
                 tags_repo: TagsRepository,
                 implication_repo: Optional[TagImplicationRepository] = None,
                 cooccurrence_repo: Optional[TagCooccurrenceRepository] = None):
        self.tags = tags_repo
        self.implications = implication_repo
        self.cooccurrence = cooccurrence_repo

    # ========================================================================
    # Categories
    # ========================================================================

    def get_categories(self) -> List[Dict]:
        """Get all tag categories."""
        return self.tags.get_categories()

    def get_category_by_slug(self, slug: str) -> Optional[Dict]:
        """Get category by slug."""
        return self.tags.get_category_by_slug(slug)

    def create_category(self, name: str, color: str, sort_order: Optional[int] = None) -> Dict:
        """Create a new tag category."""
        slug = name.lower().strip().replace(' ', '_')
        if not slug or not slug.replace('_', '').isalnum():
            raise HTTPException(400, "Invalid category name")
        existing = self.tags.get_category_by_slug(slug)
        if existing:
            raise HTTPException(400, "Category already exists")
        if sort_order is None:
            categories = self.tags.get_categories()
            sort_order = max((c.get('sort_order', 0) for c in categories), default=0) + 1
        cat_id = self.tags.create_category(name, slug, color, sort_order)
        cat = self.tags.get_category_by_id(cat_id)
        return cat

    def update_category(self, category_id: int, name: Optional[str] = None,
                        color: Optional[str] = None,
                        sort_order: Optional[int] = None) -> Dict:
        """Update category fields."""
        cat = self.tags.get_category_by_id(category_id)
        if not cat:
            raise HTTPException(404, "Category not found")
        updates = {}
        if name is not None:
            updates["name"] = name
            updates["slug"] = name.lower().strip().replace(' ', '_')
        if color is not None:
            updates["color"] = color
        if sort_order is not None:
            updates["sort_order"] = sort_order
        self.tags.update_category(category_id, **updates)
        return self.tags.get_category_by_id(category_id)

    def delete_category(self, category_id: int) -> bool:
        """Delete a category if it has no tags."""
        cat = self.tags.get_category_by_id(category_id)
        if not cat:
            raise HTTPException(404, "Category not found")
        try:
            return self.tags.delete_category(category_id)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))

    # ========================================================================
    # Tag CRUD
    # ========================================================================

    def get_tag(self, tag_id: int) -> Optional[Dict]:
        """Get tag by ID."""
        return self.tags.get_by_id(tag_id)

    def create_tag(self, name: str, display_name: str, category_id: int, description: str = '') -> Dict:
        """Create a new flat tag.

        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name
            category_id: Category ID
            description: Markdown description

        Returns:
            Created tag dict
        """
        name = name.lower().strip().replace(' ', '_')
        if not name or not name.replace('_', '').isalnum():
            raise HTTPException(400, "Invalid tag name. Use letters, numbers, underscores.")

        # Check if tag already exists
        existing = self.tags.get_by_name(name)
        if existing:
            raise HTTPException(400, "Tag already exists")

        tag_id = self.tags.create(name, display_name or name.replace('_', ' ').title(), category_id, description)
        return self.tags.get_by_id(tag_id)

    # ========================================================================
    # Item Tagging
    # ========================================================================

    def get_item_tags(self, item_id: str) -> Dict:
        """Get tags for item - explicit (user) and implied (auto).

        Implied tags are ordered by BFS depth from explicit tags
        (closest implications first), so the display order is natural.

        Returns:
            Dict with explicit_tags, implied_tags, and all_tags
        """
        explicit = self.tags.get_item_tags_explicit(item_id)
        implied = self.tags.get_item_tags_implied(item_id)
        all_tags = self.tags.get_item_tags_all(item_id)

        # Sort implied by BFS depth from explicit tags
        if implied and self.implications:
            explicit_ids = {t["id"] for t in explicit}
            implied_ids = {t["id"] for t in implied}

            depth_map = {}
            queue = list(explicit_ids)
            visited = set(explicit_ids)
            current_depth = 0
            while queue:
                next_queue = []
                direct = self.implications.get_direct_implications(queue)
                for tid, implied_list in direct.items():
                    for next_tid in implied_list:
                        if next_tid not in visited and next_tid in implied_ids:
                            visited.add(next_tid)
                            depth_map[next_tid] = current_depth + 1
                            next_queue.append(next_tid)
                queue = next_queue
                current_depth += 1

            def sort_key(tag):
                if tag["is_explicit"]:
                    return (0, 0, tag["name"])
                return (1, depth_map.get(tag["id"], 999), tag["name"])

            all_tags = sorted(all_tags, key=sort_key)

        return {
            "explicit_tags": explicit,
            "implied_tags": implied,
            "all_tags": all_tags
        }

    def set_item_tags(self, item_id: str, explicit_tag_ids: List[int]) -> Dict:
        """Replace all explicit tags for item and resolve implications.

        Returns:
            Dict with updated tags
        """
        if self.implications is None:
            # Fallback: no implications configured
            self.tags.set_item_tags(item_id, explicit_tag_ids, [])
            return self.get_item_tags(item_id)

        # Resolve transitive implications
        implied_ids = self.implications.get_transitive_closure(set(explicit_tag_ids))
        # Remove explicit tags that are also implied (implied wins for is_explicit=0)
        implied_ids = implied_ids - set(explicit_tag_ids)

        self.tags.set_item_tags(item_id, explicit_tag_ids, list(implied_ids))

        # Update co-occurrence statistics
        if self.cooccurrence is not None:
            all_ids = list(set(explicit_tag_ids) | implied_ids)
            self.cooccurrence.increment_many(all_ids)

        return self.get_item_tags(item_id)

    def add_tag_to_item(self, item_id: str, tag_id: int) -> Dict:
        """Add explicit tag to item (append mode).

        Returns:
            Dict with updated tags
        """
        tag = self.tags.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")

        current_explicit = self.tags.get_item_tags_explicit(item_id)
        current_ids = [t["id"] for t in current_explicit]

        if tag_id not in current_ids:
            current_ids.append(tag_id)

        return {
            "added": [tag_id],
            "tags": self.set_item_tags(item_id, current_ids)
        }

    def remove_tag_from_item(self, item_id: str, tag_id: int) -> Dict:
        """Remove explicit tag from item and recalculate implied tags."""
        tag = self.tags.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")

        current_explicit = self.tags.get_item_tags_explicit(item_id)
        current_ids = [t["id"] for t in current_explicit if t["id"] != tag_id]

        return {
            "removed": [tag_id],
            "tags": self.set_item_tags(item_id, current_ids)
        }

    # ========================================================================
    # Suggestions
    # ========================================================================

    def get_related_tags(self, tag_id: int, limit: int = 8) -> List[Dict]:
        """Get tags frequently co-occurring with this tag."""
        if self.cooccurrence is None:
            return []
        all_current = self.tags.get_item_tags_all(item_id=None)  # Not needed here
        # Actually get_related_tags doesn't need item context
        return self.cooccurrence.get_related_tags(tag_id, limit)

    def get_contextual_suggestions(self,
                                   selected_tag_ids: List[int],
                                   limit: int = 8) -> List[Dict]:
        """Get suggestions based on currently selected tags."""
        if self.cooccurrence is None:
            return []
        return self.cooccurrence.get_contextual_suggestions(selected_tag_ids, limit)

    def get_tag_implications(self, tag_id: int) -> Dict:
        """Get implications for a tag."""
        if self.implications is None:
            return {"implies": [], "implied_by": []}

        direct = self.implications.get_direct_implications([tag_id])
        implied_by = self.implications.get_implied_by(tag_id)

        implies_tags = self.tags.get_tags_by_ids(direct.get(tag_id, []))
        implied_by_tags = self.tags.get_tags_by_ids(implied_by)

        return {
            "implies": implies_tags,
            "implied_by": implied_by_tags
        }

    def list_tags(self, query: Optional[str] = None, limit: int = 50,
                  offset: int = 0, category_id: Optional[int] = None) -> Dict:
        """Get paginated tag list with implication counts."""
        items = self.tags.list_tags(query, limit, offset, category_id)
        total = self.tags.count_tags(query, category_id)

        # Enrich with implication counts
        for tag in items:
            if self.implications:
                tag["implies_count"] = self.implications.get_implications_count(tag["id"])
                tag["implied_by_count"] = self.implications.get_implied_by_count(tag["id"])
            else:
                tag["implies_count"] = 0
                tag["implied_by_count"] = 0

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def update_tag(self, tag_id: int, name: Optional[str] = None,
                   display_name: Optional[str] = None,
                   category_id: Optional[int] = None,
                   description: Optional[str] = None) -> Dict:
        """Update tag fields."""
        tag = self.tags.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")

        updates = {}
        if name is not None:
            name = name.lower().strip().replace(' ', '_')
            if not name or not name.replace('_', '').isalnum():
                raise HTTPException(400, "Invalid tag name")
            updates["name"] = name
        if display_name is not None:
            updates["display_name"] = display_name
        if category_id is not None:
            updates["category_id"] = category_id
        if description is not None:
            updates["description"] = description

        self.tags.update_tag(tag_id, **updates)
        return self.tags.get_by_id(tag_id)

    def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag."""
        tag = self.tags.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        return self.tags.delete_tag(tag_id)

    def create_implication(self, tag_id: int, implies_tag_id: int) -> Dict:
        """Create implication edge with cycle validation."""
        if self.implications is None:
            raise HTTPException(500, "Implication service not configured")

        tag = self.tags.get_by_id(tag_id)
        implied = self.tags.get_by_id(implies_tag_id)
        if not tag or not implied:
            raise HTTPException(404, "Tag not found")

        self.implications.create(tag_id, implies_tag_id)
        return self.get_tag_implications(tag_id)

    def delete_implication(self, tag_id: int, implies_tag_id: int) -> bool:
        """Delete implication edge."""
        if self.implications is None:
            raise HTTPException(500, "Implication service not configured")
        return self.implications.delete(tag_id, implies_tag_id)

    # ========================================================================
    # Search
    # ========================================================================

    def search_tags(self, query: str, limit: int = 10) -> List[Dict]:
        """Search tags with usage count."""
        if not query or len(query) < 2:
            return []
        return self.tags.search(query, limit)

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

        # For each include word, collect tag IDs (exact match only — implied are materialized)
        include_groups = []  # List of sets, each set is tags for one word
        for name in include:
            tag_ids = set()
            tags = self.tags.get_by_name(name)
            for tag in tags:
                tag_ids.add(tag['id'])
            if tag_ids:
                include_groups.append(tag_ids)

        # For exclude, collect all tag IDs (OR logic within exclude)
        exclude_ids = set()
        for name in exclude:
            tags = self.tags.get_by_name(name)
            for tag in tags:
                exclude_ids.add(tag['id'])

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
        """Execute tag search query with media metadata."""
        items = self.tags.search_items_by_tags(include_groups, exclude_ids, folder_id)

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
                try:
                    self.add_tag_to_item(item_id, tag_id)
                    added_count += 1
                except HTTPException:
                    pass

            for tag_id in remove_tag_ids:
                try:
                    self.remove_tag_from_item(item_id, tag_id)
                    removed_count += 1
                except HTTPException:
                    pass

        return {
            "items_processed": len(item_ids),
            "tags_added": added_count,
            "tags_removed": removed_count
        }
