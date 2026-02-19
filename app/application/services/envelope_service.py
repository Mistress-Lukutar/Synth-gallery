"""Envelope encryption service - handles client-side encryption operations.

This service encapsulates business logic for envelope encryption:
- Content Keys (CK) for photos
- Folder keys for shared folders
- User public keys for key exchange
- Migration operations
"""
import base64
import json
from typing import Optional, List, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import PhotoRepository, FolderRepository, UserRepository
from .permission_service import PermissionService


class EnvelopeService:
    """Service for envelope encryption operations.
    
    Responsibilities:
    - User public key management
    - Photo content key (CK) management
    - Photo sharing operations
    - Folder key management for shared folders
    - Migration operations
    """
    
    def __init__(
        self,
        photo_repository: Optional[PhotoRepository] = None,
        folder_repository: Optional[FolderRepository] = None,
        user_repository: Optional[UserRepository] = None,
        permission_service: Optional[PermissionService] = None
    ):
        self.photo_repo = photo_repository
        self.folder_repo = folder_repository
        self.user_repo = user_repository
        self.perm_service = permission_service
    
    # =========================================================================
    # User Public Key Management
    # =========================================================================
    
    def get_user_public_key(self, user_id: int) -> Dict:
        """Get user's public key.
        
        Args:
            user_id: User ID
            
        Returns:
            Public key data or not-found message
        """
        if not self.user_repo:
            raise RuntimeError("UserRepository not configured")
            
        public_key = self.user_repo.get_public_key(user_id)
        
        if not public_key:
            return {
                "has_key": False,
                "message": "No public key set. Upload one to enable sharing."
            }
        
        return {
            "has_key": True,
            "public_key": base64.b64encode(public_key).decode(),
            "key_version": 1
        }
    
    def set_user_public_key(self, user_id: int, public_key_b64: str) -> Dict:
        """Upload or update user's public key for shared key exchange.
        
        Args:
            user_id: User ID
            public_key_b64: Base64-encoded public key
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.user_repo:
            raise RuntimeError("UserRepository not configured")
            
        try:
            public_key = base64.b64decode(public_key_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")
        
        if len(public_key) < 32:  # Minimum reasonable key size
            raise HTTPException(status_code=400, detail="Public key too short")
        
        success = self.user_repo.set_public_key(user_id, public_key)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store public key")
        
        return {"success": True, "message": "Public key updated"}
    
    def get_user_encrypted_dek(self, user_id: int) -> Dict:
        """Get user's encrypted DEK and salt for client-side decryption.
        
        This endpoint returns the encrypted DEK that the client must decrypt
        using the user's password (PBKDF2).
        
        Args:
            user_id: User ID
            
        Returns:
            Encryption key data
        """
        if not self.user_repo:
            raise RuntimeError("UserRepository not configured")
            
        enc_keys = self.user_repo.get_encryption_keys(user_id)
        
        if not enc_keys:
            # New user - no encryption keys yet
            return {
                "has_encryption": False,
                "needs_setup": True,
                "message": "Encryption not initialized. Initialize to create new keys."
            }
        
        return {
            "has_encryption": True,
            "encrypted_dek": base64.b64encode(enc_keys["encrypted_dek"]).decode(),
            "salt": base64.b64encode(enc_keys["dek_salt"]).decode(),
            "version": enc_keys.get("encryption_version", 1)
        }
    
    def get_other_user_public_key(self, target_user_id: int) -> Dict:
        """Get another user's public key (for sharing).
        
        Args:
            target_user_id: Target user ID
            
        Returns:
            Public key data
            
        Raises:
            HTTPException: If not found
        """
        if not self.user_repo:
            raise RuntimeError("UserRepository not configured")
            
        public_key = self.user_repo.get_public_key(target_user_id)
        if not public_key:
            raise HTTPException(status_code=404, detail="User has no public key")
        
        return {
            "user_id": target_user_id,
            "public_key": base64.b64encode(public_key).decode()
        }
    
    # =========================================================================
    # Photo Content Key (CK) Management
    # =========================================================================
    
    def get_photo_key(self, photo_id: str, user_id: int) -> Dict:
        """Get encrypted content key for a photo.
        
        Returns the appropriate encrypted CK based on access:
        - If owner: encrypted with owner's DEK
        - If shared access: encrypted with recipient's key
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            
        Returns:
            Key data dict
            
        Raises:
            HTTPException: If no access or key not found
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        if not self.perm_service:
            raise RuntimeError("PermissionService not configured")
        
        # Check access
        if not self.perm_service.can_access_photo(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        storage_mode = photo.get("storage_mode") or "legacy"
        
        # For legacy photos, return special response
        if storage_mode == "legacy":
            return {
                "storage_mode": "legacy",
                "is_encrypted": photo.get("is_encrypted", False),
                "message": "Photo uses legacy encryption. Migrate to envelope encryption."
            }
        
        # Get envelope key from repository
        key_data = self.photo_repo.get_photo_key(photo_id)
        if not key_data:
            raise HTTPException(status_code=404, detail="Encryption key not found")
        
        is_owner = photo.get("user_id") == user_id
        
        if is_owner:
            encrypted_ck = key_data["encrypted_ck"]
            thumb_ck = key_data.get("thumbnail_encrypted_ck")
            return {
                "storage_mode": "envelope",
                "is_owner": True,
                "encrypted_ck": base64.b64encode(encrypted_ck).decode(),
                "thumbnail_encrypted_ck": base64.b64encode(thumb_ck).decode() if thumb_ck else None
            }
        else:
            # Check for shared key
            shared_ck = self.photo_repo.get_photo_shared_key(photo_id, user_id)
            if shared_ck:
                return {
                    "storage_mode": "envelope",
                    "is_owner": False,
                    "encrypted_ck": base64.b64encode(shared_ck).decode(),
                    "thumbnail_encrypted_ck": None
                }
            else:
                raise HTTPException(status_code=403, detail="No shared key for this user")
    
    def create_photo_key(
        self,
        photo_id: str,
        encrypted_ck: bytes,
        thumbnail_encrypted_ck: Optional[bytes] = None
    ) -> bool:
        """Create encrypted content key for a photo.
        
        Args:
            photo_id: Photo ID
            encrypted_ck: Encrypted content key bytes
            thumbnail_encrypted_ck: Encrypted thumbnail content key bytes (optional)
            
        Returns:
            True if successful
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
    
    def upload_photo_key(
        self,
        photo_id: str,
        user_id: int,
        encrypted_ck_b64: str,
        thumbnail_encrypted_ck_b64: Optional[str] = None
    ) -> Dict:
        """Upload encrypted content key for a new photo.
        
        This is called after upload when using envelope encryption.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            encrypted_ck_b64: Base64-encoded encrypted CK
            thumbnail_encrypted_ck_b64: Base64-encoded thumbnail encrypted CK (optional)
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        # Verify ownership
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        if photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can set encryption key")
        
        try:
            encrypted_ck = base64.b64decode(encrypted_ck_b64)
            thumbnail_encrypted_ck = base64.b64decode(thumbnail_encrypted_ck_b64) if thumbnail_encrypted_ck_b64 else None
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")
        
        # Store the key
        success = self.photo_repo.create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store key")
        
        # Update storage mode
        self.photo_repo.set_storage_mode(photo_id, "envelope")
        
        return {"success": True, "message": "Encryption key stored"}
    
    def get_photo_storage_mode(self, photo_id: str) -> str:
        """Get storage mode for a photo.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            Storage mode ('legacy' or 'envelope')
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.get_storage_mode(photo_id)
    
    def set_photo_storage_mode(self, photo_id: str, mode: str) -> bool:
        """Set storage mode for a photo.
        
        Args:
            photo_id: Photo ID
            mode: Storage mode ('legacy' or 'envelope')
            
        Returns:
            True if successful
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.set_storage_mode(photo_id, mode)
    
    # =========================================================================
    # Photo Sharing Operations
    # =========================================================================
    
    def share_photo(
        self,
        photo_id: str,
        user_id: int,
        target_user_id: int,
        encrypted_ck_for_user_b64: str
    ) -> Dict:
        """Share a photo with another user by providing them encrypted CK.
        
        Args:
            photo_id: Photo ID
            user_id: Owner user ID
            target_user_id: Target user ID to share with
            encrypted_ck_for_user_b64: Base64-encoded CK encrypted for target user
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        # Verify ownership
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo or photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can share")
        
        # Check storage mode
        if self.photo_repo.get_storage_mode(photo_id) != "envelope":
            raise HTTPException(status_code=400, detail="Photo must use envelope encryption")
        
        try:
            encrypted_ck_for_user = base64.b64decode(encrypted_ck_for_user_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")
        
        # Store shared key
        success = self.photo_repo.set_photo_shared_key(photo_id, target_user_id, encrypted_ck_for_user)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store shared key")
        
        return {"success": True, "message": f"Photo shared with user {target_user_id}"}
    
    def revoke_photo_share(
        self,
        photo_id: str,
        user_id: int,
        target_user_id: int
    ) -> bool:
        """Revoke shared access from a user.
        
        Args:
            photo_id: Photo ID
            user_id: Owner user ID
            target_user_id: Target user ID to revoke from
            
        Returns:
            True if successful
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        # Verify ownership
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo or photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can revoke access")
        
        return self.photo_repo.remove_photo_shared_key(photo_id, target_user_id)
    
    def get_photo_shared_users(self, photo_id: str) -> List[int]:
        """Get list of user IDs who have shared access to this photo.
        
        Args:
            photo_id: Photo ID
            
        Returns:
            List of user IDs
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.get_photo_shared_users(photo_id)
    
    # =========================================================================
    # Folder Key Management for Shared Folders
    # =========================================================================
    
    def get_folder_key(self, folder_id: str) -> Optional[Dict]:
        """Get folder key data.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Folder key dict or None
        """
        if not self.folder_repo:
            raise RuntimeError("FolderRepository not configured")
        
        return self.folder_repo.get_folder_key(folder_id)
    
    def get_folder_key_for_user(self, folder_id: str, user_id: int) -> Dict:
        """Get encrypted folder DEK for current user.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            
        Returns:
            Folder key data
            
        Raises:
            HTTPException: If no access or key not found
        """
        if not self.folder_repo:
            raise RuntimeError("FolderRepository not configured")
        if not self.perm_service:
            raise RuntimeError("PermissionService not configured")
        
        # Check access
        if not self.perm_service.can_access(folder_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        folder_key = self.folder_repo.get_folder_key(folder_id)
        if not folder_key:
            raise HTTPException(status_code=404, detail="Folder key not found")
        
        encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
        user_key_hex = encrypted_map.get(str(user_id))
        
        if not user_key_hex:
            raise HTTPException(status_code=403, detail="No key for this user")
        
        return {
            "folder_id": folder_id,
            "encrypted_folder_dek": user_key_hex,
            "is_owner": folder_key["created_by"] == user_id
        }
    
    def create_folder_key(
        self,
        folder_id: str,
        user_id: int,
        encrypted_folder_dek_b64: str
    ) -> Dict:
        """Create folder key (called by owner when enabling folder encryption).
        
        Args:
            folder_id: Folder ID
            user_id: Owner user ID
            encrypted_folder_dek_b64: Base64-encoded JSON with encrypted DEK per user
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.folder_repo:
            raise RuntimeError("FolderRepository not configured")
        
        # Verify ownership
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder or folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only owner can create folder key")
        
        # Check if already exists
        existing = self.folder_repo.get_folder_key(folder_id)
        if existing:
            raise HTTPException(status_code=400, detail="Folder key already exists")
        
        try:
            # Validate JSON structure
            dek_map = json.loads(base64.b64decode(encrypted_folder_dek_b64))
            if str(user_id) not in dek_map:
                raise HTTPException(status_code=400, detail="Must include owner's encrypted DEK")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in encrypted_folder_dek")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid data")
        
        success = self.folder_repo.create_folder_key(folder_id, user_id, encrypted_folder_dek_b64)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create folder key")
        
        return {"success": True, "message": "Folder key created"}
    
    def update_folder_key(self, folder_id: str, encrypted_folder_dek_json: str) -> bool:
        """Update folder key encrypted map.
        
        Args:
            folder_id: Folder ID
            encrypted_folder_dek_json: JSON string with encrypted DEK map
            
        Returns:
            True if successful
        """
        if not self.folder_repo:
            raise RuntimeError("FolderRepository not configured")
        
        return self.folder_repo.update_folder_key(folder_id, encrypted_folder_dek_json)
    
    def share_folder_key(
        self,
        folder_id: str,
        user_id: int,
        target_user_id: int,
        encrypted_folder_dek_for_user_b64: str
    ) -> Dict:
        """Share folder DEK with a user.
        
        Args:
            folder_id: Folder ID
            user_id: Owner user ID
            target_user_id: Target user ID to share with
            encrypted_folder_dek_for_user_b64: Base64-encoded encrypted DEK for target user
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        if not self.folder_repo:
            raise RuntimeError("FolderRepository not configured")
        
        # Verify ownership
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder or folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only owner can share folder key")
        
        folder_key = self.folder_repo.get_folder_key(folder_id)
        if not folder_key:
            raise HTTPException(status_code=404, detail="Folder key not found")
        
        # Update encrypted map
        try:
            encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
            encrypted_map[str(target_user_id)] = base64.b64decode(encrypted_folder_dek_for_user_b64).hex()
            
            success = self.folder_repo.update_folder_key(folder_id, json.dumps(encrypted_map))
            if not success:
                raise HTTPException(status_code=500, detail="Failed to update folder key")
            
            return {"success": True, "message": f"Folder key shared with user {target_user_id}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid data: {str(e)}")
    
    # =========================================================================
    # Migration Operations
    # =========================================================================
    
    def get_migration_status(self, user_id: int) -> Dict:
        """Get migration status for current user.
        
        Args:
            user_id: User ID
            
        Returns:
            Migration status
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.get_migration_status(user_id)
    
    def get_photos_needing_migration(self, user_id: int) -> List[Dict]:
        """Get list of photos needing migration.
        
        Args:
            user_id: User ID
            
        Returns:
            List of pending photos
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        return self.photo_repo.get_photos_needing_migration(user_id)
    
    def batch_migrate(
        self,
        user_id: int,
        photo_keys: List[Dict]
    ) -> Dict:
        """Batch migrate photos to envelope encryption.
        
        This is called by the client after it has:
        1. Downloaded legacy-encrypted photos
        2. Decrypted them (server provided DEK temporarily)
        3. Generated new CKs
        4. Re-encrypted photos
        5. Encrypted CKs with client's DEK
        
        Args:
            user_id: User ID
            photo_keys: List of {photo_id, encrypted_ck, thumbnail_encrypted_ck}
            
        Returns:
            Migration results
        """
        if not self.photo_repo:
            raise RuntimeError("PhotoRepository not configured")
        
        results = []
        
        for item in photo_keys:
            photo_id = item.get("photo_id")
            encrypted_ck_b64 = item.get("encrypted_ck")
            thumbnail_encrypted_ck_b64 = item.get("thumbnail_encrypted_ck")
            
            if not photo_id or not encrypted_ck_b64:
                results.append({
                    "photo_id": photo_id, 
                    "success": False, 
                    "error": "Missing data"
                })
                continue
            
            # Verify ownership
            photo = self.photo_repo.get_by_id(photo_id)
            if not photo or photo.get("user_id") != user_id:
                results.append({
                    "photo_id": photo_id, 
                    "success": False, 
                    "error": "Not owner"
                })
                continue
            
            try:
                encrypted_ck = base64.b64decode(encrypted_ck_b64)
                thumbnail_encrypted_ck = base64.b64decode(thumbnail_encrypted_ck_b64) if thumbnail_encrypted_ck_b64 else None
                
                # Store new key
                success = self.photo_repo.create_photo_key(photo_id, encrypted_ck, thumbnail_encrypted_ck)
                if success:
                    self.photo_repo.set_storage_mode(photo_id, "envelope")
                    results.append({"photo_id": photo_id, "success": True})
                else:
                    results.append({
                        "photo_id": photo_id, 
                        "success": False, 
                        "error": "Database error"
                    })
            except Exception as e:
                results.append({
                    "photo_id": photo_id, 
                    "success": False, 
                    "error": str(e)
                })
        
        success_count = sum(1 for r in results if r["success"])
        return {
            "total": len(photo_keys),
            "successful": success_count,
            "failed": len(photo_keys) - success_count,
            "results": results
        }
