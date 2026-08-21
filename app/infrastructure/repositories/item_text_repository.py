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
