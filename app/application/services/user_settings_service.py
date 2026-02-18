"""User settings service - handles user preferences and settings.

This service encapsulates business logic for:
- Default folder management
- Collapsed folders state
- Sort preferences
- User encryption keys
"""
import json
from typing import Optional, List

from fastapi import HTTPException

from ...infrastructure.repositories import FolderRepository, PermissionRepository


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
        permission_repository: PermissionRepository = None
    ):
        self.folder_repo = folder_repository
        self.perm_repo = permission_repository
    
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
        db = self.folder_repo._db
        
        settings = db.execute(
            "SELECT default_folder_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if settings and settings["default_folder_id"]:
            # Verify folder still exists and user has access
            folder_id = settings["default_folder_id"]
            folder = self.folder_repo.get_by_id(folder_id)
            if folder and self._can_access_folder(folder_id, user_id):
                return folder_id
        
        # Create default folder if missing
        return self._create_default_folder(user_id)
    
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
        
        db = self.folder_repo._db
        
        # Check if user has settings row
        existing = db.execute(
            "SELECT user_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if existing:
            db.execute(
                "UPDATE user_settings SET default_folder_id = ? WHERE user_id = ?",
                (folder_id, user_id)
            )
        else:
            db.execute(
                "INSERT INTO user_settings (user_id, default_folder_id) VALUES (?, ?)",
                (user_id, folder_id)
            )
        
        db.commit()
        return True
    
    def _create_default_folder(self, user_id: int) -> str:
        """Create default folder for user."""
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
        db = self.folder_repo._db
        
        settings = db.execute(
            "SELECT collapsed_folders FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if settings and settings["collapsed_folders"]:
            try:
                return json.loads(settings["collapsed_folders"])
            except json.JSONDecodeError:
                return []
        return []
    
    def set_collapsed_folders(self, user_id: int, folder_ids: List[str]) -> bool:
        """Set list of collapsed folder IDs for a user.
        
        Args:
            user_id: User ID
            folder_ids: List of folder IDs
            
        Returns:
            True if successful
        """
        db = self.folder_repo._db
        
        collapsed_json = json.dumps(folder_ids)
        
        # Check if user has settings row
        existing = db.execute(
            "SELECT user_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if existing:
            db.execute(
                "UPDATE user_settings SET collapsed_folders = ? WHERE user_id = ?",
                (collapsed_json, user_id)
            )
        else:
            db.execute(
                "INSERT INTO user_settings (user_id, collapsed_folders) VALUES (?, ?)",
                (user_id, collapsed_json)
            )
        
        db.commit()
        return True
    
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
            Sort preference ('uploaded_at' or 'taken_at')
        """
        db = self.folder_repo._db
        
        settings = db.execute(
            """SELECT sort_by FROM user_folder_preferences 
               WHERE user_id = ? AND folder_id = ?""",
            (user_id, folder_id)
        ).fetchone()
        
        if settings and settings["sort_by"]:
            return settings["sort_by"]
        return "uploaded_at"  # Default
    
    def set_sort_preference(self, user_id: int, folder_id: str, sort_by: str) -> bool:
        """Set sort preference for a folder.
        
        Args:
            user_id: User ID
            folder_id: Folder ID
            sort_by: Sort field ('uploaded_at' or 'taken_at')
            
        Returns:
            True if successful
        """
        if sort_by not in ("uploaded_at", "taken_at"):
            raise HTTPException(status_code=400, detail="Invalid sort option")
        
        db = self.folder_repo._db
        
        # Check if preference exists
        existing = db.execute(
            """SELECT 1 FROM user_folder_preferences 
               WHERE user_id = ? AND folder_id = ?""",
            (user_id, folder_id)
        ).fetchone()
        
        if existing:
            db.execute(
                """UPDATE user_folder_preferences 
                   SET sort_by = ? WHERE user_id = ? AND folder_id = ?""",
                (sort_by, user_id, folder_id)
            )
        else:
            db.execute(
                """INSERT INTO user_folder_preferences (user_id, folder_id, sort_by)
                   VALUES (?, ?, ?)""",
                (user_id, folder_id, sort_by)
            )
        
        db.commit()
        return True
    
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
        db = self.folder_repo._db
        
        row = db.execute(
            """SELECT encrypted_dek, dek_salt, encryption_version,
                      recovery_encrypted_dek
               FROM user_settings WHERE user_id = ?""",
            (user_id,)
        ).fetchone()
        
        if not row or not row["encrypted_dek"]:
            return None
        
        return {
            "encrypted_dek": row["encrypted_dek"],
            "dek_salt": row["dek_salt"],
            "encryption_version": row["encryption_version"] or 1,
            "recovery_encrypted_dek": row["recovery_encrypted_dek"]
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
        db = self.folder_repo._db
        
        # Check if user has settings row
        existing = db.execute(
            "SELECT user_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if existing:
            db.execute(
                """UPDATE user_settings 
                   SET encrypted_dek = ?, dek_salt = ?, encryption_version = 1
                   WHERE user_id = ?""",
                (encrypted_dek, dek_salt, user_id)
            )
        else:
            db.execute(
                """INSERT INTO user_settings 
                   (user_id, encrypted_dek, dek_salt, encryption_version)
                   VALUES (?, ?, ?, 1)""",
                (user_id, encrypted_dek, dek_salt)
            )
        
        db.commit()
        return True
    
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
