"""Tag implication repository - directed edges for semantic inheritance."""
from typing import Optional, List, Dict, Set
from collections import defaultdict

from .base import Repository


class TagImplicationRepository(Repository):
    """Repository for tag implication graph operations."""

    def create(self, tag_id: int, implies_tag_id: int) -> int:
        """Create implication edge. Raises on cycle or duplicate."""
        if self._has_cycle_if_added(tag_id, implies_tag_id):
            raise ValueError("Adding this implication would create a cycle")
        cursor = self._execute(
            "INSERT INTO tag_implications (tag_id, implies_tag_id) VALUES (?, ?)",
            (tag_id, implies_tag_id)
        )
        self._commit()
        return cursor.lastrowid

    def delete(self, tag_id: int, implies_tag_id: int) -> bool:
        """Delete implication edge."""
        self._execute(
            "DELETE FROM tag_implications WHERE tag_id = ? AND implies_tag_id = ?",
            (tag_id, implies_tag_id)
        )
        self._commit()
        return self._conn.total_changes > 0

    def get_direct_implications(self, tag_ids: List[int]) -> Dict[int, List[int]]:
        """Map tag_id -> list of directly implied tag_ids."""
        if not tag_ids:
            return {}
        placeholders = ','.join('?' * len(tag_ids))
        cursor = self._execute(
            f"SELECT tag_id, implies_tag_id FROM tag_implications WHERE tag_id IN ({placeholders})",
            tuple(tag_ids)
        )
        result = defaultdict(list)
        for row in cursor.fetchall():
            result[row["tag_id"]].append(row["implies_tag_id"])
        return dict(result)

    def get_transitive_closure(self, tag_ids: Set[int]) -> Set[int]:
        """Return all tag IDs reachable from tag_ids via implication graph."""
        if not tag_ids:
            return set()
        # Use iterative approach to avoid deep recursion limits
        result = set()
        stack = list(tag_ids)
        visited = set()
        while stack:
            tid = stack.pop()
            if tid in visited:
                continue
            visited.add(tid)
            cursor = self._execute(
                "SELECT implies_tag_id FROM tag_implications WHERE tag_id = ?",
                (tid,)
            )
            for row in cursor.fetchall():
                implied = row["implies_tag_id"]
                if implied not in visited:
                    result.add(implied)
                    stack.append(implied)
        return result

    def get_implied_by(self, tag_id: int) -> List[int]:
        """Return tag IDs that directly imply this tag."""
        cursor = self._execute(
            "SELECT tag_id FROM tag_implications WHERE implies_tag_id = ?",
            (tag_id,)
        )
        return [row["tag_id"] for row in cursor.fetchall()]

    def get_all(self) -> List[Dict]:
        """Get all implication edges."""
        cursor = self._execute(
            "SELECT ti.*, t1.name as tag_name, t2.name as implies_name "
            "FROM tag_implications ti "
            "JOIN tags t1 ON ti.tag_id = t1.id "
            "JOIN tags t2 ON ti.implies_tag_id = t2.id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def _has_cycle_if_added(self, from_id: int, to_id: int) -> bool:
        """Check if adding from_id -> to_id would create a cycle."""
        if from_id == to_id:
            return True
        # If to_id can already reach from_id, adding edge creates cycle
        stack = [to_id]
        visited = set()
        while stack:
            tid = stack.pop()
            if tid == from_id:
                return True
            if tid in visited:
                continue
            visited.add(tid)
            cursor = self._execute(
                "SELECT implies_tag_id FROM tag_implications WHERE tag_id = ?",
                (tid,)
            )
            for row in cursor.fetchall():
                stack.append(row["implies_tag_id"])
        return False
