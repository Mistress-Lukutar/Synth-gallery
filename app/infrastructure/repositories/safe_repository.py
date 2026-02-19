"""Safe repository - handles encrypted vault operations.

Safes are independent encrypted containers with separate keys.
Each safe can be unlocked via password or WebAuthn hardware key.
"""
import uuid

from .base import Repository


class SafeRepository(Repository):
    """Repository for safe (encrypted vault) operations.
    
    Safes provide additional encryption layer for sensitive content.
    Each safe has its own DEK (Data Encryption Key) protected by:
    - Password (PBKDF2 key derivation)
    - WebAuthn hardware key
    
    Examples:
        >>> repo = SafeRepository(db)
        >>> safe_id = repo.create("Tax Documents", user_id, 
        ...                       encrypted_dek, unlock_type="password")
        >>> repo.unlock(safe_id, user_id, session_encrypted_dek)
        >>> is_unlocked = repo.is_unlocked(safe_id, user_id)
    """
    
    VALID_UNLOCK_TYPES = {"password", "webauthn"}
    
    def create(
        self,
        name: str,
        user_id: int,
        encrypted_dek: bytes,
        unlock_type: str,
        credential_id: bytes = None,
        salt: bytes = None,
        recovery_encrypted_dek: bytes = None
    ) -> str:
        """Create new safe.
        
        Args:
            name: Safe name
            user_id: Owner user ID
            encrypted_dek: DEK encrypted with safe key
            unlock_type: 'password' or 'webauthn'
            credential_id: WebAuthn credential ID (if webauthn type)
            salt: Salt for password derivation (if password type)
            recovery_encrypted_dek: Recovery key encrypted DEK (optional)
            
        Returns:
            New safe UUID
            
        Raises:
            ValueError: If unlock_type is invalid
        """
        if unlock_type not in self.VALID_UNLOCK_TYPES:
            raise ValueError(f"Invalid unlock_type: {unlock_type}")
        
        safe_id = str(uuid.uuid4())
        
        self._execute(
            """INSERT INTO safes 
               (id, name, user_id, encrypted_dek, unlock_type,
                credential_id, salt, recovery_encrypted_dek)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (safe_id, name.strip(), user_id, encrypted_dek, unlock_type,
             credential_id, salt, recovery_encrypted_dek)
        )
        self._commit()
        return safe_id
    
    def get_by_id(self, safe_id: str) -> dict | None:
        """Get safe by ID.
        
        Args:
            safe_id: Safe UUID
            
        Returns:
            Safe dict or None
        """
        cursor = self._execute(
            "SELECT * FROM safes WHERE id = ?",
            (safe_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_folder(self, folder_id: str) -> dict | None:
        """Get safe containing folder.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Safe dict or None
        """
        cursor = self._execute(
            """SELECT s.* FROM safes s
               JOIN folders f ON f.safe_id = s.id
               WHERE f.id = ?""",
            (folder_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    # Alias for compatibility with async version
    get_by_folder_id = get_by_folder
    
    def is_safe_folder(self, folder_id: str) -> bool:
        """Check if folder is in a safe.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            True if folder is in a safe
        """
        return self.get_by_folder(folder_id) is not None
    
    def get_safe_id_for_folder(self, folder_id: str) -> str | None:
        """Get safe_id for a folder if it's inside a safe.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            Safe ID or None if folder is not in a safe
        """
        cursor = self._execute(
            "SELECT safe_id FROM folders WHERE id = ?",
            (folder_id,)
        )
        row = cursor.fetchone()
        return row["safe_id"] if row else None
    
    def list_by_user(self, user_id: int) -> list[dict]:
        """Get all safes for user with counts.
        
        Args:
            user_id: User ID
            
        Returns:
            List of safe dicts with folder_count and photo_count
        """
        cursor = self._execute(
            """SELECT s.*,
                   (SELECT COUNT(*) FROM folders WHERE safe_id = s.id) as folder_count,
                   (SELECT COUNT(*) FROM photos WHERE safe_id = s.id) as photo_count
               FROM safes s
               WHERE s.user_id = ?
               ORDER BY s.created_at DESC""",
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def update(self, safe_id: str, name: str = None) -> bool:
        """Update safe name.
        
        Args:
            safe_id: Safe ID
            name: New name
            
        Returns:
            True if updated
        """
        if name is None:
            return False
        
        cursor = self._execute(
            "UPDATE safes SET name = ? WHERE id = ?",
            (name.strip(), safe_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, safe_id: str) -> bool:
        """Delete safe and all its contents.
        
        Args:
            safe_id: Safe ID
            
        Returns:
            True if deleted
        """
        # Get folders in safe
        cursor = self._execute(
            "SELECT id FROM folders WHERE safe_id = ?",
            (safe_id,)
        )
        folders = [row["id"] for row in cursor.fetchall()]
        
        # Delete photos in safe
        self._execute("DELETE FROM photos WHERE safe_id = ?", (safe_id,))
        
        # Delete albums in safe
        self._execute("DELETE FROM albums WHERE safe_id = ?", (safe_id,))
        
        # Delete folders in safe
        self._execute("DELETE FROM folders WHERE safe_id = ?", (safe_id,))
        
        # Delete safe sessions
        self._execute("DELETE FROM safe_sessions WHERE safe_id = ?", (safe_id,))
        
        # Delete safe
        cursor = self._execute("DELETE FROM safes WHERE id = ?", (safe_id,))
        self._commit()
        return cursor.rowcount > 0
    
    def set_password_enabled(self, folder_id: str, enabled: bool) -> bool:
        """Enable/disable password unlock (sync version - folder-based).
        
        Note: This is a compatibility method. The actual implementation
        uses safe_id based on current schema.
        
        Args:
            folder_id: Folder ID (which is the safe folder)
            enabled: True to enable password unlock
            
        Returns:
            True if updated
        """
        safe = self.get_by_folder(folder_id)
        if not safe:
            return False
        
        cursor = self._execute(
            "UPDATE safes SET password_enabled = ? WHERE id = ?",
            (enabled, safe["id"])
        )
        self._commit()
        return cursor.rowcount > 0
    
    def set_hardware_key_enabled(self, folder_id: str, enabled: bool) -> bool:
        """Enable/disable hardware key unlock (sync version - folder-based).
        
        Note: This is a compatibility method. The actual implementation
        uses safe_id based on current schema.
        
        Args:
            folder_id: Folder ID (which is the safe folder)
            enabled: True to enable hardware key unlock
            
        Returns:
            True if updated
        """
        safe = self.get_by_folder(folder_id)
        if not safe:
            return False
        
        cursor = self._execute(
            "UPDATE safes SET hardware_key_enabled = ? WHERE id = ?",
            (enabled, safe["id"])
        )
        self._commit()
        return cursor.rowcount > 0
    
    # Safe Session Management
    
    def create_session(
        self,
        safe_id: str,
        user_id: int,
        encrypted_dek: bytes,
        expires_hours: int = 24
    ) -> str:
        """Create unlocked safe session.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            encrypted_dek: DEK encrypted with session key
            expires_hours: Session lifetime
            
        Returns:
            Session ID
        """
        import secrets
        session_id = secrets.token_urlsafe(32)
        
        self._execute(
            """INSERT INTO safe_sessions (id, safe_id, user_id, encrypted_dek, expires_at)
               VALUES (?, ?, ?, ?, datetime('now', '+' || ? || ' hours'))""",
            (session_id, safe_id, user_id, encrypted_dek, expires_hours)
        )
        self._commit()
        return session_id
    
    def get_session(self, session_id: str) -> dict | None:
        """Get valid (non-expired) session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session dict or None if expired/invalid
        """
        cursor = self._execute(
            """SELECT * FROM safe_sessions 
               WHERE id = ? AND expires_at > datetime('now')""",
            (session_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session (lock safe).
        
        Args:
            session_id: Session ID
            
        Returns:
            True if existed and deleted
        """
        cursor = self._execute(
            "DELETE FROM safe_sessions WHERE id = ?",
            (session_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete_all_sessions(self, safe_id: str, user_id: int = None) -> int:
        """Delete all sessions for safe (force lock).
        
        Args:
            safe_id: Safe ID
            user_id: Optional user filter
            
        Returns:
            Number of sessions deleted
        """
        if user_id:
            cursor = self._execute(
                "DELETE FROM safe_sessions WHERE safe_id = ? AND user_id = ?",
                (safe_id, user_id)
            )
        else:
            cursor = self._execute(
                "DELETE FROM safe_sessions WHERE safe_id = ?",
                (safe_id,)
            )
        self._commit()
        return cursor.rowcount
    
    def cleanup_expired_sessions(self) -> int:
        """Remove all expired sessions.
        
        Returns:
            Number of sessions cleaned up
        """
        cursor = self._execute(
            "DELETE FROM safe_sessions WHERE expires_at <= datetime('now')"
        )
        self._commit()
        return cursor.rowcount
    
    def is_unlocked(self, safe_id: str, user_id: int) -> bool:
        """Check if safe has valid session for user.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            True if unlocked
        """
        cursor = self._execute(
            """SELECT 1 FROM safe_sessions 
               WHERE safe_id = ? AND user_id = ? AND expires_at > datetime('now')""",
            (safe_id, user_id)
        )
        return cursor.fetchone() is not None
    
    def list_unlocked(self, user_id: int) -> list[str]:
        """Get list of unlocked safe IDs for user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of safe IDs
        """
        cursor = self._execute(
            """SELECT safe_id FROM safe_sessions 
               WHERE user_id = ? AND expires_at > datetime('now')""",
            (user_id,)
        )
        return [row["safe_id"] for row in cursor.fetchall()]
    
    def get_unlock_session(self, safe_id: str, user_id: int) -> dict | None:
        """Get most recent valid session for safe.
        
        Args:
            safe_id: Safe ID
            user_id: User ID
            
        Returns:
            Session dict or None
        """
        cursor = self._execute(
            """SELECT * FROM safe_sessions 
               WHERE safe_id = ? AND user_id = ? AND expires_at > datetime('now')
               ORDER BY created_at DESC
               LIMIT 1""",
            (safe_id, user_id)
        )
        return self._row_to_dict(cursor.fetchone())
    
    # Folder/Photo Safe Assignment
    
    def assign_folder(self, folder_id: str, safe_id: str) -> bool:
        """Move folder into safe.
        
        Args:
            folder_id: Folder ID
            safe_id: Safe ID
            
        Returns:
            True if updated
        """
        cursor = self._execute(
            "UPDATE folders SET safe_id = ? WHERE id = ?",
            (safe_id, folder_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def remove_folder(self, folder_id: str) -> bool:
        """Remove folder from safe.
        
        Args:
            folder_id: Folder ID
            
        Returns:
            True if updated
        """
        cursor = self._execute(
            "UPDATE folders SET safe_id = NULL WHERE id = ?",
            (folder_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def get_folders(self, safe_id: str) -> list[dict]:
        """Get folders in safe.
        
        Args:
            safe_id: Safe ID
            
        Returns:
            List of folder dicts
        """
        cursor = self._execute(
            """SELECT f.*, 
                   (SELECT COUNT(*) FROM photos WHERE folder_id = f.id) as photo_count
               FROM folders f
               WHERE f.safe_id = ?
               ORDER BY f.name""",
            (safe_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    
    # Statistics
    
    def get_stats(self, safe_id: str) -> dict:
        """Get safe statistics.
        
        Args:
            safe_id: Safe ID
            
        Returns:
            Stats dict
        """
        # Folder count
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM folders WHERE safe_id = ?",
            (safe_id,)
        )
        folder_count = cursor.fetchone()["count"]
        
        # Photo count
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM photos WHERE safe_id = ?",
            (safe_id,)
        )
        photo_count = cursor.fetchone()["count"]
        
        return {
            "folder_count": folder_count,
            "photo_count": photo_count
        }
