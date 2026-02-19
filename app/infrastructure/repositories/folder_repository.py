"""Folder repository - handles all folder-related database operations.

Folders form a hierarchical structure with parent-child relationships.
Operations support recursive queries for subtree operations.
"""
import uuid

from .base import Repository


class FolderRepository(Repository):
    """Repository for folder entity operations.
    
    Folders form a tree structure where each folder can have:
    - One parent (optional, None for root folders)
    - Multiple children (subfolders)
    - Multiple photos and albums
    
    Examples:
        >>> repo = FolderRepository(db)
        >>> root = repo.create("My Gallery", user_id)
        >>> child = repo.create("Vacation", user_id, parent_id=root)
        >>> tree = repo.get_tree(user_id)  # All folders with metadata
    """
    
    def create(self, name: str, user_id: int, parent_id: str = None, safe_id: str = None) -> str:
        """Create a new folder.
        
        Args:
            name: Folder name
            user_id: Owner user ID
            parent_id: Parent folder ID (None for root)
            safe_id: Associated safe ID (None for regular folders)
            
        Returns:
            New folder UUID
        """
        folder_id = str(uuid.uuid4())
        self._execute(
            """INSERT INTO folders (id, name, parent_id, user_id, safe_id) 
               VALUES (?, ?, ?, ?, ?)""",
            (folder_id, name.strip(), parent_id, user_id, safe_id)
        )
        self._commit()
        return folder_id
    
    def get_by_id(self, folder_id: str) -> dict | None:
        """Get folder by ID.
        
        Args:
            folder_id: Folder UUID
            
        Returns:
            Folder dict or None
        """
        cursor = self._execute(
            "SELECT * FROM folders WHERE id = ?",
            (folder_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def update(self, folder_id: str, name: str = None) -> bool:
        """Update folder name.
        
        Args:
            folder_id: Folder ID
            name: New name (if provided)
            
        Returns:
            True if folder existed and was updated
        """
        if name is None:
            return False
            
        cursor = self._execute(
            "UPDATE folders SET name = ? WHERE id = ?",
            (name.strip(), folder_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, folder_id: str) -> list[str]:
        """Delete folder and all its contents recursively.
        
        This deletes:
        - The folder and all subfolders (recursive)
        - All photos in those folders
        - All albums in those folders
        
        Args:
            folder_id: Root folder ID to delete
            
        Returns:
            List of filenames that should be deleted from storage
        """
        # Get all folder IDs in the tree (recursive CTE)
        folder_ids = self._get_subtree_ids(folder_id)
        
        if not folder_ids:
            return []
        
        # Collect filenames for cleanup
        filenames = self._get_filenames_in_folders(folder_ids)
        
        # Delete photos in these folders
        self._delete_photos_in_folders(folder_ids)
        
        # Delete albums in these folders
        self._delete_albums_in_folders(folder_ids)
        
        # Delete the folders themselves
        self._delete_folders_by_ids(folder_ids)
        
        self._commit()
        return filenames
    
    def list_by_user(self, user_id: int) -> list[dict]:
        """Get all folders owned by user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of folder dicts
        """
        cursor = self._execute(
            "SELECT * FROM folders WHERE user_id = ? ORDER BY name",
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def get_children(self, folder_id: str) -> list[dict]:
        """Get direct child folders.
        
        Args:
            folder_id: Parent folder ID
            
        Returns:
            List of child folder dicts
        """
        cursor = self._execute(
            "SELECT * FROM folders WHERE parent_id = ? ORDER BY name",
            (folder_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def get_tree(self, user_id: int, include_shared: bool = True) -> list[dict]:
        """Get folder tree for sidebar with metadata.
        
        Returns folders with:
        - Photo counts (recursive)
        - Permission info
        - Safe info (if in safe)
        
        Args:
            user_id: User ID
            include_shared: Include folders shared with user
            
        Returns:
            List of folder dicts with metadata
        """
        # This is a complex query that mirrors the original get_folder_tree
        # We return basic info; extend as needed
        
        if include_shared:
            cursor = self._execute(
                """SELECT f.*, u.display_name as owner_name,
                       (SELECT COUNT(*) FROM photos p WHERE p.folder_id = f.id) as photo_count
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ? 
                      OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
                   ORDER BY f.name""",
                (user_id, user_id)
            )
        else:
            cursor = self._execute(
                """SELECT f.*, u.display_name as owner_name,
                       (SELECT COUNT(*) FROM photos p WHERE p.folder_id = f.id) as photo_count
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ?
                   ORDER BY f.name""",
                (user_id,)
            )
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_breadcrumbs(self, folder_id: str) -> list[dict]:
        """Get breadcrumb path from root to folder.
        
        Args:
            folder_id: Target folder ID
            
        Returns:
            List of {id, name} dicts from root to target
        """
        breadcrumbs = []
        current_id = folder_id
        
        while current_id:
            cursor = self._execute(
                "SELECT id, name, parent_id FROM folders WHERE id = ?",
                (current_id,)
            )
            folder = cursor.fetchone()
            
            if folder:
                breadcrumbs.insert(0, {"id": folder["id"], "name": folder["name"]})
                current_id = folder["parent_id"]
            else:
                break
        
        return breadcrumbs
    
    def move_to_folder(self, folder_id: str, new_parent_id: str | None) -> bool:
        """Move folder to new parent (or make root).
        
        Args:
            folder_id: Folder to move
            new_parent_id: New parent (None for root)
            
        Returns:
            True if successful
        """
        cursor = self._execute(
            "UPDATE folders SET parent_id = ? WHERE id = ?",
            (new_parent_id, folder_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def exists(self, folder_id: str) -> bool:
        """Check if folder exists.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            True if exists
        """
        cursor = self._execute(
            "SELECT 1 FROM folders WHERE id = ?",
            (folder_id,)
        )
        return cursor.fetchone() is not None
    
    def count_photos_recursive(self, folder_id: str) -> int:
        """Count all photos in folder and subfolders.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Total photo count
        """
        folder_ids = self._get_subtree_ids(folder_id)
        if not folder_ids:
            return 0
        
        placeholders = ",".join("?" * len(folder_ids))
        cursor = self._execute(
            f"SELECT COUNT(*) as count FROM photos WHERE folder_id IN ({placeholders})",
            tuple(folder_ids)
        )
        row = cursor.fetchone()
        return row["count"] if row else 0
    
    # Private helper methods
    
    def _get_subtree_ids(self, folder_id: str) -> list[str]:
        """Get all folder IDs in subtree using recursive CTE."""
        cursor = self._execute(
            """WITH RECURSIVE folder_tree AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f 
                JOIN folder_tree ft ON f.parent_id = ft.id
            )
            SELECT id FROM folder_tree""",
            (folder_id,)
        )
        return [row["id"] for row in cursor.fetchall()]
    
    def _get_filenames_in_folders(self, folder_ids: list[str]) -> list[str]:
        """Get all photo filenames in folders."""
        if not folder_ids:
            return []
        
        placeholders = ",".join("?" * len(folder_ids))
        cursor = self._execute(
            f"""SELECT p.filename FROM photos p
                WHERE p.folder_id IN ({placeholders})
                   OR p.album_id IN (SELECT a.id FROM albums a WHERE a.folder_id IN ({placeholders}))""",
            tuple(folder_ids + folder_ids)
        )
        return [row["filename"] for row in cursor.fetchall()]
    
    def _delete_photos_in_folders(self, folder_ids: list[str]) -> None:
        """Delete photos in folders."""
        if not folder_ids:
            return
        
        placeholders = ",".join("?" * len(folder_ids))
        self._execute(
            f"""DELETE FROM photos
                WHERE folder_id IN ({placeholders})
                   OR album_id IN (SELECT id FROM albums WHERE folder_id IN ({placeholders}))""",
            tuple(folder_ids + folder_ids)
        )
    
    def _delete_albums_in_folders(self, folder_ids: list[str]) -> None:
        """Delete albums in folders."""
        if not folder_ids:
            return
        
        placeholders = ",".join("?" * len(folder_ids))
        self._execute(
            f"DELETE FROM albums WHERE folder_id IN ({placeholders})",
            tuple(folder_ids)
        )
    
    def _delete_folders_by_ids(self, folder_ids: list[str]) -> None:
        """Delete folders by IDs."""
        if not folder_ids:
            return
        
        placeholders = ",".join("?" * len(folder_ids))
        self._execute(
            f"DELETE FROM folders WHERE id IN ({placeholders})",
            tuple(folder_ids)
        )
    
    # =========================================================================
    # Folder Key Operations (Envelope Encryption)
    # =========================================================================
    
    def get_folder_key(self, folder_id: str) -> dict | None:
        """Get folder encryption key data.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Dict with encrypted_folder_dek, created_by or None
        """
        cursor = self._execute(
            """SELECT folder_id, encrypted_folder_dek, created_by, created_at
               FROM folder_keys WHERE folder_id = ?""",
            (folder_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "folder_id": row["folder_id"],
                "encrypted_folder_dek": row["encrypted_folder_dek"],
                "created_by": row["created_by"],
                "created_at": row["created_at"]
            }
        return None
    
    def create_folder_key(
        self,
        folder_id: str,
        created_by: int,
        encrypted_folder_dek_b64: str
    ) -> bool:
        """Create folder encryption key.
        
        Args:
            folder_id: Folder ID
            created_by: User ID creating the key
            encrypted_folder_dek_b64: Base64-encoded JSON with encrypted DEK per user
            
        Returns:
            True if successful
        """
        try:
            import base64
            # Decode the base64 to get the JSON string
            encrypted_folder_dek = base64.b64decode(encrypted_folder_dek_b64).decode('utf-8')
            
            self._execute(
                """INSERT INTO folder_keys (folder_id, encrypted_folder_dek, created_by)
                   VALUES (?, ?, ?)""",
                (folder_id, encrypted_folder_dek, created_by)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def update_folder_key(self, folder_id: str, encrypted_folder_dek_json: str) -> bool:
        """Update folder encryption key.
        
        Args:
            folder_id: Folder ID
            encrypted_folder_dek_json: JSON string with encrypted DEK map
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                """UPDATE folder_keys 
                   SET encrypted_folder_dek = ?
                   WHERE folder_id = ?""",
                (encrypted_folder_dek_json, folder_id)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def delete_folder_key(self, folder_id: str) -> bool:
        """Delete folder encryption key.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                "DELETE FROM folder_keys WHERE folder_id = ?",
                (folder_id,)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    # =========================================================================
    # Folder Contents Operations
    # =========================================================================
    
    def get_subfolders(self, folder_id: str, user_id: int) -> list[dict]:
        """Get subfolders accessible by user.
        
        Args:
            folder_id: Parent folder ID
            user_id: User ID
            
        Returns:
            List of subfolder dicts
        """
        cursor = self._execute("""
            SELECT * FROM folders
            WHERE parent_id = ? AND (
                user_id = ?
                OR id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
            )
            ORDER BY name
        """, (folder_id, user_id, user_id))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_albums_in_folder(self, folder_id: str) -> list[dict]:
        """Get albums in folder with photo counts.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            List of album dicts with photo_count and cover_photo_id
        """
        cursor = self._execute("""
            SELECT a.*,
                   (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
                   (SELECT id FROM photos WHERE album_id = a.id ORDER BY position LIMIT 1) as cover_photo_id
            FROM albums a
            WHERE a.folder_id = ?
            ORDER BY a.created_at DESC
        """, (folder_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_standalone_photos(self, folder_id: str) -> list[dict]:
        """Get standalone photos (not in album) in folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            List of photo dicts
        """
        cursor = self._execute("""
            SELECT * FROM photos
            WHERE folder_id = ? AND album_id IS NULL
            ORDER BY uploaded_at DESC
        """, (folder_id,))
        return [dict(row) for row in cursor.fetchall()]
    
    def list_with_metadata(self, user_id: int, unlocked_safe_ids: list[str] = None) -> list[dict]:
        """Get all folders accessible by user with metadata.
        
        Args:
            user_id: User ID
            unlocked_safe_ids: List of unlocked safe IDs for safe unlocked status
            
        Returns:
            List of folder dicts with metadata
        """
        # Base query with photo count and permission
        safe_placeholder = ','.join(['?'] * len(unlocked_safe_ids)) if unlocked_safe_ids else 'NULL'
        
        query = f"""
            SELECT f.*, u.display_name as owner_name,
                   (
                       SELECT COUNT(*) FROM photos p
                       WHERE p.folder_id IN (
                           WITH RECURSIVE subfolder_tree AS (
                               SELECT id FROM folders WHERE id = f.id
                               UNION ALL
                               SELECT child.id FROM folders child
                               JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                           )
                           SELECT id FROM subfolder_tree
                       )
                   ) as photo_count,
                   CASE
                       WHEN f.user_id = ? THEN 'owner'
                       ELSE (SELECT permission FROM folder_permissions WHERE folder_id = f.id AND user_id = ?)
                   END as permission,
                   CASE
                       WHEN f.user_id != ? THEN NULL
                       WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'editor') THEN 'has_editors'
                       WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'viewer') THEN 'has_viewers'
                       ELSE 'private'
                   END as share_status,
                   CASE
                       WHEN f.safe_id IS NULL THEN 'no_safe'
                       WHEN f.safe_id IN ({safe_placeholder}) THEN 'unlocked'
                       ELSE 'locked'
                   END as safe_status
            FROM folders f
            JOIN users u ON f.user_id = u.id
            WHERE f.user_id = ?
               OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
            ORDER BY f.name
        """
        
        params = [user_id, user_id, user_id] + (unlocked_safe_ids or []) + [user_id, user_id]
        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]
