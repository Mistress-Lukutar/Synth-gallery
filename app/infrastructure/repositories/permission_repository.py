"""Permission repository - handles folder access control and sharing.

Manages relationships between users and folders with permission levels:
- owner: full control (implicit via folder.user_id)
- editor: can add/edit/delete content
- viewer: read-only access
"""
from typing import Optional
from .base import Repository, AsyncRepository, AsyncRepository


class PermissionRepository(Repository):
    """Repository for folder permission operations.
    
    Permissions control who can access shared folders and what they can do.
    
    Examples:
        >>> repo = PermissionRepository(db)
        >>> repo.grant(folder_id, user_id, "editor", granted_by=owner_id)
        >>> if repo.can_edit(folder_id, user_id):
        ...     upload_photo(...)
    """
    
    VALID_PERMISSIONS = {"viewer", "editor"}
    
    def grant(
        self,
        folder_id: str,
        user_id: int,
        permission: str,
        granted_by: int
    ) -> bool:
        """Grant permission to user on folder.
        
        Args:
            folder_id: Folder to share
            user_id: User to grant access
            permission: 'viewer' or 'editor'
            granted_by: User ID granting the permission (for audit)
            
        Returns:
            True if successful
            
        Raises:
            ValueError: If permission is invalid
        """
        if permission not in self.VALID_PERMISSIONS:
            raise ValueError(f"Invalid permission: {permission}. Use 'viewer' or 'editor'")
        
        try:
            self._execute(
                """INSERT INTO folder_permissions 
                   (folder_id, user_id, permission, granted_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(folder_id, user_id) 
                   DO UPDATE SET 
                       permission = excluded.permission,
                       granted_by = excluded.granted_by,
                       granted_at = CURRENT_TIMESTAMP""",
                (folder_id, user_id, permission, granted_by)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def revoke(self, folder_id: str, user_id: int) -> bool:
        """Revoke permission from user.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            True if permission existed and was removed
        """
        cursor = self._execute(
            "DELETE FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def update_permission(
        self,
        folder_id: str,
        user_id: int,
        permission: str
    ) -> bool:
        """Update existing permission level.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            permission: New permission level ('viewer' or 'editor')
            
        Returns:
            True if permission existed and was updated
        """
        if permission not in self.VALID_PERMISSIONS:
            raise ValueError(f"Invalid permission: {permission}")
        
        cursor = self._execute(
            "UPDATE folder_permissions SET permission = ? WHERE folder_id = ? AND user_id = ?",
            (permission, folder_id, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def get_permission(self, folder_id: str, user_id: int) -> str | None:
        """Get user's permission level for folder.
        
        Returns:
            'owner', 'editor', 'viewer', or None
        """
        # Check if owner
        cursor = self._execute(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        )
        folder = cursor.fetchone()
        
        if folder and folder["user_id"] == user_id:
            return "owner"
        
        # Check explicit permission
        cursor = self._execute(
            "SELECT permission FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        row = cursor.fetchone()
        return row["permission"] if row else None
    
    def can_view(self, folder_id: str, user_id: int) -> bool:
        """Check if user can view folder (owner, viewer, or editor).
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            True if user has view access
        """
        if not folder_id:
            return True
        
        permission = self.get_permission(folder_id, user_id)
        return permission in ("owner", "viewer", "editor")
    
    def can_edit(self, folder_id: str, user_id: int) -> bool:
        """Check if user can edit folder content (owner or editor).
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            True if user has edit access
        """
        if not folder_id:
            return False
        
        permission = self.get_permission(folder_id, user_id)
        return permission in ("owner", "editor")
    
    def is_owner(self, folder_id: str, user_id: int) -> bool:
        """Check if user owns the folder.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            True if user is owner
        """
        cursor = self._execute(
            "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        return cursor.fetchone() is not None
    
    def list_permissions(self, folder_id: str) -> list[dict]:
        """Get all permissions for folder with user info.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            List of permission dicts with user info
        """
        cursor = self._execute(
            """SELECT fp.user_id, fp.permission, fp.granted_at,
                      u.username, u.display_name
               FROM folder_permissions fp
               JOIN users u ON fp.user_id = u.id
               WHERE fp.folder_id = ?
               ORDER BY u.display_name""",
            (folder_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def list_accessible_folders(
        self,
        user_id: int,
        include_owned: bool = True,
        include_shared: bool = True
    ) -> list[dict]:
        """Get all folders user can access.
        
        Args:
            user_id: User ID
            include_owned: Include user's own folders
            include_shared: Include folders shared with user
            
        Returns:
            List of folder dicts
        """
        if include_owned and include_shared:
            cursor = self._execute(
                """SELECT DISTINCT f.*, u.display_name as owner_name,
                          CASE 
                              WHEN f.user_id = ? THEN 'owner'
                              ELSE (SELECT permission FROM folder_permissions 
                                    WHERE folder_id = f.id AND user_id = ?)
                          END as permission
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ?
                      OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
                   ORDER BY f.name""",
                (user_id, user_id, user_id, user_id)
            )
        elif include_owned:
            cursor = self._execute(
                """SELECT f.*, u.display_name as owner_name, 'owner' as permission
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ?
                   ORDER BY f.name""",
                (user_id,)
            )
        elif include_shared:
            cursor = self._execute(
                """SELECT f.*, u.display_name as owner_name, fp.permission
                   FROM folders f
                   JOIN folder_permissions fp ON f.id = fp.folder_id
                   JOIN users u ON f.user_id = u.id
                   WHERE fp.user_id = ?
                   ORDER BY f.name""",
                (user_id,)
            )
        else:
            return []
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_share_status(self, folder_id: str) -> str:
        """Get sharing status of folder.
        
        Returns:
            'private' - not shared
            'has_viewers' - shared with viewers only
            'has_editors' - shared with at least one editor
        """
        cursor = self._execute(
            """SELECT permission FROM folder_permissions WHERE folder_id = ?""",
            (folder_id,)
        )
        permissions = [row["permission"] for row in cursor.fetchall()]
        
        if not permissions:
            return "private"
        
        if "editor" in permissions:
            return "has_editors"
        
        return "has_viewers"
    
    def transfer_ownership(self, folder_id: str, new_owner_id: int) -> bool:
        """Transfer folder ownership to another user.
        
        Old owner becomes editor. New owner must have some permission already.
        
        Args:
            folder_id: Folder ID
            new_owner_id: New owner user ID
            
        Returns:
            True if successful
        """
        # Get current owner
        cursor = self._execute(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        )
        folder = cursor.fetchone()
        if not folder:
            return False
        
        old_owner_id = folder["user_id"]
        
        # Update folder ownership
        self._execute(
            "UPDATE folders SET user_id = ? WHERE id = ?",
            (new_owner_id, folder_id)
        )
        
        # Add old owner as editor (if not already)
        self._execute(
            """INSERT OR REPLACE INTO folder_permissions 
               (folder_id, user_id, permission, granted_by)
               VALUES (?, ?, 'editor', ?)""",
            (folder_id, old_owner_id, new_owner_id)
        )
        
        # Remove new owner from permissions (they are now owner)
        self._execute(
            "DELETE FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, new_owner_id)
        )
        
        self._commit()
        return True


# =============================================================================
# ASYNC VERSION (Issue #15)
# =============================================================================

class AsyncPermissionRepository(AsyncRepository):
    """Async repository for folder permission operations."""
    
    VALID_PERMISSIONS = {"viewer", "editor"}
    PERMISSION_HIERARCHY = {"viewer": 1, "editor": 2, "owner": 3}
    
    async def grant(
        self,
        folder_id: str,
        user_id: int,
        permission: str,
        granted_by: int
    ) -> bool:
        """Grant permission to user on folder."""
        if permission not in self.VALID_PERMISSIONS:
            raise ValueError(f"Invalid permission: {permission}. Use 'viewer' or 'editor'")
        
        try:
            await self._execute(
                """INSERT INTO folder_permissions 
                   (folder_id, user_id, permission, granted_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(folder_id, user_id) 
                   DO UPDATE SET 
                       permission = excluded.permission,
                       granted_by = excluded.granted_by,
                       granted_at = CURRENT_TIMESTAMP""",
                (folder_id, user_id, permission, granted_by)
            )
            await self._commit()
            return True
        except Exception:
            return False
    
    async def revoke(self, folder_id: str, user_id: int) -> bool:
        """Revoke permission from user."""
        cursor = await self._execute(
            "DELETE FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def get_permission(self, folder_id: str, user_id: int) -> str | None:
        """Get user's permission level for folder."""
        # Check if owner
        folder = await self._fetchone(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        )
        
        if folder and folder["user_id"] == user_id:
            return "owner"
        
        # Check explicit permission
        row = await self._fetchone(
            "SELECT permission FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)
        )
        return row["permission"] if row else None
    
    async def has_permission(
        self,
        folder_id: str,
        user_id: int,
        min_level: str = "viewer"
    ) -> bool:
        """Check if user has at least the specified permission level."""
        current = await self.get_permission(folder_id, user_id)
        if not current:
            return False
        
        min_value = self.PERMISSION_HIERARCHY.get(min_level, 1)
        current_value = self.PERMISSION_HIERARCHY.get(current, 0)
        return current_value >= min_value
    
    async def list_for_folder(self, folder_id: str) -> list[dict]:
        """Get all permissions for folder with user info."""
        return await self._fetchall(
            """SELECT fp.user_id, fp.permission, fp.granted_at,
                      u.username, u.display_name
               FROM folder_permissions fp
               JOIN users u ON fp.user_id = u.id
               WHERE fp.folder_id = ?
               ORDER BY u.display_name""",
            (folder_id,)
        )
    
    async def list_for_user(self, user_id: int) -> list[dict]:
        """Get all folders shared with user."""
        return await self._fetchall(
            """SELECT fp.folder_id, fp.permission, fp.granted_at,
                      f.name as folder_name, f.description,
                      u.display_name as owner_name
               FROM folder_permissions fp
               JOIN folders f ON fp.folder_id = f.id
               JOIN users u ON f.user_id = u.id
               WHERE fp.user_id = ?
               ORDER BY f.name""",
            (user_id,)
        )
    
    async def can_access(self, folder_id: str, user_id: int) -> bool:
        """Check if user can access folder (any permission level)."""
        if not folder_id:
            return True
        
        permission = await self.get_permission(folder_id, user_id)
        return permission is not None
    
    async def can_edit(self, folder_id: str, user_id: int) -> bool:
        """Check if user can edit folder content (owner or editor)."""
        if not folder_id:
            return False
        
        permission = await self.get_permission(folder_id, user_id)
        return permission in ("owner", "editor")
    
    async def update_permission(
        self,
        folder_id: str,
        user_id: int,
        new_permission: str
    ) -> bool:
        """Update existing permission level."""
        if new_permission not in self.VALID_PERMISSIONS:
            raise ValueError(f"Invalid permission: {new_permission}")
        
        cursor = await self._execute(
            "UPDATE folder_permissions SET permission = ? WHERE folder_id = ? AND user_id = ?",
            (new_permission, folder_id, user_id)
        )
        await self._commit()
        return cursor.rowcount > 0
    
    async def transfer_ownership(self, folder_id: str, new_owner_id: int) -> bool:
        """Transfer folder ownership to another user."""
        # Get current owner
        folder = await self._fetchone(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        )
        if not folder:
            return False
        
        old_owner_id = folder["user_id"]
        
        # Update folder ownership
        await self._execute(
            "UPDATE folders SET user_id = ? WHERE id = ?",
            (new_owner_id, folder_id)
        )
        
        # Add old owner as editor (if not already)
        await self._execute(
            """INSERT OR REPLACE INTO folder_permissions 
               (folder_id, user_id, permission, granted_by)
               VALUES (?, ?, 'editor', ?)""",
            (folder_id, old_owner_id, new_owner_id)
        )
        
        # Remove new owner from permissions (they are now owner)
        await self._execute(
            "DELETE FROM folder_permissions WHERE folder_id = ? AND user_id = ?",
            (folder_id, new_owner_id)
        )
        
        await self._commit()
        return True
    
    async def get_shared_users(self, folder_id: str) -> list[dict]:
        """Get list of users folder is shared with."""
        return await self._fetchall(
            """SELECT u.id, u.username, u.display_name, fp.permission
               FROM folder_permissions fp
               JOIN users u ON fp.user_id = u.id
               WHERE fp.folder_id = ?
               ORDER BY fp.permission DESC, u.display_name""",
            (folder_id,)
        )
    
    async def revoke_all_for_folder(self, folder_id: str) -> int:
        """Revoke all permissions for a folder."""
        cursor = await self._execute(
            "DELETE FROM folder_permissions WHERE folder_id = ?",
            (folder_id,)
        )
        await self._commit()
        return cursor.rowcount
    
    async def revoke_all_for_user(self, user_id: int) -> int:
        """Revoke all permissions granted to a user."""
        cursor = await self._execute(
            "DELETE FROM folder_permissions WHERE user_id = ?",
            (user_id,)
        )
        await self._commit()
        return cursor.rowcount
