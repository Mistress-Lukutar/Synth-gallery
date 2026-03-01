"""Item media repository - photo/video specific data.

This repository handles the 'item_media' table which stores
media-specific data for photos and videos.
"""
from datetime import datetime
from typing import Optional, Dict

from .base import Repository


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
        filename: str,
        original_name: str = None,
        content_type: str = None,
        width: int = None,
        height: int = None,
        duration: int = None,  # For video
        thumb_width: int = None,
        thumb_height: int = None,
        taken_at: datetime = None,
        storage_mode: str = None
    ) -> bool:
        """Create media details for an item.
        
        Args:
            item_id: Reference to items.id
            media_type: 'image' or 'video'
            filename: Stored filename (UUID)
            original_name: Original filename
            content_type: MIME type
            width: Image/video width
            height: Image/video height
            duration: Video duration in seconds
            thumb_width: Thumbnail width
            thumb_height: Thumbnail height
            taken_at: EXIF capture date
            storage_mode: 'legacy' or 'envelope'
        """
        try:
            self._execute(
                """INSERT INTO item_media 
                   (item_id, media_type, filename, original_name, content_type,
                    width, height, duration, thumb_width, thumb_height, taken_at, storage_mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id, media_type, filename, original_name, content_type,
                    width, height, duration, thumb_width, thumb_height, taken_at, storage_mode
                )
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_by_item_id(self, item_id: str) -> Optional[Dict]:
        """Get media details by item ID."""
        cursor = self._execute(
            "SELECT * FROM item_media WHERE item_id = ?",
            (item_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def update(self, item_id: str, **kwargs) -> bool:
        """Update media details.
        
        Args:
            item_id: Item ID
            **kwargs: Fields to update
        """
        allowed_fields = {
            'filename', 'original_name', 'content_type',
            'width', 'height', 'duration',
            'thumb_width', 'thumb_height', 'taken_at', 'storage_mode'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
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
    
    def get_full_item(self, item_id: str) -> Optional[Dict]:
        """Get combined item + media data.
        
        Returns:
            Dict with both base item and media-specific fields
        """
        cursor = self._execute(
            """SELECT 
                i.*,
                im.media_type, im.filename, im.original_name, im.content_type,
                im.width, im.height, im.duration,
                im.thumb_width, im.thumb_height, im.taken_at, im.storage_mode
               FROM items i
               LEFT JOIN item_media im ON i.id = im.item_id
               WHERE i.id = ? AND i.type = 'media'""",
            (item_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_by_folder(self, folder_id: str, media_type: str = None) -> list:
        """Get media items in folder.
        
        Args:
            folder_id: Folder ID
            media_type: 'image', 'video', or None for all
        """
        if media_type:
            cursor = self._execute(
                """SELECT 
                    i.*,
                    im.media_type, im.filename, im.original_name, im.content_type,
                    im.width, im.height, im.duration,
                    im.thumb_width, im.thumb_height, im.taken_at, im.storage_mode
                   FROM items i
                   JOIN item_media im ON i.id = im.item_id
                   WHERE i.folder_id = ? AND i.type = 'media' AND im.media_type = ?
                   ORDER BY i.created_at DESC""",
                (folder_id, media_type)
            )
        else:
            cursor = self._execute(
                """SELECT 
                    i.*,
                    im.media_type, im.filename, im.original_name, im.content_type,
                    im.width, im.height, im.duration,
                    im.thumb_width, im.thumb_height, im.taken_at, im.storage_mode
                   FROM items i
                   JOIN item_media im ON i.id = im.item_id
                   WHERE i.folder_id = ? AND i.type = 'media'
                   ORDER BY i.created_at DESC""",
                (folder_id,)
            )
        return [dict(row) for row in cursor.fetchall()]
