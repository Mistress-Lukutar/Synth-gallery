'''
File:   item_text_repository.py
Brief:  Repository for the item_texts detail table (note items).
Author: Mistress-Lukutar
Date:   2026-08-21
'''
from typing import Dict, Optional

from .base import Repository


class ItemTextRepository(Repository):
    """Repository for text-note item detail records."""

    def create(
        self,
        item_id: str,
        content_type: str,
        original_name: Optional[str] = None,
        encoding: str = 'utf-8',
        char_count: int = 0,
        line_count: int = 0,
    ) -> None:
        """Create the item_texts detail row for a note item.

        Args:
            item_id: Parent item UUID
            content_type: MIME type of the stored text
            original_name: Original upload filename
            encoding: Detected text encoding
            char_count: Decoded character count
            line_count: Line count
        """
        self._execute(
            """INSERT INTO item_texts
               (item_id, content_type, original_name, encoding,
                char_count, line_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (item_id, content_type, original_name, encoding,
             char_count, line_count),
        )
        self._commit()

    def get_by_item_id(self, item_id: str) -> Optional[Dict]:
        """Get text metadata for an item.

        Args:
            item_id: Item UUID

        Returns:
            Text metadata dict or None
        """
        cursor = self._execute(
            'SELECT * FROM item_texts WHERE item_id = ?',
            (item_id,),
        )
        return self._row_to_dict(cursor.fetchone())

    def update_stats(self, item_id: str, char_count: int, line_count: int) -> None:
        """Update content counters after a note content edit.

        Args:
            item_id: Item UUID
            char_count: New decoded character count
            line_count: New line count
        """
        self._execute(
            'UPDATE item_texts SET char_count = ?, line_count = ? '
            'WHERE item_id = ?',
            (char_count, line_count, item_id),
        )
        self._commit()

    def set_cover(self, item_id: str, cover_item_id: Optional[str]) -> None:
        """Set or clear the cover image reference for a note.

        Args:
            item_id: Note item UUID
            cover_item_id: Media item UUID to use as cover, or None to clear
        """
        self._execute(
            'UPDATE item_texts SET cover_item_id = ? WHERE item_id = ?',
            (cover_item_id, item_id),
        )
        self._commit()
