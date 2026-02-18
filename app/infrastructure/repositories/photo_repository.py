"""Photo repository - handles all photo-related database operations.

Manages photos and videos including:
- File metadata and storage references
- Album membership and ordering
- Encryption status
- EXIF metadata (taken_at dates)
- Thumbnail tracking
"""
import uuid
from datetime import datetime
from typing import Optional
from .base import Repository, AsyncRepository, AsyncRepository


class PhotoRepository(Repository):
    """Repository for photo/video entity operations.
    
    Photos can be:
    - Standalone (directly in folder)
    - In albums (with position ordering)
    - Encrypted (is_encrypted flag)
    - In safes (safe_id reference)
    
    Examples:
        >>> repo = PhotoRepository(db)
        >>> photo_id = repo.create(filename, folder_id, user_id, 
        ...                        media_type="image", thumb_width=400)
        >>> photos = repo.get_by_folder(folder_id, sort_by="taken")
    """
    
    def create(
        self,
        filename: str,
        folder_id: str,
        user_id: int,
        photo_id: str = None,
        original_name: str = None,
        media_type: str = "image",
        album_id: str = None,
        position: int = 0,
        taken_at: datetime = None,
        is_encrypted: bool = False,
        thumb_width: int = None,
        thumb_height: int = None,
        safe_id: str = None
    ) -> str:
        """Create new photo record.
        
        Args:
            filename: Stored filename (UUID + ext)
            folder_id: Parent folder ID
            user_id: Uploading user ID
            photo_id: Optional photo UUID (generated if not provided)
            original_name: Original filename
            media_type: 'image' or 'video'
            album_id: Album ID (if in album)
            position: Position in album
            taken_at: EXIF capture date
            is_encrypted: Encryption status
            thumb_width: Thumbnail width
            thumb_height: Thumbnail height
            safe_id: Safe ID (if in encrypted safe)
            
        Returns:
            New photo UUID
        """
        if photo_id is None:
            photo_id = str(uuid.uuid4())
        
        self._execute(
            """INSERT INTO photos 
               (id, filename, original_name, folder_id, user_id,
                media_type, album_id, position, taken_at,
                is_encrypted, thumb_width, thumb_height, safe_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                photo_id, filename, original_name, folder_id, user_id,
                media_type, album_id, position, taken_at,
                1 if is_encrypted else 0, thumb_width, thumb_height, safe_id
            )
        )
        self._commit()
        return photo_id
    
    def get_by_id(self, photo_id: str) -> dict | None:
        """Get photo by ID.
        
        Args:
            photo_id: Photo UUID
            
        Returns:
            Photo dict or None
        """
        cursor = self._execute(
            "SELECT * FROM photos WHERE id = ?",
            (photo_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_filename(self, filename: str) -> dict | None:
        """Get photo by stored filename.
        
        Args:
            filename: Stored filename
            
        Returns:
            Photo dict or None
        """
        cursor = self._execute(
            "SELECT * FROM photos WHERE filename = ?",
            (filename,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_folder(
        self,
        folder_id: str,
        album_id: str = None,
        sort_by: str = "uploaded",
        include_subfolders: bool = False
    ) -> list[dict]:
        """Get photos in folder.
        
        Args:
            folder_id: Folder ID
            album_id: Filter by album (None for standalone, 'all' for all)
            sort_by: 'uploaded' or 'taken'
            include_subfolders: Include photos from subfolders
            
        Returns:
            List of photo dicts
        """
        if include_subfolders:
            # Recursive CTE for subfolders
            folder_filter = """folder_id IN (
                WITH RECURSIVE subfolder_tree AS (
                    SELECT id FROM folders WHERE id = ?
                    UNION ALL
                    SELECT f.id FROM folders f 
                    JOIN subfolder_tree st ON f.parent_id = st.id
                )
                SELECT id FROM subfolder_tree
            )"""
            params = [folder_id]
        else:
            folder_filter = "folder_id = ?"
            params = [folder_id]
        
        # Album filter
        if album_id == 'all':
            album_filter = ""
        elif album_id is None:
            album_filter = "AND album_id IS NULL"
        else:
            album_filter = "AND album_id = ?"
            params.append(album_id)
        
        # Sort order
        if sort_by == "taken":
            order_by = "COALESCE(taken_at, uploaded_at) DESC"
        else:
            order_by = "uploaded_at DESC"
        
        cursor = self._execute(
            f"""SELECT * FROM photos 
                WHERE {folder_filter} {album_filter}
                ORDER BY {order_by}""",
            tuple(params)
        )
        return [dict(row) for row in cursor.fetchall()]


# =============================================================================
# ASYNC VERSION (Issue #15)
# =============================================================================

class AsyncPhotoRepository(AsyncRepository):
    """Async repository for photo operations."""
    
    async def get_by_id(self, photo_id: str) -> dict | None:
        """Get photo by ID.
        
        Args:
            photo_id: Photo UUID
            
        Returns:
            Photo dict or None
        """
        return await self._fetchone(
            "SELECT * FROM photos WHERE id = ?",
            (photo_id,)
        )
    
    async def create(
        self,
        photo_id: str,
        filename: str,
        user_id: int,
        folder_id: str | None = None,
        original_name: str = None,
        media_type: str = "image",
        album_id: str = None,
        position: int = 0,
        taken_at: datetime = None,
        is_encrypted: bool = False,
        thumb_width: int = None,
        thumb_height: int = None,
        safe_id: str = None
    ) -> str:
        """Create new photo record.
        
        Args:
            photo_id: Photo UUID
            filename: Stored filename (UUID + ext)
            user_id: Uploading user ID
            folder_id: Parent folder ID
            original_name: Original filename
            media_type: 'image' or 'video'
            album_id: Album ID (if in album)
            position: Position in album
            taken_at: EXIF capture date
            is_encrypted: Encryption status
            thumb_width: Thumbnail width
            thumb_height: Thumbnail height
            safe_id: Safe ID (if in encrypted safe)
            
        Returns:
            New photo UUID
        """
        await self._execute(
            """INSERT INTO photos 
               (id, filename, original_name, folder_id, user_id,
                media_type, album_id, position, taken_at,
                is_encrypted, thumb_width, thumb_height, safe_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                photo_id, filename, original_name, folder_id, user_id,
                media_type, album_id, position, taken_at,
                1 if is_encrypted else 0, thumb_width, thumb_height, safe_id
            )
        )
        await self._commit()
        return photo_id
    
    async def update(self, photo_id: str, **kwargs) -> bool:
        """Update photo fields.
        
        Args:
            photo_id: Photo ID
            **kwargs: Fields to update
            
        Returns:
            True if photo existed and was updated
        """
        allowed_fields = {
            'filename', 'original_name', 'folder_id', 'album_id',
            'position', 'taken_at', 'is_encrypted', 
            'thumb_width', 'thumb_height', 'safe_id'
        }
        
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [photo_id]
        
        cursor = await self._execute(
            f"UPDATE photos SET {set_clause} WHERE id = ?",
            tuple(values)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def delete(self, photo_id: str) -> bool:
        """Delete photo.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            True if photo was deleted
        """
        cursor = await self._execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        await self._commit()
        return cursor.rowcount > 0
    
    async def list_by_folder(self, folder_id: str) -> list[dict]:
        """Get photos in folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            List of photo dicts
        """
        return await self._fetchall(
            "SELECT * FROM photos WHERE folder_id = ? ORDER BY uploaded_at DESC",
            (folder_id,)
        )
    
    async def list_by_user(self, user_id: int) -> list[dict]:
        """Get photos by user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of photo dicts
        """
        return await self._fetchall(
            "SELECT * FROM photos WHERE user_id = ? ORDER BY uploaded_at DESC",
            (user_id,)
        )
    
    async def list_by_album(self, album_id: str) -> list[dict]:
        """Get photos in album ordered by position.
        
        Args:
            album_id: Album ID
            
        Returns:
            List of photo dicts
        """
        return await self._fetchall(
            """SELECT * FROM photos 
               WHERE album_id = ?
               ORDER BY position, id""",
            (album_id,)
        )
    
    async def move_to_folder(self, photo_id: str, folder_id: str | None) -> bool:
        """Move photo to different folder.
        
        Args:
            photo_id: Photo ID
            folder_id: Target folder ID (None for standalone)
            
        Returns:
            True if successful
        """
        cursor = await self._execute(
            "UPDATE photos SET folder_id = ? WHERE id = ?",
            (folder_id, photo_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def add_to_album(self, photo_id: str, album_id: str) -> bool:
        """Add photo to album.
        
        Args:
            photo_id: Photo ID
            album_id: Album ID
            
        Returns:
            True if successful
        """
        cursor = await self._execute(
            "UPDATE photos SET album_id = ? WHERE id = ?",
            (album_id, photo_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def remove_from_album(self, photo_id: str, album_id: str) -> bool:
        """Remove photo from album.
        
        Args:
            photo_id: Photo ID
            album_id: Album ID (for verification)
            
        Returns:
            True if successful
        """
        cursor = await self._execute(
            "UPDATE photos SET album_id = NULL WHERE id = ? AND album_id = ?",
            (photo_id, album_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def search(self, query: str, user_id: int | None = None) -> list[dict]:
        """Search photos by filename or original name.
        
        Args:
            query: Search query string
            user_id: Optional user filter
            
        Returns:
            List of matching photo dicts
        """
        search_pattern = f"%{query}%"
        
        if user_id is not None:
            return await self._fetchall(
                """SELECT * FROM photos 
                   WHERE user_id = ? AND (filename LIKE ? OR original_name LIKE ?)
                   ORDER BY uploaded_at DESC""",
                (user_id, search_pattern, search_pattern)
            )
        else:
            return await self._fetchall(
                """SELECT * FROM photos 
                   WHERE filename LIKE ? OR original_name LIKE ?
                   ORDER BY uploaded_at DESC""",
                (search_pattern, search_pattern)
            )
    
    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Get most recent photos.
        
        Args:
            limit: Maximum number of photos
            
        Returns:
            List of recent photo dicts
        """
        return await self._fetchall(
            "SELECT * FROM photos ORDER BY uploaded_at DESC LIMIT ?",
            (limit,)
        )
    
    async def count_by_folder(self, folder_id: str) -> int:
        """Count photos in folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Photo count
        """
        row = await self._fetchone(
            "SELECT COUNT(*) as count FROM photos WHERE folder_id = ?",
            (folder_id,)
        )
        return row["count"] if row else 0
    
    async def count_by_user(self, user_id: int) -> int:
        """Count photos by user.
        
        Args:
            user_id: User ID
            
        Returns:
            Photo count
        """
        row = await self._fetchone(
            "SELECT COUNT(*) as count FROM photos WHERE user_id = ?",
            (user_id,)
        )
        return row["count"] if row else 0
    
    async def update_metadata(
        self,
        photo_id: str,
        width: int | None = None,
        height: int | None = None,
        **kwargs
    ) -> bool:
        """Update photo metadata.
        
        Args:
            photo_id: Photo ID
            width: Image width
            height: Image height
            **kwargs: Additional metadata fields
            
        Returns:
            True if successful
        """
        updates = {}
        if width is not None:
            updates['thumb_width'] = width
        if height is not None:
            updates['thumb_height'] = height
        
        allowed_fields = {'taken_at', 'is_encrypted', 'safe_id'}
        updates.update({k: v for k, v in kwargs.items() if k in allowed_fields})
        
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [photo_id]
        
        cursor = await self._execute(
            f"UPDATE photos SET {set_clause} WHERE id = ?",
            tuple(values)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def delete_all_in_folder(self, folder_id: str) -> int:
        """Delete all photos in folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Number of photos deleted
        """
        cursor = await self._execute(
            "DELETE FROM photos WHERE folder_id = ?",
            (folder_id,)
        )
        await self._commit()
        return cursor.rowcount
    
    async def list_orphaned(self) -> list[dict]:
        """Get orphaned photos (no folder or invalid folder).
        
        Returns:
            List of orphaned photo dicts
        """
        return await self._fetchall(
            """SELECT p.* FROM photos p
               LEFT JOIN folders f ON p.folder_id = f.id
               WHERE p.folder_id IS NULL OR f.id IS NULL""",
            ()
        )
    
    def get_by_album(self, album_id: str) -> list[dict]:
        """Get photos in album ordered by position.
        
        Args:
            album_id: Album ID
            
        Returns:
            List of photo dicts
        """
        cursor = self._execute(
            """SELECT * FROM photos 
               WHERE album_id = ?
               ORDER BY position, id""",
            (album_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def update(self, photo_id: str, **kwargs) -> bool:
        """Update photo fields.
        
        Args:
            photo_id: Photo ID
            **kwargs: Fields to update
            
        Returns:
            True if photo existed and was updated
        """
        allowed_fields = {
            'filename', 'original_name', 'folder_id', 'album_id',
            'position', 'taken_at', 'is_encrypted', 
            'thumb_width', 'thumb_height', 'safe_id'
        }
        
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [photo_id]
        
        cursor = self._execute(
            f"UPDATE photos SET {set_clause} WHERE id = ?",
            tuple(values)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, photo_id: str) -> dict | None:
        """Delete photo and return info for file cleanup.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            Photo dict (with filename for cleanup) or None
        """
        # Get photo info before delete
        photo = self.get_by_id(photo_id)
        if not photo:
            return None
        
        self._execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        self._commit()
        return photo
    
    def delete_by_folder(self, folder_id: str) -> list[str]:
        """Delete all photos in folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            List of filenames for cleanup
        """
        # Get filenames before delete
        cursor = self._execute(
            "SELECT filename FROM photos WHERE folder_id = ?",
            (folder_id,)
        )
        filenames = [row["filename"] for row in cursor.fetchall()]
        
        self._execute("DELETE FROM photos WHERE folder_id = ?", (folder_id,))
        self._commit()
        return filenames
    
    def move_to_folder(self, photo_id: str, new_folder_id: str) -> bool:
        """Move photo to different folder.
        
        Only works for standalone photos (not in album).
        
        Args:
            photo_id: Photo ID
            new_folder_id: Target folder ID
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            """UPDATE photos 
               SET folder_id = ? 
               WHERE id = ? AND album_id IS NULL""",
            (new_folder_id, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def add_to_album(self, photo_id: str, album_id: str, position: int = None) -> bool:
        """Add photo to album.
        
        Args:
            photo_id: Photo ID
            album_id: Album ID
            position: Position in album (auto if None)
            
        Returns:
            True if successful
        """
        if position is None:
            # Get next position
            cursor = self._execute(
                "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM photos WHERE album_id = ?",
                (album_id,)
            )
            position = cursor.fetchone()["next_pos"]
        
        cursor = self._execute(
            "UPDATE photos SET album_id = ?, position = ? WHERE id = ?",
            (album_id, position, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def remove_from_album(self, photo_id: str) -> bool:
        """Remove photo from album (keeps in folder).
        
        Args:
            photo_id: Photo ID
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE photos SET album_id = NULL, position = 0 WHERE id = ?",
            (photo_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def reorder_in_album(self, album_id: str, photo_ids: list[str]) -> bool:
        """Reorder photos in album.
        
        Args:
            album_id: Album ID
            photo_ids: Photo IDs in desired order
            
        Returns:
            True if successful
        """
        # Verify all photos belong to album
        placeholders = ",".join("?" * len(photo_ids))
        cursor = self._execute(
            f"SELECT id FROM photos WHERE album_id = ? AND id IN ({placeholders})",
            (album_id,) + tuple(photo_ids)
        )
        found = {row["id"] for row in cursor.fetchall()}
        
        if len(found) != len(photo_ids):
            return False
        
        # Update positions
        for position, photo_id in enumerate(photo_ids):
            self._execute(
                "UPDATE photos SET position = ? WHERE id = ?",
                (position, photo_id)
            )
        
        self._commit()
        return True
    
    def update_thumbnail_dimensions(
        self,
        photo_id: str,
        width: int,
        height: int
    ) -> bool:
        """Update thumbnail dimensions.
        
        Args:
            photo_id: Photo ID
            width: Thumbnail width
            height: Thumbnail height
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE photos SET thumb_width = ?, thumb_height = ? WHERE id = ?",
            (width, height, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def mark_encrypted(self, photo_id: str, encrypted: bool = True) -> bool:
        """Mark photo as encrypted/decrypted.
        
        Args:
            photo_id: Photo ID
            encrypted: New encryption status
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE photos SET is_encrypted = ? WHERE id = ?",
            (1 if encrypted else 0, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def update_taken_date(self, photo_id: str, taken_at: datetime) -> bool:
        """Update EXIF taken date.
        
        Args:
            photo_id: Photo ID
            taken_at: Capture date
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE photos SET taken_at = ? WHERE id = ?",
            (taken_at, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def count_by_folder(self, folder_id: str, include_subfolders: bool = False) -> int:
        """Count photos in folder.
        
        Args:
            folder_id: Folder ID
            include_subfolders: Include subfolders
            
        Returns:
            Photo count
        """
        if include_subfolders:
            cursor = self._execute(
                """WITH RECURSIVE subfolder_tree AS (
                    SELECT id FROM folders WHERE id = ?
                    UNION ALL
                    SELECT f.id FROM folders f 
                    JOIN subfolder_tree st ON f.parent_id = st.id
                )
                SELECT COUNT(*) as count FROM photos 
                WHERE folder_id IN (SELECT id FROM subfolder_tree)""",
                (folder_id,)
            )
        else:
            cursor = self._execute(
                "SELECT COUNT(*) as count FROM photos WHERE folder_id = ?",
                (folder_id,)
            )
        
        row = cursor.fetchone()
        return row["count"] if row else 0
    
    def count_by_album(self, album_id: str) -> int:
        """Count photos in album.
        
        Args:
            album_id: Album ID
            
        Returns:
            Photo count
        """
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM photos WHERE album_id = ?",
            (album_id,)
        )
        row = cursor.fetchone()
        return row["count"] if row else 0
    
    def get_stats(self, user_id: int = None) -> dict:
        """Get photo statistics.
        
        Args:
            user_id: Filter by user (None for all)
            
        Returns:
            Stats dict with counts
        """
        if user_id:
            total = self._execute(
                "SELECT COUNT(*) as count FROM photos WHERE user_id = ?",
                (user_id,)
            ).fetchone()["count"]
            
            encrypted = self._execute(
                "SELECT COUNT(*) as count FROM photos WHERE user_id = ? AND is_encrypted = 1",
                (user_id,)
            ).fetchone()["count"]
        else:
            total = self._execute(
                "SELECT COUNT(*) as count FROM photos"
            ).fetchone()["count"]
            
            encrypted = self._execute(
                "SELECT COUNT(*) as count FROM photos WHERE is_encrypted = 1"
            ).fetchone()["count"]
        
        return {
            "total": total,
            "encrypted": encrypted,
            "unencrypted": total - encrypted
        }
    
    def get_untagged(self, limit: int = 10) -> list[dict]:
        """Get photos without tags (for AI processing).
        
        Args:
            limit: Maximum results
            
        Returns:
            List of photo dicts
        """
        cursor = self._execute(
            """SELECT p.id, p.filename 
               FROM photos p
               LEFT JOIN tags t ON p.id = t.photo_id
               WHERE t.id IS NULL
               ORDER BY p.uploaded_at ASC
               LIMIT ?""",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]
