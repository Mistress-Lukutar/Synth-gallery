"""Permission service - handles folder sharing and access control.

This service encapsulates business logic for folder permissions,
including granting/revoking access and checking permissions.
"""
from typing import Optional, List, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import PermissionRepository, FolderRepository, PhotoRepository


class PermissionService:
    """Service for folder permission management.
    
    Responsibilities:
    - Grant/revoke folder permissions
    - Check user access levels
    - List shared folders
    - Permission hierarchy validation
    """
    
    VALID_PERMISSIONS = {"viewer", "editor"}
    PERMISSION_HIERARCHY = {"viewer": 1, "editor": 2, "owner": 3}
    
    def __init__(
        self,
        permission_repository: PermissionRepository,
        folder_repository: FolderRepository,
        photo_repository: PhotoRepository = None
    ):
        self.perm_repo = permission_repository
        self.folder_repo = folder_repository
        self.photo_repo = photo_repository
    
    def grant_permission(
        self,
        folder_id: str,
        user_id: int,
        permission: str,
        granted_by: int
    ) -> bool:
        """Grant permission to a user on a folder.
        
        Args:
            folder_id: Target folder ID
            user_id: User to grant permission to
            permission: 'viewer' or 'editor'
            granted_by: User ID granting the permission (must be owner)
            
        Returns:
            True if permission was granted
            
        Raises:
            HTTPException: On validation errors
        """
        # Validate permission value
        if permission not in self.VALID_PERMISSIONS:
            raise HTTPException(
                status_code=400,
                detail="Permission must be 'viewer' or 'editor'"
            )
        
        # Check folder exists and granter is owner
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != granted_by:
            raise HTTPException(
                status_code=403,
                detail="Only folder owner can manage permissions"
            )
        
        # Cannot grant permission to yourself
        if user_id == granted_by:
            raise HTTPException(
                status_code=400,
                detail="Cannot set permission for yourself"
            )
        
        return self.perm_repo.grant(folder_id, user_id, permission, granted_by)
    
    def revoke_permission(
        self,
        folder_id: str,
        user_id: int,
        revoked_by: int
    ) -> bool:
        """Revoke permission from a user.
        
        Args:
            folder_id: Target folder ID
            user_id: User to revoke permission from
            revoked_by: User ID revoking the permission (must be owner)
            
        Returns:
            True if permission was revoked
            
        Raises:
            HTTPException: On validation errors
        """
        # Check folder exists and revoker is owner
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != revoked_by:
            raise HTTPException(
                status_code=403,
                detail="Only folder owner can manage permissions"
            )
        
        return self.perm_repo.revoke(folder_id, user_id)
    
    def update_permission(
        self,
        folder_id: str,
        user_id: int,
        new_permission: str,
        updated_by: int
    ) -> bool:
        """Update existing permission.
        
        Args:
            folder_id: Target folder ID
            user_id: User to update permission for
            new_permission: New permission level ('viewer' or 'editor')
            updated_by: User ID making the change (must be owner)
            
        Returns:
            True if permission was updated
            
        Raises:
            HTTPException: On validation errors
        """
        if new_permission not in self.VALID_PERMISSIONS:
            raise HTTPException(
                status_code=400,
                detail="Permission must be 'viewer' or 'editor'"
            )
        
        # Check folder exists and updater is owner
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != updated_by:
            raise HTTPException(
                status_code=403,
                detail="Only folder owner can manage permissions"
            )
        
        return self.perm_repo.update_permission(folder_id, user_id, new_permission)
    
    def get_user_permission(
        self,
        folder_id: str,
        user_id: int
    ) -> Optional[str]:
        """Get user's permission level on a folder.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            'owner', 'editor', 'viewer', or None
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            return None
        
        # User is owner
        if folder["user_id"] == user_id:
            return "owner"
        
        # Check explicit permission
        return self.perm_repo.get_permission(folder_id, user_id)
    
    def has_permission(
        self,
        folder_id: str,
        user_id: int,
        min_level: str = "viewer"
    ) -> bool:
        """Check if user has at least the required permission level.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            min_level: Minimum required level ('viewer', 'editor', or 'owner')
            
        Returns:
            True if user has required permission
        """
        current = self.get_user_permission(folder_id, user_id)
        if not current:
            return False
        
        return self.PERMISSION_HIERARCHY.get(current, 0) >= \
               self.PERMISSION_HIERARCHY.get(min_level, 0)
    
    def can_access(self, folder_id: str, user_id: int) -> bool:
        """Check if user can access folder (any permission)."""
        return self.has_permission(folder_id, user_id, "viewer")
    
    def can_edit(self, folder_id: str, user_id: int) -> bool:
        """Check if user can edit folder (editor or owner)."""
        return self.has_permission(folder_id, user_id, "editor")
    
    def get_folder_permissions(self, folder_id: str, user_id: int) -> List[dict]:
        """Get all permissions for a folder.
        
        Args:
            folder_id: Folder ID
            user_id: User requesting (must be owner)
            
        Returns:
            List of permission records
            
        Raises:
            HTTPException: If user is not owner
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(
                status_code=403,
                detail="Only folder owner can view permissions"
            )
        
        return self.perm_repo.list_for_folder(folder_id)
    
    def get_shared_folders(self, user_id: int) -> List[dict]:
        """Get folders shared with user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of folders with permission info
        """
        return self.perm_repo.list_for_user(user_id)
    
    def transfer_ownership(
        self,
        folder_id: str,
        new_owner_id: int,
        current_owner_id: int
    ) -> bool:
        """Transfer folder ownership to another user.
        
        Args:
            folder_id: Folder to transfer
            new_owner_id: New owner user ID
            current_owner_id: Current owner (must match)
            
        Returns:
            True if transfer successful
            
        Raises:
            HTTPException: On validation errors
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != current_owner_id:
            raise HTTPException(
                status_code=403,
                detail="Only owner can transfer ownership"
            )
        
        if new_owner_id == current_owner_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot transfer to yourself"
            )
        
        return self.perm_repo.transfer_ownership(folder_id, new_owner_id)
    
    # =========================================================================
    # Photo & Album Permission Checks
    # =========================================================================
    
    def can_access_photo(self, photo_id: str, user_id: int) -> bool:
        """Check if user can access photo.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            
        Returns:
            True if user can access the photo
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            return False
        
        # Owner always has access
        if photo["user_id"] == user_id:
            return True
        
        # Check folder access if photo is in a folder
        if photo.get("folder_id"):
            return self.can_access(photo["folder_id"], user_id)
        
        # Check album's folder if photo is in album
        if photo.get("album_id"):
            album = self.album_repo.get_by_id(photo["album_id"]) if self.album_repo else None
            if album:
                if album["user_id"] == user_id:
                    return True
                if album.get("folder_id"):
                    return self.can_access(album["folder_id"], user_id)
        
        # Legacy photos without folder/user - accessible to all authenticated users
        if photo.get("folder_id") is None and photo.get("user_id") is None:
            return True
        
        return False
    
    def can_delete_photo(self, photo_id: str, user_id: int) -> bool:
        """Check if user can delete photo.
        
        Rules:
        - Photo owner can always delete
        - Folder owner can delete any photo in their folder
        - Editor can only delete photos they uploaded
        - Viewer cannot delete
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            
        Returns:
            True if user can delete the photo
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            return False
        
        # Photo owner can always delete
        if photo["user_id"] == user_id:
            return True
        
        # Check folder permissions
        if photo.get("folder_id"):
            folder = self.folder_repo.get_by_id(photo["folder_id"])
            if folder:
                # Folder owner can delete any photo
                if folder["user_id"] == user_id:
                    return True
                
                # Editor can only delete their own uploads
                perm = self.perm_repo.get_permission(photo["folder_id"], user_id)
                if perm == "editor":
                    return False  # Editor can't delete others' photos
        
        return False
    
    def can_access_album(self, album_id: str, user_id: int) -> bool:
        """Check if user can access album.
        
        Args:
            album_id: Album ID
            user_id: User ID
            
        Returns:
            True if user can access the album
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        album = self.photo_repo.get_album(album_id)
        if not album:
            return False
        
        # Owner always has access
        if album["user_id"] == user_id:
            return True
        
        # Check folder access
        if album.get("folder_id"):
            return self.can_access(album["folder_id"], user_id)
        
        # Legacy albums without folder/user - accessible to all
        if album.get("folder_id") is None and album.get("user_id") is None:
            return True
        
        return False
    
    def can_delete_album(self, album_id: str, user_id: int) -> bool:
        """Check if user can delete album.
        
        Rules:
        - Album owner can always delete
        - Folder owner can delete any album in their folder
        - Editor can only delete albums they created
        - Viewer cannot delete
        
        Args:
            album_id: Album ID
            user_id: User ID
            
        Returns:
            True if user can delete the album
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        album = self.photo_repo.get_album(album_id)
        if not album:
            return False
        
        # Album owner can always delete
        if album["user_id"] == user_id:
            return True
        
        # Check folder permissions
        if album.get("folder_id"):
            folder = self.folder_repo.get_by_id(album["folder_id"])
            if folder:
                # Folder owner can delete any album
                if folder["user_id"] == user_id:
                    return True
        
        return False
    
    def can_edit_album(self, album_id: str, user_id: int) -> bool:
        """Check if user can edit album (add/remove photos, rename).
        
        Args:
            album_id: Album ID
            user_id: User ID
            
        Returns:
            True if user can edit the album
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        album = self.photo_repo.get_album(album_id)
        if not album:
            return False
        
        # Album owner can always edit
        if album["user_id"] == user_id:
            return True
        
        # Check folder permissions - editor can edit albums in shared folders
        if album.get("folder_id"):
            return self.can_edit(album["folder_id"], user_id)
        
        return False
