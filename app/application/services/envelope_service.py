"""Envelope encryption service - handles client-side encryption operations.

This service encapsulates business logic for envelope encryption:
- Content Keys (CK) for photos
- Folder keys for shared folders
- User public keys for key exchange
- Migration operations
"""
import base64
import json
from typing import Optional, List, Dict, Callable

from fastapi import HTTPException

from ...infrastructure.repositories import PhotoRepository, FolderRepository


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
        folder_repository: Optional[FolderRepository] = None
    ):
        self.photo_repo = photo_repository
        self.folder_repo = folder_repository
    
    # =========================================================================
    # User Public Key Management
    # =========================================================================
    
    def get_user_public_key(
        self,
        user_id: int,
        get_key_fn: Callable[[int], Optional[bytes]]
    ) -> Dict:
        """Get user's public key.
        
        Args:
            user_id: User ID
            get_key_fn: Function to get public key from database
            
        Returns:
            Public key data or not-found message
        """
        public_key = get_key_fn(user_id)
        
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
    
    def set_user_public_key(
        self,
        user_id: int,
        public_key_b64: str,
        set_key_fn: Callable[[int, bytes], bool]
    ) -> Dict:
        """Upload or update user's public key for shared key exchange.
        
        Args:
            user_id: User ID
            public_key_b64: Base64-encoded public key
            set_key_fn: Function to store public key in database
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        try:
            public_key = base64.b64decode(public_key_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")
        
        if len(public_key) < 32:  # Minimum reasonable key size
            raise HTTPException(status_code=400, detail="Public key too short")
        
        success = set_key_fn(user_id, public_key)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store public key")
        
        return {"success": True, "message": "Public key updated"}
    
    def get_user_encrypted_dek(
        self,
        user_id: int,
        get_encryption_keys_fn: Callable[[int], Optional[Dict]]
    ) -> Dict:
        """Get user's encrypted DEK and salt for client-side decryption.
        
        This endpoint returns the encrypted DEK that the client must decrypt
        using the user's password (PBKDF2).
        
        Args:
            user_id: User ID
            get_encryption_keys_fn: Function to get encryption keys from database
            
        Returns:
            Encryption key data
        """
        enc_keys = get_encryption_keys_fn(user_id)
        
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
    
    def get_other_user_public_key(
        self,
        target_user_id: int,
        get_key_fn: Callable[[int], Optional[bytes]]
    ) -> Dict:
        """Get another user's public key (for sharing).
        
        Args:
            target_user_id: Target user ID
            get_key_fn: Function to get public key from database
            
        Returns:
            Public key data
            
        Raises:
            HTTPException: If not found
        """
        public_key = get_key_fn(target_user_id)
        if not public_key:
            raise HTTPException(status_code=404, detail="User has no public key")
        
        return {
            "user_id": target_user_id,
            "public_key": base64.b64encode(public_key).decode()
        }
    
    # =========================================================================
    # Photo Content Key (CK) Management
    # =========================================================================
    
    def get_photo_key(
        self,
        photo_id: str,
        user_id: int,
        can_access_photo_fn: Callable[[str, int], bool],
        get_photo_key_fn: Callable[[str], Optional[Dict]],
        get_photo_shared_key_fn: Callable[[str, int], Optional[bytes]],
        get_photo_fn: Callable[[str], Optional[Dict]]
    ) -> Dict:
        """Get encrypted content key for a photo.
        
        Returns the appropriate encrypted CK based on access:
        - If owner: encrypted with owner's DEK
        - If shared access: encrypted with recipient's key
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            can_access_photo_fn: Function to check photo access
            get_photo_key_fn: Function to get photo key from database
            get_photo_shared_key_fn: Function to get shared key for user
            get_photo_fn: Function to get photo details
            
        Returns:
            Key data dict
            
        Raises:
            HTTPException: If no access or key not found
        """
        # Check access
        if not can_access_photo_fn(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        photo = get_photo_fn(photo_id)
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
        
        # Get envelope key
        key_data = get_photo_key_fn(photo_id)
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
            shared_ck = get_photo_shared_key_fn(photo_id, user_id)
            if shared_ck:
                return {
                    "storage_mode": "envelope",
                    "is_owner": False,
                    "encrypted_ck": base64.b64encode(shared_ck).decode(),
                    "thumbnail_encrypted_ck": None
                }
            else:
                raise HTTPException(status_code=403, detail="No shared key for this user")
    
    def upload_photo_key(
        self,
        photo_id: str,
        user_id: int,
        encrypted_ck_b64: str,
        thumbnail_encrypted_ck_b64: Optional[str],
        get_photo_fn: Callable[[str], Optional[Dict]],
        create_photo_key_fn: Callable[[str, bytes, Optional[bytes]], bool],
        set_storage_mode_fn: Callable[[str, str], bool]
    ) -> Dict:
        """Upload encrypted content key for a new photo.
        
        This is called after upload when using envelope encryption.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            encrypted_ck_b64: Base64-encoded encrypted CK
            thumbnail_encrypted_ck_b64: Base64-encoded thumbnail encrypted CK (optional)
            get_photo_fn: Function to get photo details
            create_photo_key_fn: Function to create photo key
            set_storage_mode_fn: Function to set storage mode
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Verify ownership
        photo = get_photo_fn(photo_id)
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
        success = create_photo_key_fn(photo_id, encrypted_ck, thumbnail_encrypted_ck)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store key")
        
        # Update storage mode
        set_storage_mode_fn(photo_id, "envelope")
        
        return {"success": True, "message": "Encryption key stored"}
    
    # =========================================================================
    # Photo Sharing Operations
    # =========================================================================
    
    def share_photo(
        self,
        photo_id: str,
        user_id: int,
        target_user_id: int,
        encrypted_ck_for_user_b64: str,
        get_photo_fn: Callable[[str], Optional[Dict]],
        get_storage_mode_fn: Callable[[str], Optional[str]],
        set_shared_key_fn: Callable[[str, int, bytes], bool]
    ) -> Dict:
        """Share a photo with another user by providing them encrypted CK.
        
        Args:
            photo_id: Photo ID
            user_id: Owner user ID
            target_user_id: Target user ID to share with
            encrypted_ck_for_user_b64: Base64-encoded CK encrypted for target user
            get_photo_fn: Function to get photo details
            get_storage_mode_fn: Function to get storage mode
            set_shared_key_fn: Function to set shared key
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Verify ownership
        photo = get_photo_fn(photo_id)
        if not photo or photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can share")
        
        # Check storage mode
        if get_storage_mode_fn(photo_id) != "envelope":
            raise HTTPException(status_code=400, detail="Photo must use envelope encryption")
        
        try:
            encrypted_ck_for_user = base64.b64decode(encrypted_ck_for_user_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")
        
        # Store shared key
        success = set_shared_key_fn(photo_id, target_user_id, encrypted_ck_for_user)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store shared key")
        
        return {"success": True, "message": f"Photo shared with user {target_user_id}"}
    
    def revoke_share(
        self,
        photo_id: str,
        user_id: int,
        target_user_id: int,
        get_photo_fn: Callable[[str], Optional[Dict]],
        remove_shared_key_fn: Callable[[str, int], bool]
    ) -> Dict:
        """Revoke shared access from a user.
        
        Args:
            photo_id: Photo ID
            user_id: Owner user ID
            target_user_id: Target user ID to revoke from
            get_photo_fn: Function to get photo details
            remove_shared_key_fn: Function to remove shared key
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Verify ownership
        photo = get_photo_fn(photo_id)
        if not photo or photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can revoke access")
        
        success = remove_shared_key_fn(photo_id, target_user_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to revoke access")
        
        return {"success": True, "message": f"Access revoked for user {target_user_id}"}
    
    def list_shares(
        self,
        photo_id: str,
        user_id: int,
        get_photo_fn: Callable[[str], Optional[Dict]],
        get_shared_users_fn: Callable[[str], List[int]],
        get_user_fn: Callable[[int], Optional[Dict]]
    ) -> Dict:
        """List users who have shared access to this photo.
        
        Args:
            photo_id: Photo ID
            user_id: Owner user ID
            get_photo_fn: Function to get photo details
            get_shared_users_fn: Function to get list of shared user IDs
            get_user_fn: Function to get user details
            
        Returns:
            Shares list
            
        Raises:
            HTTPException: If not owner
        """
        # Verify ownership
        photo = get_photo_fn(photo_id)
        if not photo or photo.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Only owner can view shares")
        
        shared_users = get_shared_users_fn(photo_id)
        
        # Get user details
        shares = []
        for uid in shared_users:
            u = get_user_fn(uid)
            if u:
                shares.append({
                    "user_id": uid,
                    "username": u["username"],
                    "display_name": u["display_name"]
                })
        
        return {"photo_id": photo_id, "shares": shares}
    
    # =========================================================================
    # Folder Key Management for Shared Folders
    # =========================================================================
    
    def get_folder_key(
        self,
        folder_id: str,
        user_id: int,
        can_access_folder_fn: Callable[[str, int], bool],
        get_folder_key_fn: Callable[[str], Optional[Dict]]
    ) -> Dict:
        """Get encrypted folder DEK for current user.
        
        Args:
            folder_id: Folder ID
            user_id: User ID
            can_access_folder_fn: Function to check folder access
            get_folder_key_fn: Function to get folder key
            
        Returns:
            Folder key data
            
        Raises:
            HTTPException: If no access or key not found
        """
        # Check access
        if not can_access_folder_fn(folder_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        folder_key = get_folder_key_fn(folder_id)
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
        encrypted_folder_dek_b64: str,
        get_folder_fn: Callable[[str], Optional[Dict]],
        get_folder_key_fn: Callable[[str], Optional[Dict]],
        create_key_fn: Callable[[str, int, str], bool]
    ) -> Dict:
        """Create folder key (called by owner when enabling folder encryption).
        
        Args:
            folder_id: Folder ID
            user_id: Owner user ID
            encrypted_folder_dek_b64: Base64-encoded JSON with encrypted DEK per user
            get_folder_fn: Function to get folder details
            get_folder_key_fn: Function to check if key already exists
            create_key_fn: Function to create folder key
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Verify ownership
        folder = get_folder_fn(folder_id)
        if not folder or folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only owner can create folder key")
        
        # Check if already exists
        existing = get_folder_key_fn(folder_id)
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
        
        success = create_key_fn(folder_id, user_id, encrypted_folder_dek_b64)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create folder key")
        
        return {"success": True, "message": "Folder key created"}
    
    def share_folder_key(
        self,
        folder_id: str,
        user_id: int,
        target_user_id: int,
        encrypted_folder_dek_for_user_b64: str,
        get_folder_fn: Callable[[str], Optional[Dict]],
        get_folder_key_fn: Callable[[str], Optional[Dict]],
        update_key_fn: Callable[[str, str], bool]
    ) -> Dict:
        """Share folder DEK with a user.
        
        Args:
            folder_id: Folder ID
            user_id: Owner user ID
            target_user_id: Target user ID to share with
            encrypted_folder_dek_for_user_b64: Base64-encoded encrypted DEK for target user
            get_folder_fn: Function to get folder details
            get_folder_key_fn: Function to get folder key
            update_key_fn: Function to update folder key
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Verify ownership
        folder = get_folder_fn(folder_id)
        if not folder or folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only owner can share folder key")
        
        folder_key = get_folder_key_fn(folder_id)
        if not folder_key:
            raise HTTPException(status_code=404, detail="Folder key not found")
        
        # Update encrypted map
        try:
            encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
            encrypted_map[str(target_user_id)] = base64.b64decode(encrypted_folder_dek_for_user_b64).hex()
            
            success = update_key_fn(folder_id, json.dumps(encrypted_map))
            if not success:
                raise HTTPException(status_code=500, detail="Failed to update folder key")
            
            return {"success": True, "message": f"Folder key shared with user {target_user_id}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid data: {str(e)}")
    
    # =========================================================================
    # Migration Operations
    # =========================================================================
    
    def get_migration_status(
        self,
        user_id: int,
        get_status_fn: Callable[[int], Dict]
    ) -> Dict:
        """Get migration status for current user.
        
        Args:
            user_id: User ID
            get_status_fn: Function to get migration status
            
        Returns:
            Migration status
        """
        return get_status_fn(user_id)
    
    def get_pending_photos(
        self,
        user_id: int,
        get_pending_fn: Callable[[int], List[Dict]]
    ) -> Dict:
        """Get list of photos needing migration.
        
        Args:
            user_id: User ID
            get_pending_fn: Function to get pending photos
            
        Returns:
            List of pending photos
        """
        photos = get_pending_fn(user_id)
        return {
            "count": len(photos),
            "photos": [
                {
                    "id": p["id"], 
                    "filename": p["filename"], 
                    "original_name": p["original_name"]
                } for p in photos
            ]
        }
    
    def batch_migrate(
        self,
        user_id: int,
        photo_keys: List[Dict],
        get_photo_fn: Callable[[str], Optional[Dict]],
        create_photo_key_fn: Callable[[str, bytes, Optional[bytes]], bool],
        set_storage_mode_fn: Callable[[str, str], bool]
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
            get_photo_fn: Function to get photo details
            create_photo_key_fn: Function to create photo key
            set_storage_mode_fn: Function to set storage mode
            
        Returns:
            Migration results
        """
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
            photo = get_photo_fn(photo_id)
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
                success = create_photo_key_fn(photo_id, encrypted_ck, thumbnail_encrypted_ck)
                if success:
                    set_storage_mode_fn(photo_id, "envelope")
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
