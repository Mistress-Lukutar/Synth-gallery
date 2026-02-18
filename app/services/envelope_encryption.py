"""Envelope Encryption Service - Server-side helpers for client-side encryption.

This module provides server-side support for envelope encryption architecture:
- Content Key (CK) management per file
- Encrypted key storage and retrieval
- Shared access key management
- Folder-level encryption keys

Note: This module does NOT perform actual encryption/decryption of file content.
All encryption happens on the client side using WebCrypto API.
"""
import json
import secrets
from datetime import datetime
from typing import Optional
from pathlib import Path

from ..database import get_db


class EnvelopeEncryptionService:
    """Server-side service for managing envelope encryption keys.
    
    All encryption/decryption of actual file content happens on the client.
    Server only stores and serves encrypted keys and encrypted blobs.
    """

    # ==========================================================================
    # Content Key (CK) Management for Photos
    # ==========================================================================

    @staticmethod
    def create_photo_key(
        photo_id: str,
        encrypted_ck: bytes,
        encrypted_thumbnail_ck: Optional[bytes] = None
    ) -> bool:
        """Store encrypted content key for a photo.
        
        Args:
            photo_id: UUID of the photo
            encrypted_ck: Content Key encrypted with owner's DEK (from client)
            encrypted_thumbnail_ck: Optional separate key for thumbnail
        
        Returns:
            True if successful
        """
        db = get_db()
        try:
            db.execute(
                """INSERT INTO photo_keys 
                   (photo_id, encrypted_ck, thumbnail_encrypted_ck, shared_ck_map)
                   VALUES (?, ?, ?, ?)""",
                (photo_id, encrypted_ck, encrypted_thumbnail_ck, '{}')
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_photo_key(photo_id: str, user_id: int) -> Optional[dict]:
        """Get encrypted content key for a photo accessible by user.
        
        Returns the appropriate encrypted CK based on access:
        - If user is owner: returns encrypted_ck (encrypted with owner's DEK)
        - If user has shared access: returns encrypted_ck from shared_ck_map
        
        Args:
            photo_id: UUID of the photo
            user_id: ID of the requesting user
        
        Returns:
            Dict with encrypted_ck and metadata, or None if no access
        """
        db = get_db()
        
        # Get photo info to check ownership
        photo = db.execute(
            "SELECT user_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()
        
        if not photo:
            return None
        
        # Get the key record
        key_record = db.execute(
            "SELECT * FROM photo_keys WHERE photo_id = ?",
            (photo_id,)
        ).fetchone()
        
        if not key_record:
            return None
        
        is_owner = photo["user_id"] == user_id
        
        if is_owner:
            # Owner gets their encrypted CK
            return {
                "encrypted_ck": key_record["encrypted_ck"],
                "thumbnail_encrypted_ck": key_record["thumbnail_encrypted_ck"],
                "is_owner": True,
                "key_version": 1
            }
        else:
            # Check for shared access
            shared_map = json.loads(key_record["shared_ck_map"])
            user_key = shared_map.get(str(user_id))
            
            if user_key:
                return {
                    "encrypted_ck": user_key,
                    "thumbnail_encrypted_ck": None,  # Shared users get main key
                    "is_owner": False,
                    "key_version": 1
                }
        
        return None

    @staticmethod
    def share_photo_key(
        photo_id: str,
        owner_id: int,
        target_user_id: int,
        encrypted_ck_for_target: bytes
    ) -> bool:
        """Share a photo's content key with another user.
        
        Args:
            photo_id: UUID of the photo
            owner_id: ID of the photo owner (must be owner)
            target_user_id: ID of the user to share with
            encrypted_ck_for_target: CK encrypted with target user's public key/DEK
        
        Returns:
            True if successful
        """
        db = get_db()
        
        # Verify ownership
        photo = db.execute(
            "SELECT user_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()
        
        if not photo or photo["user_id"] != owner_id:
            return False
        
        try:
            # Get current shared map
            key_record = db.execute(
                "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
                (photo_id,)
            ).fetchone()
            
            if not key_record:
                return False
            
            shared_map = json.loads(key_record["shared_ck_map"])
            shared_map[str(target_user_id)] = encrypted_ck_for_target.hex()
            
            db.execute(
                "UPDATE photo_keys SET shared_ck_map = ? WHERE photo_id = ?",
                (json.dumps(shared_map), photo_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def revoke_photo_share(photo_id: str, owner_id: int, target_user_id: int) -> bool:
        """Revoke shared access to a photo.
        
        Args:
            photo_id: UUID of the photo
            owner_id: ID of the photo owner
            target_user_id: ID of the user to revoke access from
        
        Returns:
            True if successful
        """
        db = get_db()
        
        # Verify ownership
        photo = db.execute(
            "SELECT user_id FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()
        
        if not photo or photo["user_id"] != owner_id:
            return False
        
        try:
            key_record = db.execute(
                "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
                (photo_id,)
            ).fetchone()
            
            if not key_record:
                return False
            
            shared_map = json.loads(key_record["shared_ck_map"])
            shared_map.pop(str(target_user_id), None)
            
            db.execute(
                "UPDATE photo_keys SET shared_ck_map = ? WHERE photo_id = ?",
                (json.dumps(shared_map), photo_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    # ==========================================================================
    # Folder Key Management (for Shared Folders)
    # ==========================================================================

    @staticmethod
    def create_folder_key(folder_id: str, owner_id: int, encrypted_folder_dek: bytes) -> bool:
        """Create a folder-level DEK for shared folder encryption.
        
        Args:
            folder_id: UUID of the folder
            owner_id: ID of the folder owner
            encrypted_folder_dek: Folder DEK encrypted with owner's DEK
        
        Returns:
            True if successful
        """
        db = get_db()
        try:
            encrypted_map = {str(owner_id): encrypted_folder_dek.hex()}
            
            db.execute(
                """INSERT INTO folder_keys 
                   (folder_id, encrypted_folder_dek, created_by)
                   VALUES (?, ?, ?)""",
                (folder_id, json.dumps(encrypted_map), owner_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_folder_key(folder_id: str, user_id: int) -> Optional[bytes]:
        """Get encrypted folder DEK for a user.
        
        Args:
            folder_id: UUID of the folder
            user_id: ID of the requesting user
        
        Returns:
            Encrypted folder DEK, or None if no access
        """
        db = get_db()
        
        folder_key = db.execute(
            "SELECT encrypted_folder_dek FROM folder_keys WHERE folder_id = ?",
            (folder_id,)
        ).fetchone()
        
        if not folder_key:
            return None
        
        encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
        encrypted_dek_hex = encrypted_map.get(str(user_id))
        
        if encrypted_dek_hex:
            return bytes.fromhex(encrypted_dek_hex)
        
        return None

    @staticmethod
    def share_folder_key(
        folder_id: str,
        owner_id: int,
        target_user_id: int,
        encrypted_folder_dek_for_target: bytes
    ) -> bool:
        """Share folder DEK with a user.
        
        Args:
            folder_id: UUID of the folder
            owner_id: ID of the folder owner
            target_user_id: ID of the user to share with
            encrypted_folder_dek_for_target: Folder DEK encrypted for target user
        
        Returns:
            True if successful
        """
        db = get_db()
        
        # Verify ownership
        folder = db.execute(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        ).fetchone()
        
        if not folder or folder["user_id"] != owner_id:
            return False
        
        try:
            folder_key = db.execute(
                "SELECT encrypted_folder_dek FROM folder_keys WHERE folder_id = ?",
                (folder_id,)
            ).fetchone()
            
            if not folder_key:
                return False
            
            encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
            encrypted_map[str(target_user_id)] = encrypted_folder_dek_for_target.hex()
            
            db.execute(
                "UPDATE folder_keys SET encrypted_folder_dek = ? WHERE folder_id = ?",
                (json.dumps(encrypted_map), folder_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def revoke_folder_share(folder_id: str, owner_id: int, target_user_id: int) -> bool:
        """Revoke folder access from a user.
        
        Note: This does NOT re-encrypt existing files. It only prevents
        future access. For true revocation, files must be re-encrypted.
        
        Args:
            folder_id: UUID of the folder
            owner_id: ID of the folder owner
            target_user_id: ID of the user to revoke access from
        
        Returns:
            True if successful
        """
        db = get_db()
        
        folder = db.execute(
            "SELECT user_id FROM folders WHERE id = ?",
            (folder_id,)
        ).fetchone()
        
        if not folder or folder["user_id"] != owner_id:
            return False
        
        try:
            folder_key = db.execute(
                "SELECT encrypted_folder_dek FROM folder_keys WHERE folder_id = ?",
                (folder_id,)
            ).fetchone()
            
            if not folder_key:
                return False
            
            encrypted_map = json.loads(folder_key["encrypted_folder_dek"])
            encrypted_map.pop(str(target_user_id), None)
            
            db.execute(
                "UPDATE folder_keys SET encrypted_folder_dek = ? WHERE folder_id = ?",
                (json.dumps(encrypted_map), folder_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    # ==========================================================================
    # User Key Management (for Shared Access)
    # ==========================================================================

    @staticmethod
    def set_user_public_key(user_id: int, public_key: bytes) -> bool:
        """Store user's public key for shared key exchange.
        
        Args:
            user_id: User ID
            public_key: ECC public key bytes
        
        Returns:
            True if successful
        """
        db = get_db()
        try:
            db.execute(
                """INSERT INTO user_public_keys (user_id, public_key)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   public_key = excluded.public_key,
                   key_version = key_version + 1""",
                (user_id, public_key)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_user_public_key(user_id: int) -> Optional[bytes]:
        """Get user's public key.
        
        Args:
            user_id: User ID
        
        Returns:
            Public key bytes, or None if not set
        """
        db = get_db()
        result = db.execute(
            "SELECT public_key FROM user_public_keys WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        return result["public_key"] if result else None

    # ==========================================================================
    # Migration Helpers
    # ==========================================================================

    @staticmethod
    def migrate_photo_to_envelope(
        photo_id: str,
        encrypted_ck: bytes,
        encrypted_thumbnail_ck: Optional[bytes] = None
    ) -> bool:
        """Migrate a legacy encrypted photo to envelope encryption.
        
        This is called after the client has:
        1. Downloaded the legacy-encrypted file
        2. Decrypted it with the old DEK (server-side)
        3. Re-encrypted with a new random CK
        4. Encrypted the CK with their new client-side DEK
        
        Args:
            photo_id: UUID of the photo
            encrypted_ck: New content key encrypted with client's DEK
            encrypted_thumbnail_ck: Optional thumbnail key
        
        Returns:
            True if successful
        """
        db = get_db()
        try:
            # Insert into photo_keys
            db.execute(
                """INSERT INTO photo_keys 
                   (photo_id, encrypted_ck, thumbnail_encrypted_ck, shared_ck_map)
                   VALUES (?, ?, ?, '{}')""",
                (photo_id, encrypted_ck, encrypted_thumbnail_ck)
            )
            
            # Update photo storage_mode
            db.execute(
                "UPDATE photos SET storage_mode = 'envelope' WHERE id = ?",
                (photo_id,)
            )
            
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_user_legacy_photos(user_id: int) -> list:
        """Get all photos that still use legacy encryption.
        
        Args:
            user_id: User ID
        
        Returns:
            List of photo records needing migration
        """
        db = get_db()
        photos = db.execute(
            """SELECT id, filename, original_name, is_encrypted
               FROM photos 
               WHERE user_id = ? AND (storage_mode IS NULL OR storage_mode = 'legacy')
               ORDER BY uploaded_at""",
            (user_id,)
        ).fetchall()
        
        return [dict(p) for p in photos]

    @staticmethod
    def is_photo_migrated(photo_id: str) -> bool:
        """Check if a photo has been migrated to envelope encryption."""
        db = get_db()
        result = db.execute(
            "SELECT storage_mode FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()
        
        return result and result["storage_mode"] == 'envelope'

    @staticmethod
    def get_photo_shared_users(photo_id: str) -> list[int]:
        """Get list of user IDs that have shared access to a photo.
        
        Args:
            photo_id: UUID of the photo
            
        Returns:
            List of user IDs with shared access
        """
        db = get_db()
        result = db.execute(
            "SELECT shared_ck_map FROM photo_keys WHERE photo_id = ?",
            (photo_id,)
        ).fetchone()
        
        if not result:
            return []
        
        shared_map = json.loads(result["shared_ck_map"])
        return [int(uid) for uid in shared_map.keys()]

    @staticmethod
    def set_photo_storage_mode(photo_id: str, storage_mode: str) -> bool:
        """Set storage mode for a photo.
        
        Args:
            photo_id: UUID of the photo
            storage_mode: Storage mode ('legacy', 'envelope', etc.)
            
        Returns:
            True if successful
        """
        db = get_db()
        try:
            db.execute(
                "UPDATE photos SET storage_mode = ? WHERE id = ?",
                (storage_mode, photo_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_photo_storage_mode(photo_id: str) -> Optional[str]:
        """Get storage mode for a photo.
        
        Args:
            photo_id: UUID of the photo
            
        Returns:
            Storage mode string, or None if not found
        """
        db = get_db()
        result = db.execute(
            "SELECT storage_mode FROM photos WHERE id = ?",
            (photo_id,)
        ).fetchone()
        
        return result["storage_mode"] if result else None

    @staticmethod
    def update_folder_key(folder_id: str, encrypted_folder_dek_map: str) -> bool:
        """Update folder key encrypted map.
        
        Args:
            folder_id: UUID of the folder
            encrypted_folder_dek_map: JSON string of {user_id: encrypted_dek_hex}
            
        Returns:
            True if successful
        """
        db = get_db()
        try:
            db.execute(
                "UPDATE folder_keys SET encrypted_folder_dek = ? WHERE folder_id = ?",
                (encrypted_folder_dek_map, folder_id)
            )
            db.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_migration_status(user_id: int) -> dict:
        """Get migration status for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with migration status info
        """
        db = get_db()
        
        # Count legacy photos
        legacy_count = db.execute(
            """SELECT COUNT(*) as count FROM photos 
               WHERE user_id = ? AND (storage_mode IS NULL OR storage_mode = 'legacy')""",
            (user_id,)
        ).fetchone()["count"]
        
        # Count envelope photos
        envelope_count = db.execute(
            """SELECT COUNT(*) as count FROM photos 
               WHERE user_id = ? AND storage_mode = 'envelope'""",
            (user_id,)
        ).fetchone()["count"]
        
        return {
            "legacy_count": legacy_count,
            "envelope_count": envelope_count,
            "total_photos": legacy_count + envelope_count,
            "migration_complete": legacy_count == 0
        }

    @staticmethod
    def get_photos_needing_migration(user_id: int) -> list:
        """Get photos that need migration to envelope encryption.
        
        Args:
            user_id: User ID
            
        Returns:
            List of photo records needing migration
        """
        return EnvelopeEncryptionService.get_user_legacy_photos(user_id)

    @staticmethod
    def get_folder_key_full(folder_id: str) -> Optional[dict]:
        """Get full folder key record including created_by.
        
        Args:
            folder_id: UUID of the folder
            
        Returns:
            Dict with encrypted_folder_dek and created_by, or None
        """
        db = get_db()
        result = db.execute(
            "SELECT * FROM folder_keys WHERE folder_id = ?",
            (folder_id,)
        ).fetchone()
        
        if not result:
            return None
        
        return {
            "folder_id": result["folder_id"],
            "encrypted_folder_dek": result["encrypted_folder_dek"],
            "created_by": result["created_by"]
        }
