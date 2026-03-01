"""Album repository - content-agnostic album management.

This repository handles albums and their relationships with items
via the album_items junction table.
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict

from .base import Repository


class AlbumRepository(Repository):
    """Repository for albums.
    
    Albums can contain any type of items (media, notes, etc.)
    via the album_items junction table.
    """
    
    def create(
        self,
        folder_id: str,
        user_id: int,
        name: str,
        album_id: str = None,
        cover_item_id: str = None,
        safe_id: str = None
    ) -> str:
        """Create a new album.
        
        Args:
            folder_id: Parent folder ID
            user_id: Owner user ID
            name: Album name
            album_id: Optional UUID
            cover_item_id: Optional cover item ID
            safe_id: Optional safe ID
            
        Returns:
            New album UUID
        """
        if album_id is None:
            album_id = str(uuid.uuid4())
        
        self._execute(
            """INSERT INTO albums 
               (id, name, folder_id, user_id, cover_item_id, safe_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                album_id, name, folder_id, user_id, 
                cover_item_id, safe_id, datetime.now()
            )
        )
        self._commit()
        return album_id
    
    def get_by_id(self, album_id: str) -> Optional[Dict]:
        """Get album by ID."""
        cursor = self._execute(
            """SELECT a.*, 
                (SELECT COUNT(*) FROM album_items WHERE album_id = a.id) as item_count,
                (SELECT item_id FROM album_items 
                 WHERE album_id = a.id ORDER BY position LIMIT 1) as first_item_id
               FROM albums a WHERE a.id = ?""",
            (album_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_folder(self, folder_id: str) -> List[Dict]:
        """Get albums in folder."""
        cursor = self._execute(
            """SELECT a.*, 
                (SELECT COUNT(*) FROM album_items WHERE album_id = a.id) as item_count
               FROM albums a 
               WHERE a.folder_id = ?
               ORDER BY a.created_at DESC""",
            (folder_id,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]
    
    def update(self, album_id: str, **kwargs) -> bool:
        """Update album fields."""
        allowed_fields = {'name', 'folder_id', 'cover_item_id'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [album_id]
        
        cursor = self._execute(
            f"UPDATE albums SET {set_clause} WHERE id = ?",
            tuple(values)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, album_id: str) -> bool:
        """Delete album (album_items deleted via CASCADE)."""
        cursor = self._execute(
            "DELETE FROM albums WHERE id = ?",
            (album_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    # =============================================================================
    # Album Items (junction table operations)
    # =============================================================================
    
    def add_item(self, album_id: str, item_id: str, position: int = None) -> bool:
        """Add item to album.
        
        Args:
            album_id: Album ID
            item_id: Item ID
            position: Optional position (auto-calculated if None)
        """
        if position is None:
            # Get next position
            cursor = self._execute(
                "SELECT MAX(COALESCE(position, 0)) as max_pos FROM album_items WHERE album_id = ?",
                (album_id,)
            )
            row = cursor.fetchone()
            position = (row["max_pos"] or 0) + 1
        
        try:
            self._execute(
                """INSERT INTO album_items (album_id, item_id, position, added_at)
                   VALUES (?, ?, ?, ?)""",
                (album_id, item_id, position, datetime.now())
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def remove_item(self, album_id: str, item_id: str) -> bool:
        """Remove item from album."""
        cursor = self._execute(
            "DELETE FROM album_items WHERE album_id = ? AND item_id = ?",
            (album_id, item_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def get_items(self, album_id: str) -> List[Dict]:
        """Get all items in album with their positions.
        
        Returns:
            List of items with additional fields: position, added_at
        """
        cursor = self._execute(
            """SELECT 
                i.*,
                ai.position,
                ai.added_at as added_to_album_at,
                im.media_type, im.filename, im.original_name, im.content_type,
                im.width, im.height, im.duration,
                im.thumb_width, im.thumb_height, im.taken_at
               FROM album_items ai
               JOIN items i ON ai.item_id = i.id
               LEFT JOIN item_media im ON i.id = im.item_id AND i.type = 'media'
               WHERE ai.album_id = ?
               ORDER BY ai.position, ai.added_at""",
            (album_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def reorder_items(self, album_id: str, item_ids: List[str]) -> bool:
        """Reorder items in album.
        
        Args:
            album_id: Album ID
            item_ids: Item IDs in desired order
        """
        try:
            for position, item_id in enumerate(item_ids):
                self._execute(
                    "UPDATE album_items SET position = ? WHERE album_id = ? AND item_id = ?",
                    (position, album_id, item_id)
                )
            self._commit()
            return True
        except Exception:
            return False
    
    def set_cover_item(self, album_id: str, item_id: str) -> bool:
        """Set album cover item."""
        return self.update(album_id, cover_item_id=item_id)
    
    def get_effective_cover(self, album_id: str) -> Optional[str]:
        """Get effective cover item ID.
        
        Returns:
            cover_item_id if set, else first item in album, else None
        """
        # Check explicit cover
        cursor = self._execute(
            "SELECT cover_item_id FROM albums WHERE id = ?",
            (album_id,)
        )
        row = cursor.fetchone()
        if row and row["cover_item_id"]:
            return row["cover_item_id"]
        
        # Return first item
        cursor = self._execute(
            """SELECT item_id FROM album_items 
               WHERE album_id = ? 
               ORDER BY position, added_at 
               LIMIT 1""",
            (album_id,)
        )
        row = cursor.fetchone()
        return row["item_id"] if row else None
    
    def move_to_folder(self, album_id: str, folder_id: str) -> bool:
        """Move album to different folder."""
        return self.update(album_id, folder_id=folder_id)
    
    def count_items(self, album_id: str) -> int:
        """Count items in album."""
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM album_items WHERE album_id = ?",
            (album_id,)
        )
        row = cursor.fetchone()
        return row["count"] if row else 0
