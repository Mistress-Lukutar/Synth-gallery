"""User settings service - handles user preferences and settings.

This service encapsulates business logic for:
- Default folder management
- Collapsed folders state
- Sort preferences
- User encryption keys
"""
from typing import Optional, List

from fastapi import HTTPException

from ...infrastructure.repositories import FolderRepository, PermissionRepository, UserRepository


class UserSettingsService:
    """Service for user settings and preferences.
    
    Responsibilities:
    - Default folder management
    - Collapsed folder state
    - Sort preferences per folder
    - User encryption keys
    """
    
    def __init__(
        self,
        folder_repository: FolderRepository,
        permission_repository: PermissionRepository = None,
        user_repository: UserRepository = None
    ):
        self.folder_repo = folder_repository
        self.perm_repo = permission_repository
        self.user_repo = user_repository
    
    # =========================================================================
    # Default Folder
    # =========================================================================
    
    def get_default_folder(self, user_id: int) -> str:
        """Get user's default folder ID, create if doesn't exist.
        
        Args:
            user_id: User ID
            
        Returns:
            Default folder ID
        """
        folder_id = self.user_repo.get_default_folder(user_id) if self.user_repo else None
        
        if folder_id:
            # Verify folder still exists and user has access
            folder = self.folder_repo.get_by_id(folder_id)
            if folder and self._can_access_folder(folder_id, user_id):
                return folder_id
        
        # Create default folder if missing
        return self.create_default_folder(user_id)
    
    def set_default_folder(self, user_id: int, folder_id: str) -> bool:
        """Set user's default folder.
        
        Args:
            user_id: User ID
            folder_id: Folder ID
            
        Returns:
            True if successful
            
        Raises:
            HTTPException: If folder not found or no access
        """
        # Verify folder exists and user has access
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if not self._can_access_folder(folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot access this folder")
        
        if not self.user_repo:
            raise HTTPException(status_code=500, detail="UserRepository not configured")
        
        return self.user_repo.set_default_folder(user_id, folder_id)
    
    def create_default_folder(self, user_id: int) -> str:
        """Create default folder for user.
        
        Args:
            user_id: User ID
            
        Returns:
            New folder ID
        """
        folder_id = self.folder_repo.create("My Gallery", user_id)
        self.set_default_folder(user_id, folder_id)
        return folder_id
    
    # =========================================================================
    # Collapsed Folders
    # =========================================================================
    
    def get_collapsed_folders(self, user_id: int) -> List[str]:
        """Get list of collapsed folder IDs for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of folder IDs
        """
        if not self.user_repo:
            return []
        return self.user_repo.get_collapsed_folders(user_id)
    
    def set_collapsed_folders(self, user_id: int, folder_ids: List[str]) -> bool:
        """Set list of collapsed folder IDs for a user.
        
        Args:
            user_id: User ID
            folder_ids: List of folder IDs
            
        Returns:
            True if successful
        """
        if not self.user_repo:
            return False
        return self.user_repo.set_collapsed_folders(user_id, folder_ids)
    
    def toggle_collapsed_folder(self, user_id: int, folder_id: str) -> bool:
        """Toggle collapsed state for a folder.
        
        Args:
            user_id: User ID
            folder_id: Folder ID
            
        Returns:
            True if folder is now collapsed, False if expanded
        """
        collapsed = self.get_collapsed_folders(user_id)
        
        if folder_id in collapsed:
            collapsed.remove(folder_id)
            is_collapsed = False
        else:
            collapsed.append(folder_id)
            is_collapsed = True
        
        self.set_collapsed_folders(user_id, collapsed)
        return is_collapsed
    
    # =========================================================================
    # Sort Preferences
    # =========================================================================
    
    def get_sort_preference(self, user_id: int, folder_id: str) -> str:
        """Get sort preference for a folder.
        
        Args:
            user_id: User ID
            folder_id: Folder ID
            
        Returns:
            Sort preference ('uploaded' or 'taken')
        """
        if not self.user_repo:
            return "uploaded"
        sort = self.user_repo.get_sort_preference(user_id, folder_id)
        # Map database values to API values
        if sort == "uploaded_at":
            return "uploaded"
        if sort == "taken_at":
            return "taken"
        return sort if sort else "uploaded"
    
    def set_sort_preference(self, user_id: int, folder_id: str, sort_by: str) -> bool:
        """Set sort preference for a folder.
        
        Args:
            user_id: User ID
            folder_id: Folder ID
            sort_by: Sort field ('uploaded' or 'taken')
            
        Returns:
            True if successful
        """
        if sort_by not in ("uploaded", "taken"):
            raise HTTPException(status_code=400, detail="Invalid sort option")
        
        # Map API values to database values
        if sort_by == "uploaded":
            sort_by = "uploaded_at"
        elif sort_by == "taken":
            sort_by = "taken_at"
        
        if not self.user_repo:
            raise HTTPException(status_code=500, detail="UserRepository not configured")
        
        return self.user_repo.set_sort_preference(user_id, folder_id, sort_by)
    
    # =========================================================================
    # Encryption Keys
    # =========================================================================
    
    def get_encryption_keys(self, user_id: int) -> Optional[dict]:
        """Get user's encryption keys.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with encrypted_dek, dek_salt, etc. or None
        """
        if not self.user_repo:
            return None
        
        keys = self.user_repo.get_encryption_keys(user_id)
        if not keys:
            return None
        
        return {
            "encrypted_dek": keys["encrypted_dek"],
            "dek_salt": keys["dek_salt"],
            "encryption_version": keys.get("encryption_version", 1),
            "recovery_encrypted_dek": keys.get("recovery_encrypted_dek")
        }
    
    def set_encryption_keys(
        self,
        user_id: int,
        encrypted_dek: bytes,
        dek_salt: bytes
    ) -> bool:
        """Set user's encryption keys.
        
        Args:
            user_id: User ID
            encrypted_dek: Encrypted DEK
            dek_salt: DEK salt
            
        Returns:
            True if successful
        """
        if not self.user_repo:
            return False
        return self.user_repo.save_encryption_keys(user_id, encrypted_dek, dek_salt)
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _can_access_folder(self, folder_id: str, user_id: int) -> bool:
        """Check if user can access folder."""
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            return False
        
        # Owner has access
        if folder["user_id"] == user_id:
            return True
        
        # Check explicit permission
        if self.perm_repo:
            perm = self.perm_repo.get_permission(folder_id, user_id)
            return perm is not None
        
        return False
