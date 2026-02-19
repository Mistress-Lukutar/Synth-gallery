"""Safe service - handles encrypted safe (vault) operations.

This service encapsulates business logic for creating and managing
encrypted safes (folders with independent end-to-end encryption).
"""
import base64
from typing import Optional, List, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import SafeRepository, FolderRepository
from ...infrastructure.services.encryption import EncryptionService


class SafeService:
    """Service for safe (encrypted vault) management.
    
    Responsibilities:
    - Safe creation and configuration
    - DEK (Data Encryption Key) management
    - Safe unlock/lock operations
    - Safe validation
    - Safe session management
    """
    
    def __init__(
        self,
        safe_repository: SafeRepository,
        folder_repository: Optional[FolderRepository] = None
    ):
        self.safe_repo = safe_repository
        self.folder_repo = folder_repository
    
    # =========================================================================
    # Safe CRUD Operations
    # =========================================================================
    
    def list_safes(self, user_id: int) -> Dict:
        """Get all safes for user with unlock status.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with safes list
        """
        safes = self.safe_repo.list_by_user(user_id)
        unlocked = self.safe_repo.list_unlocked(user_id)
        
        return {
            "safes": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "created_at": s["created_at"],
                    "unlock_type": s["unlock_type"],
                    "folder_count": s["folder_count"],
                    "photo_count": s["photo_count"],
                    "is_unlocked": s["id"] in unlocked
                }
                for s in safes
            ]
        }
    
    def create_safe(
        self,
        name: str,
        user_id: int,
        unlock_type: str,
        encrypted_dek_b64: str,
        password: Optional[str] = None,
        salt_b64: Optional[str] = None,
        credential_id_b64: Optional[str] = None
    ) -> Dict:
        """Create a new safe.
        
        Args:
            name: Safe name
            user_id: Owner user ID
            unlock_type: 'password' or 'webauthn'
            encrypted_dek_b64: Base64-encoded encrypted DEK
            password: Password (required for password unlock)
            salt_b64: Base64-encoded salt (required for password unlock)
            credential_id_b64: Base64-encoded credential ID (required for webauthn)
            
        Returns:
            Created safe info dict
            
        Raises:
            HTTPException: On validation errors
        """
        # Validate unlock type
        if unlock_type not in ('password', 'webauthn'):
            raise HTTPException(
                status_code=400, 
                detail="unlock_type must be 'password' or 'webauthn'"
            )
        
        # Validate based on unlock type
        if unlock_type == 'password':
            if not password or len(password) < 8:
                raise HTTPException(
                    status_code=400, 
                    detail="Password must be at least 8 characters"
                )
            if not salt_b64:
                raise HTTPException(
                    status_code=400, 
                    detail="Salt required for password-based safe"
                )
        elif unlock_type == 'webauthn':
            if not credential_id_b64:
                raise HTTPException(
                    status_code=400, 
                    detail="credential_id required for WebAuthn safe"
                )
        
        # Decode encrypted DEK
        try:
            encrypted_dek = self._decode_base64(encrypted_dek_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid encrypted_dek format")
        
        # Decode salt if provided
        salt = None
        if salt_b64:
            try:
                salt = self._decode_base64(salt_b64)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid salt format")
        
        # Decode credential_id if provided
        credential_id = None
        if credential_id_b64:
            try:
                credential_id = self._decode_base64(credential_id_b64)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid credential_id format")
        
        # Create safe
        safe_id = self.safe_repo.create(
            name=name,
            user_id=user_id,
            encrypted_dek=encrypted_dek,
            unlock_type=unlock_type,
            credential_id=credential_id,
            salt=salt
        )
        
        return {
            "status": "ok",
            "safe_id": safe_id,
            "message": "Safe created successfully"
        }
    
    def get_safe_details(self, safe_id: str, user_id: int) -> Dict:
        """Get safe details.
        
        Args:
            safe_id: Safe ID
            user_id: User requesting details
            
        Returns:
            Safe details dict
            
        Raises:
            HTTPException: If not found or no access
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        is_unlocked = self.safe_repo.is_unlocked(safe_id, user_id)
        
        return {
            "id": safe["id"],
            "name": safe["name"],
            "created_at": safe["created_at"],
            "unlock_type": safe["unlock_type"],
            "is_unlocked": is_unlocked,
            "has_recovery": safe["recovery_encrypted_dek"] is not None
        }
    
    def rename_safe(self, safe_id: str, name: str, user_id: int) -> Dict:
        """Rename a safe.
        
        Args:
            safe_id: Safe ID
            name: New name
            user_id: User making the request
            
        Returns:
            Updated safe info
            
        Raises:
            HTTPException: If not found or no access
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        self.safe_repo.update(safe_id, name)
        
        return {"status": "ok", "name": name}
    
    def delete_safe(self, safe_id: str, user_id: int) -> Dict:
        """Delete a safe and all its contents.
        
        Args:
            safe_id: Safe ID
            user_id: User making the request (must be owner)
            
        Returns:
            Success message
            
        Raises:
            HTTPException: If not found or no access
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        self.safe_repo.delete(safe_id)
        
        return {"status": "ok", "message": "Safe deleted"}
    
    # =========================================================================
    # Safe Unlock/Lock Operations
    # =========================================================================
    
    def get_unlock_challenge(
        self, 
        safe_id: str, 
        user_id: int
    ) -> Dict:
        """Get challenge data for unlocking a safe.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            Challenge data dict
            
        Raises:
            HTTPException: If not found, no access, or invalid unlock type
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        if safe["unlock_type"] == 'password':
            if not safe["salt"]:
                raise HTTPException(
                    status_code=500, 
                    detail="Safe is missing salt - may have been created with old code"
                )
            if not safe["encrypted_dek"]:
                raise HTTPException(
                    status_code=500, 
                    detail="Safe is missing encrypted_dek"
                )
            
            return {
                "status": "challenge",
                "type": "password",
                "encrypted_dek": self._encode_base64(safe["encrypted_dek"]),
                "salt": self._encode_base64(safe["salt"])
            }
        
        elif safe["unlock_type"] == 'webauthn':
            # Return minimal data - WebAuthn challenge handled by caller
            return {
                "status": "challenge",
                "type": "webauthn",
                "safe_id": safe_id
            }
        
        else:
            raise HTTPException(status_code=400, detail="Invalid unlock type")
    
    def complete_unlock(
        self,
        safe_id: str,
        user_id: int,
        session_encrypted_dek_b64: str
    ) -> Dict:
        """Complete safe unlock and create session.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            session_encrypted_dek_b64: Base64-encoded session-encrypted DEK
            
        Returns:
            Session info dict
            
        Raises:
            HTTPException: On validation errors
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Decode session-encrypted DEK
        try:
            session_encrypted_dek = self._decode_base64(session_encrypted_dek_b64)
        except Exception:
            raise HTTPException(
                status_code=400, 
                detail="Invalid session_encrypted_dek format"
            )
        
        # Create safe session (24 hours by default)
        session_id = self.safe_repo.create_session(
            safe_id=safe_id,
            user_id=user_id,
            encrypted_dek=session_encrypted_dek,
            expires_hours=24
        )
        
        return {
            "status": "ok",
            "session_id": session_id,
            "safe_id": safe_id,
            "message": "Safe unlocked successfully"
        }
    
    def lock_safe(self, safe_id: str, user_id: int) -> Dict:
        """Lock a safe (invalidate all sessions).
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            Success message
            
        Raises:
            HTTPException: If not found or no access
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Delete all sessions for this safe and user
        self.safe_repo.delete_all_sessions(safe_id, user_id)
        
        return {"status": "ok", "message": "Safe locked"}
    
    def get_unlocked_safes(self, user_id: int) -> Dict:
        """Get list of currently unlocked safe IDs.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with unlocked safe IDs list
        """
        # Cleanup expired sessions first
        self.safe_repo.cleanup_expired_sessions()
        
        unlocked = self.safe_repo.list_unlocked(user_id)
        
        return {"unlocked_safes": unlocked}
    
    def get_safe_key(self, safe_id: str, user_id: int) -> Dict:
        """Get encrypted safe key for file operations.
        
        Returns the session-encrypted DEK for client-side decryption.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            Key data dict
            
        Raises:
            HTTPException: If not found, no access, or safe locked
        """
        safe = self.safe_repo.get_by_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if safe is unlocked
        if not self.safe_repo.is_unlocked(safe_id, user_id):
            raise HTTPException(
                status_code=403, 
                detail="Safe is locked. Please unlock first."
            )
        
        # Get the session
        session = self.safe_repo.get_unlock_session(safe_id, user_id)
        
        if not session:
            raise HTTPException(status_code=403, detail="Safe session expired")
        
        return {
            "safe_id": safe_id,
            "session_id": session["id"],
            "encrypted_dek": base64.b64encode(session["encrypted_dek"]).decode(),
            "expires_at": session["expires_at"]
        }
    
    # =========================================================================
    # Legacy/Compatibility Methods
    # =========================================================================
    
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
        if hasattr(self.safe_repo, 'is_safe_folder'):
            return self.safe_repo.is_safe_folder(folder_id)
        return self.safe_repo.get_by_folder_id(folder_id) is not None
    
    def is_unlocked(self, safe_id: str, user_id: int) -> bool:
        """Check if safe is unlocked for user.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            True if safe has an active unlock session
        """
        return self.safe_repo.is_unlocked(safe_id, user_id)
    
    def get_safe_folder_id(self, folder_id: str) -> Optional[str]:
        """Get safe_id for a folder if it's in a safe.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Safe ID or None
        """
        return self.safe_repo.get_safe_id_for_folder(folder_id)
    
    def get_user_safes(self, user_id: int) -> List[dict]:
        """Get all safes for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of safes with metadata
        """
        return self.safe_repo.list_by_user(user_id)
    
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
            success = self.safe_repo.set_password_enabled(folder_id, password_enabled)
            if not success:
                raise HTTPException(status_code=404, detail="Safe not found")
        
        if hardware_key_enabled is not None:
            success = self.safe_repo.set_hardware_key_enabled(folder_id, hardware_key_enabled)
            if not success:
                raise HTTPException(status_code=404, detail="Safe not found")
        
        return self.safe_repo.get_by_folder_id(folder_id)
    
    def get_safe_stats(self) -> dict:
        """Get global safe statistics.
        
        Returns:
            Dict with safe statistics
        """
        # Note: This gets stats for a single safe if safe_id provided
        # or global stats if not. Here we return empty dict for global.
        return {}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _decode_base64(self, data: str) -> bytes:
        """Decode URL-safe base64 string to bytes."""
        padding = 4 - len(data) % 4
        if padding != 4:
            data += '=' * padding
        return base64.urlsafe_b64decode(data)
    
    def _encode_base64(self, data: bytes) -> str:
        """Encode bytes to URL-safe base64 string."""
        return base64.urlsafe_b64encode(data).decode().rstrip('=')
