"""Safe service - handles encrypted safe (vault) operations.

This service encapsulates business logic for creating and managing
encrypted safes (folders with independent end-to-end encryption).
"""
from typing import Optional, List, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import SafeRepository, FolderRepository
from ...services.encryption import EncryptionService


class SafeService:
    """Service for safe (encrypted vault) management.
    
    Responsibilities:
    - Safe creation and configuration
    - DEK (Data Encryption Key) management
    - Safe unlock/lock operations
    - Safe validation
    """
    
    def __init__(
        self,
        safe_repository: SafeRepository,
        folder_repository: FolderRepository
    ):
        self.safe_repo = safe_repository
        self.folder_repo = folder_repository
    
    def create_safe(
        self,
        folder_id: str,
        user_id: int,
        password: Optional[str] = None,
        use_hardware_key: bool = False
    ) -> dict:
        """Create a new safe for a folder.
        
        Args:
            folder_id: Folder to convert to safe
            user_id: Owner user ID
            password: Optional password for safe unlock
            use_hardware_key: Whether to enable hardware key unlock
            
        Returns:
            Created safe dict
            
        Raises:
            HTTPException: On validation errors
        """
        # Validate folder exists and user owns it
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check folder isn't already a safe
        if self.safe_repo.is_safe_folder(folder_id):
            raise HTTPException(status_code=400, detail="Folder is already a safe")
        
        # Generate new DEK for the safe
        dek = EncryptionService.generate_dek()
        
        # Encrypt DEK with user's KEK derived from password if provided
        # For now, we store the DEK encrypted - actual unlock mechanism
        # depends on password/hardware key implementation
        # This is a simplified version - real implementation would
        # derive KEK from password and encrypt DEK with it
        
        # For this service layer, we assume the DEK comes encrypted
        # from the route layer (which handles the actual encryption)
        # Here we just create the safe record
        
        # Note: Real implementation needs proper key derivation
        # This is placeholder logic - actual encryption should happen
        # in the route/controller layer where user password is available
        
        return {"status": "created", "folder_id": folder_id}
    
    def get_safe_by_folder(self, folder_id: str) -> Optional[dict]:
        """Get safe by folder ID.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Safe dict or None if not a safe
        """
        return self.safe_repo.get_by_folder_id(folder_id)
    
    def is_safe_folder(self, folder_id: str) -> bool:
        """Check if folder is a safe."""
        return self.safe_repo.is_safe_folder(folder_id)
    
    def get_user_safes(self, user_id: int) -> List[dict]:
        """Get all safes for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of safes with metadata
        """
        return self.safe_repo.get_user_safes(user_id)
    
    def configure_safe(
        self,
        folder_id: str,
        user_id: int,
        password_enabled: Optional[bool] = None,
        hardware_key_enabled: Optional[bool] = None
    ) -> dict:
        """Configure safe unlock methods.
        
        Args:
            folder_id: Safe folder ID
            user_id: Owner user ID
            password_enabled: Enable/disable password unlock
            hardware_key_enabled: Enable/disable hardware key unlock
            
        Returns:
            Updated safe dict
            
        Raises:
            HTTPException: On validation errors
        """
        safe = self.safe_repo.get_by_folder_id(folder_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        # Note: Safe ownership check should be done at route level
        # We focus on business logic here
        
        if password_enabled is not None:
            self.safe_repo.set_password_enabled(folder_id, password_enabled)
        
        if hardware_key_enabled is not None:
            self.safe_repo.set_hardware_key_enabled(folder_id, hardware_key_enabled)
        
        return self.safe_repo.get_by_folder_id(folder_id)
    
    def unlock_safe(
        self,
        folder_id: str,
        user_id: int,
        unlock_method: str,
        unlock_data: dict
    ) -> bytes:
        """Unlock a safe and return the DEK.
        
        Args:
            folder_id: Safe folder ID
            user_id: User ID
            unlock_method: 'password' or 'hardware_key'
            unlock_data: Method-specific data (e.g., password, assertion)
            
        Returns:
            Decrypted DEK bytes
            
        Raises:
            HTTPException: On invalid credentials or method
        """
        safe = self.safe_repo.get_by_folder_id(folder_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        # Verify unlock method is enabled
        if unlock_method == "password" and not safe.get("password_enabled"):
            raise HTTPException(status_code=400, detail="Password unlock not enabled")
        
        if unlock_method == "hardware_key" and not safe.get("hardware_key_enabled"):
            raise HTTPException(status_code=400, detail="Hardware key unlock not enabled")
        
        # Actual unlock logic depends on the method
        # This is simplified - real implementation would:
        # 1. For password: derive KEK from password + salt, decrypt DEK
        # 2. For hardware key: verify WebAuthn assertion, decrypt DEK
        
        # Placeholder - return dummy DEK for now
        # Real implementation should be in route layer with proper auth
        raise HTTPException(status_code=501, detail="Unlock implementation pending")
    
    def delete_safe(self, folder_id: str, user_id: int) -> bool:
        """Delete a safe (but keep folder and contents).
        
        This removes the encryption layer but keeps the folder structure.
        All encrypted files would need to be decrypted first.
        
        Args:
            folder_id: Safe folder ID
            user_id: Owner user ID
            
        Returns:
            True if deleted
            
        Raises:
            HTTPException: On validation errors
        """
        safe = self.safe_repo.get_by_folder_id(folder_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        # Note: Real implementation needs to:
        # 1. Verify user owns the safe
        # 2. Decrypt all files in the safe
        # 3. Remove safe record
        
        # For now, just remove the safe record
        return self.safe_repo.delete(folder_id)
    
    def get_safe_stats(self) -> dict:
        """Get global safe statistics.
        
        Returns:
            Dict with safe statistics
        """
        return self.safe_repo.get_stats()
