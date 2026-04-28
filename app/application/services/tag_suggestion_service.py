"""Tag suggestion service - related tags and contextual recommendations."""
from typing import List, Dict, Optional

from ...infrastructure.repositories import TagCooccurrenceRepository, TagsRepository


class TagSuggestionService:
    """Service for tag recommendations based on co-occurrence statistics."""

    def __init__(self,
                 cooccurrence_repo: TagCooccurrenceRepository,
                 tags_repo: TagsRepository):
        self.cooccurrence = cooccurrence_repo
        self.tags = tags_repo

    def get_related_for_tag(self, tag_id: int, limit: int = 8,
                            exclude_ids: Optional[List[int]] = None) -> List[Dict]:
        """Get tags most frequently co-occurring with a single tag."""
        return self.cooccurrence.get_related_tags(tag_id, limit, exclude_ids)

    def get_contextual_suggestions(
        self,
        selected_tag_ids: List[int],
        limit: int = 8,
        exclude_ids: Optional[List[int]] = None
    ) -> List[Dict]:
        """Get suggestions ranked by relevance to all currently selected tags."""
        return self.cooccurrence.get_contextual_suggestions(
            selected_tag_ids, limit, exclude_ids
        )

    def get_suggestions_for_item(self, item_id: str, limit: int = 8) -> List[Dict]:
        """Get related suggestions for an item based on its current explicit tags."""
        explicit = self.tags.get_item_tags_explicit(item_id)
        explicit_ids = [t["id"] for t in explicit]
        if not explicit_ids:
            return []
        all_current = self.tags.get_item_tags_all(item_id)
        exclude_ids = [t["id"] for t in all_current]
        return self.get_contextual_suggestions(explicit_ids, limit, exclude_ids)

    def update_cooccurrence_for_item(self, item_id: str) -> None:
        """Rebuild co-occurrence counts after item tagging changes.

        Typically called after set_item_tags to update statistics.
        """
        tags = self.tags.get_item_tags_all(item_id)
        tag_ids = [t["id"] for t in tags]
        self.cooccurrence.increment_many(tag_ids)
