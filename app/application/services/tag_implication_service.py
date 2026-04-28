"""Tag implication service - resolves semantic inheritance graph."""
from typing import Set, List, Dict

from ...infrastructure.repositories import TagImplicationRepository


class TagImplicationService:
    """Service for tag implication resolution and validation."""

    def __init__(self, implication_repo: TagImplicationRepository):
        self.repo = implication_repo

    def resolve(self, tag_ids: Set[int]) -> Set[int]:
        """Return all implied tag IDs reachable from the given tag IDs.

        Uses transitive closure over the implication graph.
        """
        return self.repo.get_transitive_closure(tag_ids)

    def resolve_with_sources(self, tag_ids: Set[int]) -> Dict[int, List[int]]:
        """Return mapping: implied_tag_id -> list of source tag_ids that caused it.

        Useful for UI "Implied by: sea, ocean" tooltips.
        """
        if not tag_ids:
            return {}

        # Build full closure while tracking sources
        sources: Dict[int, List[int]] = {}
        visited = set()
        stack = list(tag_ids)

        while stack:
            tid = stack.pop()
            if tid in visited:
                continue
            visited.add(tid)

            direct = self.repo.get_direct_implications([tid])
            for implied in direct.get(tid, []):
                if implied not in tag_ids:
                    if implied not in sources:
                        sources[implied] = []
                    if tid in tag_ids:
                        sources[implied].append(tid)
                    else:
                        # tid itself was implied by something else; inherit sources
                        parent_sources = sources.get(tid, [tid])
                        for ps in parent_sources:
                            if ps not in sources[implied]:
                                sources[implied].append(ps)
                    if implied not in visited:
                        stack.append(implied)

        return sources

    def validate_cycle(self, tag_id: int, implies_tag_id: int) -> bool:
        """Check if adding tag_id -> implies_tag_id would create a cycle.

        Returns True if safe (no cycle), raises ValueError otherwise.
        """
        try:
            self.repo.create(tag_id, implies_tag_id)
            return True
        except ValueError as e:
            raise ValueError(f"Cycle detected: {e}")
