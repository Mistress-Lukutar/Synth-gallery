"""Folder repository - handles all folder-related database operations.

Folders form a hierarchical structure with parent-child relationships.
Operations support recursive queries for subtree operations.
"""
import uuid
from typing import Optional
from .base import Repository, AsyncRepository, AsyncRepository


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


# =============================================================================
# ASYNC VERSION (Issue #15)
# =============================================================================

class AsyncFolderRepository(AsyncRepository):
    """Async repository for folder operations."""

    async def get_by_id(self, folder_id: str) -> dict | None:
        """Get folder by ID.
        
        Args:
            folder_id: Folder UUID
            
        Returns:
            Folder dict or None
        """
        return await self._fetchone(
            "SELECT * FROM folders WHERE id = ?",
            (folder_id,)
        )

    async def get_by_parent(self, parent_id: str | None) -> list[dict]:
        """Get folders by parent ID.
        
        Args:
            parent_id: Parent folder ID (None for root folders)
            
        Returns:
            List of folder dicts
        """
        return await self._fetchall(
            "SELECT * FROM folders WHERE parent_id = ? ORDER BY name",
            (parent_id,)
        )

    async def create(self, folder_id: str, name: str, user_id: int, parent_id: str | None = None) -> str:
        """Create a new folder.
        
        Args:
            folder_id: Folder UUID
            name: Folder name
            user_id: Owner user ID
            parent_id: Parent folder ID (None for root)
            
        Returns:
            New folder UUID
        """
        await self._execute(
            """INSERT INTO folders (id, name, parent_id, user_id) 
               VALUES (?, ?, ?, ?)""",
            (folder_id, name.strip(), parent_id, user_id)
        )
        await self._commit()
        return folder_id

    async def update_name(self, folder_id: str, new_name: str) -> bool:
        """Update folder name.
        
        Args:
            folder_id: Folder ID
            new_name: New folder name
            
        Returns:
            True if folder existed and was updated
        """
        cursor = await self._execute(
            "UPDATE folders SET name = ? WHERE id = ?",
            (new_name.strip(), folder_id)
        )
        await self._commit()
        return cursor.rowcount > 0

    async def delete(self, folder_id: str) -> bool:
        """Delete folder.
        
        Args:
            folder_id: Folder ID to delete
            
        Returns:
            True if folder existed and was deleted
        """
        cursor = await self._execute(
            "DELETE FROM folders WHERE id = ?",
            (folder_id,)
        )
        await self._commit()
        return cursor.rowcount > 0

    async def get_full_path(self, folder_id: str) -> list[dict]:
        """Get full path from root to folder.
        
        Args:
            folder_id: Target folder ID
            
        Returns:
            List of {id, name} dicts from root to target
        """
        breadcrumbs = []
        current_id = folder_id
        
        while current_id:
            row = await self._fetchone(
                "SELECT id, name, parent_id FROM folders WHERE id = ?",
                (current_id,)
            )
            
            if row:
                breadcrumbs.insert(0, {"id": row["id"], "name": row["name"]})
                current_id = row["parent_id"]
            else:
                break
        
        return breadcrumbs

    async def get_tree(self, user_id: int) -> list[dict]:
        """Get folder tree for user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of folder dicts with metadata
        """
        return await self._fetchall(
            """SELECT f.*, u.display_name as owner_name,
                   (SELECT COUNT(*) FROM photos p WHERE p.folder_id = f.id) as photo_count
               FROM folders f
               JOIN users u ON f.user_id = u.id
               WHERE f.user_id = ? 
                  OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
               ORDER BY f.name""",
            (user_id, user_id)
        )

    async def search_by_name(self, name: str, user_id: int | None = None) -> list[dict]:
        """Search folders by name.
        
        Args:
            name: Search term
            user_id: Optional user ID to restrict search
            
        Returns:
            List of matching folder dicts
        """
        if user_id is not None:
            return await self._fetchall(
                """SELECT * FROM folders 
                   WHERE name LIKE ? AND user_id = ? 
                   ORDER BY name""",
                (f"%{name}%", user_id)
            )
        else:
            return await self._fetchall(
                """SELECT * FROM folders 
                   WHERE name LIKE ? 
                   ORDER BY name""",
                (f"%{name}%",)
            )

    async def get_children(self, parent_id: str) -> list[dict]:
        """Get direct child folders.
        
        Args:
            parent_id: Parent folder ID
            
        Returns:
            List of child folder dicts
        """
        return await self._fetchall(
            "SELECT * FROM folders WHERE parent_id = ? ORDER BY name",
            (parent_id,)
        )

    async def move(self, folder_id: str, new_parent_id: str | None) -> bool:
        """Move folder to new parent.
        
        Args:
            folder_id: Folder to move
            new_parent_id: New parent (None for root)
            
        Returns:
            True if successful
        """
        cursor = await self._execute(
            "UPDATE folders SET parent_id = ? WHERE id = ?",
            (new_parent_id, folder_id)
        )
        await self._commit()
        return cursor.rowcount > 0

    async def get_root_folders(self, user_id: int) -> list[dict]:
        """Get all root folders for user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of root folder dicts
        """
        return await self._fetchall(
            "SELECT * FROM folders WHERE user_id = ? AND parent_id IS NULL ORDER BY name",
            (user_id,)
        )

    async def get_shared_folders(self, user_id: int) -> list[dict]:
        """Get folders shared with user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of shared folder dicts
        """
        return await self._fetchall(
            """SELECT f.* FROM folders f
               JOIN folder_permissions fp ON f.id = fp.folder_id
               WHERE fp.user_id = ?
               ORDER BY f.name""",
            (user_id,)
        )

    async def is_descendant_of(self, folder_id: str, ancestor_id: str) -> bool:
        """Check if folder is descendant of ancestor.
        
        Args:
            folder_id: Folder to check
            ancestor_id: Potential ancestor folder ID
            
        Returns:
            True if folder_id is descendant of ancestor_id
        """
        current_id = folder_id
        
        while current_id:
            row = await self._fetchone(
                "SELECT parent_id FROM folders WHERE id = ?",
                (current_id,)
            )
            
            if not row:
                return False
                
            current_id = row["parent_id"]
            
            if current_id == ancestor_id:
                return True
        
        return False
