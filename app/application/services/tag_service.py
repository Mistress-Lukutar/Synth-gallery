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

    # ========================================================================
    # Tag CRUD
    # ========================================================================

    def get_tag(self, tag_id: int) -> Optional[Dict]:
        """Get tag by ID."""
        return self.tags.get_by_id(tag_id)

    def create_tag(self, name: str, display_name: str, category_id: int) -> Dict:
        """Create a new flat tag.

        Args:
            name: Tag name (lowercase, underscore)
            display_name: Display name
            category_id: Category ID

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

        tag_id = self.tags.create(name, display_name or name.replace('_', ' ').title(), category_id)
        return self.tags.get_by_id(tag_id)

    # ========================================================================
    # Item Tagging
    # ========================================================================

    def get_item_tags(self, item_id: str) -> Dict:
        """Get tags for item - explicit (user) and implied (auto).

        Returns:
            Dict with explicit_tags, implied_tags, and all_tags
        """
        explicit = self.tags.get_item_tags_explicit(item_id)
        implied = self.tags.get_item_tags_implied(item_id)
        all_tags = self.tags.get_item_tags_all(item_id)

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

        return self.set_item_tags(item_id, current_ids)

    def remove_tag_from_item(self, item_id: str, tag_id: int) -> Dict:
        """Remove explicit tag from item and recalculate implied tags."""
        tag = self.tags.get_by_id(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")

        current_explicit = self.tags.get_item_tags_explicit(item_id)
        current_ids = [t["id"] for t in current_explicit if t["id"] != tag_id]

        return self.set_item_tags(item_id, current_ids)

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
