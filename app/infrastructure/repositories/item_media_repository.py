"""Item media repository - photo/video specific data.

This repository handles the 'item_media' table which stores
media-specific data for photos and videos.
"""
import json
from datetime import datetime
from typing import Optional, Dict

from ...logging_config import get_logger
from .base import Repository

logger = get_logger(__name__)


class ItemMediaRepository(Repository):
    """Repository for media items (photos and videos).
    
    Works in conjunction with ItemRepository:
    - ItemRepository handles base metadata (items table)
    - ItemMediaRepository handles media specifics (item_media table)
    """
    
    def create(
        self,
        item_id: str,
        media_type: str,  # 'image' or 'video'
        original_name: str = None,
        content_type: str = None,
        width: int = None,
        height: int = None,
        duration: int = None,  # For video
        thumb_width: int = None,
        thumb_height: int = None,
        taken_at: datetime = None,
        file_size: int = None,
        png_text_chunks: dict = None
    ) -> bool:
        """Create media details for an item.
        
        Args:
            item_id: Reference to items.id (also used as filename in storage)
            media_type: 'image' or 'video'
            original_name: Original filename
            content_type: MIME type
            width: Image/video width
            height: Image/video height
            duration: Video duration in seconds
            thumb_width: Thumbnail width
            thumb_height: Thumbnail height
            taken_at: EXIF capture date
            file_size: File size in bytes
            png_text_chunks: PNG tEXt/zTXt/iTXt chunks extracted from original upload
        """
        try:
            # Storage key is the item_id (extension-less); the redundant
            # ``filename`` column was dropped in the v2.0 schema migration.
            self._execute(
                """INSERT INTO item_media
                   (item_id, media_type, original_name, content_type,
                    width, height, duration, thumb_width, thumb_height, taken_at, file_size,
                    png_text_chunks)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id, media_type, original_name, content_type,
                    width, height, duration, thumb_width, thumb_height, taken_at, file_size,
                    json.dumps(png_text_chunks, ensure_ascii=False) if png_text_chunks else None
                )
            )
            self._commit()
            return True
        except Exception:
            logger.exception("Failed to create media record for item %s", item_id)
            return False
    
    def get_by_item_id(self, item_id: str) -> Optional[Dict]:
        """Get media details by item ID."""
        cursor = self._execute(
            "SELECT * FROM item_media WHERE item_id = ?",
            (item_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        media = dict(row)
        if media.get('png_text_chunks'):
            try:
                media['png_text_chunks'] = json.loads(media['png_text_chunks'])
            except json.JSONDecodeError:
                media['png_text_chunks'] = None
        return media
    
    def update(self, item_id: str, **kwargs) -> bool:
        """Update media details.
        
        Args:
            item_id: Item ID
            **kwargs: Fields to update
        """
        allowed_fields = {
            'original_name', 'content_type',
            'width', 'height', 'duration',
            'thumb_width', 'thumb_height', 'taken_at',
            'file_size', 'png_text_chunks'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        # Convert png_text_chunks dict to JSON
        if 'png_text_chunks' in updates and updates['png_text_chunks'] is not None:
            updates['png_text_chunks'] = json.dumps(updates['png_text_chunks'], ensure_ascii=False)
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [item_id]
        
        cursor = self._execute(
            f"UPDATE item_media SET {set_clause} WHERE item_id = ?",
            tuple(values)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def update_thumbnail_dimensions(
        self, 
        item_id: str, 
        thumb_width: int, 
        thumb_height: int
    ) -> bool:
        """Update thumbnail dimensions."""
        return self.update(item_id, thumb_width=thumb_width, thumb_height=thumb_height)
    
    def delete(self, item_id: str) -> bool:
        """Delete media details."""
        cursor = self._execute(
            "DELETE FROM item_media WHERE item_id = ?",
            (item_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def update_taken_at(self, item_id: str, taken_at: datetime) -> bool:
        """Update the taken_at field for a media item.
        
        Args:
            item_id: Item ID
            taken_at: New capture date/timestamp
            
        Returns:
            True if updated
        """
        cursor = self._execute(
            "UPDATE item_media SET taken_at = ? WHERE item_id = ?",
            (taken_at, item_id)
        )
        self._commit()
        return cursor.rowcount > 0
