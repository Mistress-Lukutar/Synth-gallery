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
from ...infrastructure.services.encryption import EncryptionService


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
    
    # =========================================================================
    # Profile Settings
    # =========================================================================
    
    def update_display_name(self, user_id: int, display_name: str) -> bool:
        """Update user's display name.
        
        Args:
            user_id: User ID
            display_name: New display name
            
        Returns:
            True if successful
        """
        if not self.user_repo:
            raise HTTPException(status_code=500, detail="UserRepository not configured")
        
        return self.user_repo.update_display_name(user_id, display_name)
    
    def change_password(
        self,
        user_id: int,
        old_password: str,
        new_password: str
    ) -> bool:
        """Change user password and re-encrypt DEK.
        
        Args:
            user_id: User ID
            old_password: Current password
            new_password: New password
            
        Returns:
            True if successful
            
        Raises:
            HTTPException: If old password is wrong or operation fails
        """
        if len(new_password) < 4:
            raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
        
        if not self.user_repo:
            raise HTTPException(status_code=500, detail="UserRepository not configured")
        
        # Verify old password
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        from ...database import verify_password
        if not verify_password(old_password, user["password_hash"], user.get("password_salt", "")):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        
        # Check if user has encryption keys
        enc_keys = self.user_repo.get_encryption_keys(user_id)
        if enc_keys:
            # Decrypt DEK with old password
            old_kek = EncryptionService.derive_kek(old_password, enc_keys["dek_salt"])
            try:
                dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], old_kek)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to decrypt encryption key: {e}")
            
            # Re-encrypt DEK with new password
            new_salt = EncryptionService.generate_salt()
            new_kek = EncryptionService.derive_kek(new_password, new_salt)
            new_encrypted_dek = EncryptionService.encrypt_dek(dek, new_kek)
            
            # Update password and encryption keys
            self.user_repo.update_password(user_id, new_password)
            self.user_repo.save_encryption_keys(user_id, new_encrypted_dek, new_salt)
        else:
            # No encryption - just update password
            self.user_repo.update_password(user_id, new_password)
        
        return True
    
    # =========================================================================
    # Recovery Key
    # =========================================================================
    
    def has_recovery_key(self, user_id: int) -> bool:
        """Check if user has recovery key configured.
        
        Args:
            user_id: User ID
            
        Returns:
            True if recovery key exists
        """
        if not self.user_repo:
            return False
        
        recovery_dek = self.user_repo.get_recovery_encrypted_dek(user_id)
        return recovery_dek is not None
    
    def generate_recovery_key(self, user_id: int, password: str) -> str:
        """Generate recovery key for user.
        
        Args:
            user_id: User ID
            password: Current password (to decrypt DEK)
            
        Returns:
            Formatted recovery key (shown ONCE)
            
        Raises:
            HTTPException: If password is wrong or DEK cannot be decrypted
        """
        if not self.user_repo:
            raise HTTPException(status_code=500, detail="UserRepository not configured")
        
        # Verify password
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        from ...database import verify_password
        if not verify_password(password, user["password_hash"], user.get("password_salt", "")):
            raise HTTPException(status_code=400, detail="Invalid password")
        
        # Get encryption keys
        enc_keys = self.user_repo.get_encryption_keys(user_id)
        if not enc_keys:
            raise HTTPException(status_code=400, detail="No encryption keys found")
        
        # Decrypt DEK with password
        try:
            kek = EncryptionService.derive_kek(password, enc_keys["dek_salt"])
            dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], kek)
        except Exception:
            raise HTTPException(status_code=400, detail="Could not decrypt encryption key")
        
        # Generate recovery key and encrypt DEK with it
        formatted_key, raw_key = EncryptionService.generate_recovery_key()
        recovery_encrypted_dek = EncryptionService.encrypt_dek_with_recovery_key(dek, raw_key)
        
        # Store in database
        self.user_repo.set_recovery_encrypted_dek(user_id, recovery_encrypted_dek)
        
        return formatted_key
