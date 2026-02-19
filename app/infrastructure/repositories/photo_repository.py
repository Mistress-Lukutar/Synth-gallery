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

from .base import Repository


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
    
    # =========================================================================
    # Album Operations
    # =========================================================================
    
    def get_album(self, album_id: str) -> dict | None:
        """Get album by ID.
        
        Args:
            album_id: Album UUID
            
        Returns:
            Album dict or None
        """
        cursor = self._execute(
            """SELECT a.*, 
                   (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) as photo_count,
                   (SELECT filename FROM photos p WHERE p.id = a.cover_photo_id) as cover_filename
               FROM albums a WHERE a.id = ?""",
            (album_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def set_album_cover(self, album_id: str, photo_id: str | None) -> bool:
        """Set album cover photo.
        
        Args:
            album_id: Album ID
            photo_id: Photo ID (None to clear)
            
        Returns:
            True if updated
        """
        cursor = self._execute(
            "UPDATE albums SET cover_photo_id = ? WHERE id = ?",
            (photo_id, album_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def get_album_photos(self, album_id: str) -> list[dict]:
        """Get all photos in album with position ordering.
        
        Args:
            album_id: Album ID
            
        Returns:
            List of photo dicts
        """
        cursor = self._execute(
            """SELECT * FROM photos 
               WHERE album_id = ? 
               ORDER BY COALESCE(position, 999999), uploaded_at""",
            (album_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def add_to_album(self, photo_id: str, album_id: str, position: int = None) -> bool:
        """Add photo to album.
        
        Args:
            photo_id: Photo ID
            album_id: Album ID
            position: Position in album (None for append)
            
        Returns:
            True if added
        """
        if position is None:
            # Get next position
            cursor = self._execute(
                "SELECT MAX(COALESCE(position, 0)) as max_pos FROM photos WHERE album_id = ?",
                (album_id,)
            )
            row = cursor.fetchone()
            position = (row["max_pos"] or 0) + 1
        
        cursor = self._execute(
            "UPDATE photos SET album_id = ?, position = ? WHERE id = ?",
            (album_id, position, photo_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def remove_from_album(self, photo_id: str) -> bool:
        """Remove photo from album.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            True if removed
        """
        cursor = self._execute(
            "UPDATE photos SET album_id = NULL, position = NULL WHERE id = ?",
            (photo_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def reorder_album(self, album_id: str, photo_ids: list[str]) -> bool:
        """Reorder photos in album.
        
        Args:
            album_id: Album ID
            photo_ids: Ordered list of photo IDs
            
        Returns:
            True if reordered
        """
        try:
            for i, photo_id in enumerate(photo_ids):
                self._execute(
                    "UPDATE photos SET position = ? WHERE id = ? AND album_id = ?",
                    (i, photo_id, album_id)
                )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_available_for_album(self, album_id: str, user_id: int) -> list[dict]:
        """Get photos available to add to album.
        
        Photos in the same folder that aren't in this album yet.
        
        Args:
            album_id: Album ID
            user_id: User ID (for access check)
            
        Returns:
            List of photo dicts
        """
        # Get album's folder
        cursor = self._execute(
            "SELECT folder_id FROM albums WHERE id = ?",
            (album_id,)
        )
        album = cursor.fetchone()
        if not album or not album["folder_id"]:
            return []
        
        folder_id = album["folder_id"]
        
        cursor = self._execute(
            """SELECT * FROM photos 
               WHERE folder_id = ? 
                 AND (album_id IS NULL OR album_id != ?)
                 AND user_id = ?
               ORDER BY uploaded_at DESC""",
            (folder_id, album_id, user_id)
        )
        return [dict(row) for row in cursor.fetchall()]
    
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
    
    def move_album_to_folder(self, album_id: str, folder_id: str) -> bool:
        """Move album to different folder.
        
        Args:
            album_id: Album ID
            folder_id: Target folder ID
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE albums SET folder_id = ? WHERE id = ?",
            (folder_id, album_id)
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
    
    def get_unencrypted_by_user(self, user_id: int) -> list[dict]:
        """Get all unencrypted photos for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of unencrypted photo dicts
        """
        cursor = self._execute(
            """SELECT id, filename 
               FROM photos 
               WHERE user_id = ? AND is_encrypted = 0""",
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
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
    
    # =========================================================================
    # Envelope Encryption Operations
    # =========================================================================
    
    def get_photo_key(self, photo_id: str) -> dict | None:
        """Get photo encryption key data.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            Dict with encrypted_ck, thumbnail_encrypted_ck, shared_ck_map or None
        """
        cursor = self._execute(
            """SELECT encrypted_ck, thumbnail_encrypted_ck, shared_ck_map
               FROM photo_keys WHERE photo_id = ?""",
            (photo_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "encrypted_ck": row["encrypted_ck"],
                "thumbnail_encrypted_ck": row["thumbnail_encrypted_ck"],
                "shared_ck_map": row["shared_ck_map"]
            }
        return None
    
    def create_photo_key(
        self,
        photo_id: str,
        encrypted_ck: bytes,
        thumbnail_encrypted_ck: bytes | None = None
    ) -> bool:
        """Create photo encryption key.
        
        Args:
            photo_id: Photo ID
            encrypted_ck: Encrypted content key
            thumbnail_encrypted_ck: Encrypted thumbnail content key (optional)
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                """INSERT OR REPLACE INTO photo_keys 
                   (photo_id, encrypted_ck, thumbnail_encrypted_ck, updated_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (photo_id, encrypted_ck, thumbnail_encrypted_ck)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_storage_mode(self, photo_id: str) -> str:
        """Get storage mode for a photo.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            Storage mode ('legacy' or 'envelope')
        """
        cursor = self._execute(
            "SELECT storage_mode FROM photos WHERE id = ?",
            (photo_id,)
        )
        row = cursor.fetchone()
        return row["storage_mode"] if row else "legacy"
    
    def set_storage_mode(self, photo_id: str, mode: str) -> bool:
        """Set storage mode for a photo.
        
        Args:
            photo_id: Photo ID
            mode: Storage mode ('legacy' or 'envelope')
            
        Returns:
            True if successful
        """
        try:
            cursor = self._execute(
                "UPDATE photos SET storage_mode = ? WHERE id = ?",
                (mode, photo_id)
            )
            self._commit()
            return cursor.rowcount > 0
        except Exception:
            return False
    
    def get_photo_shared_key(self, photo_id: str, user_id: int) -> bytes | None:
        """Get shared key for a specific user.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            
        Returns:
            Shared encrypted CK bytes or None
        """
        cursor = self._execute(
            "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
            (photo_id,)
        )
        row = cursor.fetchone()
        if row and row["shared_ck_map"]:
            import json
            try:
                shared_map = json.loads(row["shared_ck_map"])
                key_hex = shared_map.get(str(user_id))
                if key_hex:
                    return bytes.fromhex(key_hex)
            except (json.JSONDecodeError, ValueError):
                pass
        return None
    
    def set_photo_shared_key(
        self,
        photo_id: str,
        user_id: int,
        encrypted_ck: bytes
    ) -> bool:
        """Set shared key for a specific user.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            encrypted_ck: Encrypted CK for the user
            
        Returns:
            True if successful
        """
        try:
            # Get current shared map
            cursor = self._execute(
                "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
                (photo_id,)
            )
            row = cursor.fetchone()
            
            import json
            if row and row["shared_ck_map"]:
                shared_map = json.loads(row["shared_ck_map"])
            else:
                shared_map = {}
            
            # Add/update the shared key
            shared_map[str(user_id)] = encrypted_ck.hex()
            
            self._execute(
                """UPDATE photo_keys 
                   SET shared_ck_map = ?, updated_at = datetime('now')
                   WHERE photo_id = ?""",
                (json.dumps(shared_map), photo_id)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def remove_photo_shared_key(self, photo_id: str, user_id: int) -> bool:
        """Remove shared key for a specific user.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            
        Returns:
            True if successful
        """
        try:
            # Get current shared map
            cursor = self._execute(
                "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
                (photo_id,)
            )
            row = cursor.fetchone()
            
            if not row or not row["shared_ck_map"]:
                return False
            
            import json
            shared_map = json.loads(row["shared_ck_map"])
            
            # Remove the user
            if str(user_id) in shared_map:
                del shared_map[str(user_id)]
                
                self._execute(
                    """UPDATE photo_keys 
                       SET shared_ck_map = ?, updated_at = datetime('now')
                       WHERE photo_id = ?""",
                    (json.dumps(shared_map), photo_id)
                )
                self._commit()
                return True
            return False
        except Exception:
            return False
    
    # =========================================================================
    # Album Operations
    # =========================================================================
    
    def create_album(
        self,
        album_id: str,
        folder_id: str,
        user_id: int,
        name: str = None
    ) -> bool:
        """Create a new album.
        
        Args:
            album_id: Album UUID
            name: Album name
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                "INSERT INTO albums (id, name, folder_id, user_id) VALUES (?, ?, ?, ?)",
                (album_id, name, folder_id, user_id)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def delete_album(self, album_id: str) -> bool:
        """Delete an album (photos are NOT deleted).
        
        Args:
            album_id: Album ID
            
        Returns:
            True if successful
        """
        try:
            # Remove album_id from photos first
            self._execute(
                "UPDATE photos SET album_id = NULL, position = NULL WHERE album_id = ?",
                (album_id,)
            )
            # Delete album
            self._execute("DELETE FROM albums WHERE id = ?", (album_id,))
            self._commit()
            return True
        except Exception:
            return False
    
    def delete_album_with_photos(self, album_id: str) -> list[dict]:
        """Delete an album and all its photos.
        
        Args:
            album_id: Album ID
            
        Returns:
            List of deleted photo info (id, filename)
        """
        try:
            # Get all photos in album
            cursor = self._execute(
                "SELECT id, filename FROM photos WHERE album_id = ?",
                (album_id,)
            )
            photos = [dict(row) for row in cursor.fetchall()]
            
            # Delete photos
            self._execute("DELETE FROM photos WHERE album_id = ?", (album_id,))
            # Delete album
            self._execute("DELETE FROM albums WHERE id = ?", (album_id,))
            self._commit()
            
            return photos
        except Exception:
            return []
    
    def add_photo_to_album(
        self,
        photo_id: str,
        album_id: str,
        position: int = 0
    ) -> bool:
        """Add photo to album.
        
        Args:
            photo_id: Photo ID
            album_id: Album ID
            position: Position in album
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                "UPDATE photos SET album_id = ?, position = ? WHERE id = ?",
                (album_id, position, photo_id)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def remove_photo_from_album(self, photo_id: str) -> bool:
        """Remove photo from album.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                "UPDATE photos SET album_id = NULL, position = NULL WHERE id = ?",
                (photo_id,)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_album_by_id(self, album_id: str) -> dict | None:
        """Get album by ID.
        
        Args:
            album_id: Album ID
            
        Returns:
            Album dict or None
        """
        cursor = self._execute(
            "SELECT * FROM albums WHERE id = ?",
            (album_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_photos_in_album(self, album_id: str) -> list[dict]:
        """Get all photos in album.
        
        Args:
            album_id: Album ID
            
        Returns:
            List of photo dicts
        """
        cursor = self._execute(
            "SELECT * FROM photos WHERE album_id = ? ORDER BY position",
            (album_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def update_album_name(self, album_id: str, name: str) -> bool:
        """Update album name.
        
        Args:
            album_id: Album ID
            name: New name
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                "UPDATE albums SET name = ? WHERE id = ?",
                (name, album_id)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_photo_shared_users(self, photo_id: str) -> list[int]:
        """Get list of user IDs who have shared access to this photo.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            List of user IDs
        """
        cursor = self._execute(
            "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
            (photo_id,)
        )
        row = cursor.fetchone()
        
        if row and row["shared_ck_map"]:
            import json
            try:
                shared_map = json.loads(row["shared_ck_map"])
                return [int(uid) for uid in shared_map.keys()]
            except (json.JSONDecodeError, ValueError):
                pass
        return []
    
    def get_migration_status(self, user_id: int) -> dict:
        """Get migration status for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Migration status dict
        """
        # Count total photos
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM photos WHERE user_id = ?",
            (user_id,)
        )
        total = cursor.fetchone()["count"]
        
        # Count envelope photos
        cursor = self._execute(
            """SELECT COUNT(*) as count FROM photos 
               WHERE user_id = ? AND storage_mode = 'envelope'""",
            (user_id,)
        )
        envelope = cursor.fetchone()["count"]
        
        # Count legacy encrypted photos
        cursor = self._execute(
            """SELECT COUNT(*) as count FROM photos 
               WHERE user_id = ? AND storage_mode = 'legacy' AND is_encrypted = 1""",
            (user_id,)
        )
        legacy_encrypted = cursor.fetchone()["count"]
        
        return {
            "total_photos": total,
            "envelope_photos": envelope,
            "legacy_encrypted_photos": legacy_encrypted,
            "unencrypted_photos": total - envelope - legacy_encrypted,
            "needs_migration": legacy_encrypted > 0
        }
    
    def get_photos_needing_migration(self, user_id: int) -> list[dict]:
        """Get photos that need migration to envelope encryption.
        
        Args:
            user_id: User ID
            
        Returns:
            List of photo dicts needing migration
        """
        cursor = self._execute(
            """SELECT id, filename, original_name 
               FROM photos 
               WHERE user_id = ? 
                 AND storage_mode = 'legacy' 
                 AND is_encrypted = 1
               ORDER BY uploaded_at DESC""",
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
