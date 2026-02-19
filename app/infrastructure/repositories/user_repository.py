"""User repository - handles all user-related database operations.

This is the NEW implementation. Old functions in database.py now delegate here.
"""
import bcrypt
from typing import Optional
from .base import Repository


class UserRepository(Repository):
    """Repository for user entity operations.
    
    Examples:
        >>> repo = UserRepository(db)
        >>> user = repo.get_by_id(1)
        >>> user_id = repo.create("john", "password123", "John Doe")
    """
    
    def get_by_id(self, user_id: int) -> dict | None:
        """Get user by ID.
        
        Args:
            user_id: User ID
            
        Returns:
            User dict or None if not found
        """
        cursor = self._execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_by_username(self, username: str) -> dict | None:
        """Get user by username (case-insensitive).
        
        Args:
            username: Username to search
            
        Returns:
            User dict or None if not found
        """
        cursor = self._execute(
            "SELECT * FROM users WHERE username = ?",
            (username.lower().strip(),)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def create(self, username: str, password: str, display_name: str) -> int:
        """Create new user.
        
        Args:
            username: Unique username
            password: Plain text password (will be hashed)
            display_name: Display name
            
        Returns:
            New user ID
        """
        password_hash, _ = self._hash_password(password)
        
        cursor = self._execute(
            """INSERT INTO users 
               (username, password_hash, password_salt, display_name) 
               VALUES (?, ?, ?, ?)""",
            (username.lower().strip(), password_hash, "", display_name.strip())
        )
        self._commit()
        return cursor.lastrowid
    
    def update_password(self, user_id: int, new_password: str) -> bool:
        """Update user password.
        
        Args:
            user_id: User ID
            new_password: New plain text password
            
        Returns:
            True if user existed and was updated
        """
        password_hash, _ = self._hash_password(new_password)
        
        cursor = self._execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def update_display_name(self, user_id: int, display_name: str) -> bool:
        """Update user display name.
        
        Args:
            user_id: User ID
            display_name: New display name
            
        Returns:
            True if user existed and was updated
        """
        cursor = self._execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            (display_name.strip(), user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, user_id: int) -> bool:
        """Delete user and their sessions.
        
        Args:
            user_id: User ID to delete
            
        Returns:
            True if user existed and was deleted
        """
        # Delete sessions first (foreign key constraint)
        self._execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        
        # Delete user
        cursor = self._execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._commit()
        return cursor.rowcount > 0
    
    def list_all(self) -> list[dict]:
        """List all users.
        
        Returns:
            List of user dicts
        """
        cursor = self._execute(
            "SELECT id, username, display_name, created_at FROM users ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]
    
    def search(self, query: str, exclude_user_id: int | None = None, limit: int = 10) -> list[dict]:
        """Search users by username or display_name.
        
        Args:
            query: Search string
            exclude_user_id: Optional user ID to exclude from results
            limit: Maximum results
            
        Returns:
            List of matching users
        """
        search_pattern = f"%{query.lower()}%"
        
        if exclude_user_id:
            cursor = self._execute(
                """SELECT id, username, display_name 
                   FROM users 
                   WHERE id != ? AND (LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?)
                   ORDER BY display_name
                   LIMIT ?""",
                (exclude_user_id, search_pattern, search_pattern, limit)
            )
        else:
            cursor = self._execute(
                """SELECT id, username, display_name 
                   FROM users 
                   WHERE LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?
                   ORDER BY display_name
                   LIMIT ?""",
                (search_pattern, search_pattern, limit)
            )
        
        return [dict(row) for row in cursor.fetchall()]
    
    def authenticate(self, username: str, password: str) -> dict | None:
        """Authenticate user with username and password.
        
        Args:
            username: Username
            password: Plain text password
            
        Returns:
            User dict if authentication successful, None otherwise
        """
        user = self.get_by_username(username)
        if not user:
            return None
        
        if self._verify_password(password, user["password_hash"], user.get("password_salt", "")):
            return user
        return None
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin.
        
        Args:
            user_id: User ID
            
        Returns:
            True if user is admin
        """
        cursor = self._execute(
            "SELECT is_admin FROM users WHERE id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return bool(row and row["is_admin"])
    
    def set_admin(self, user_id: int, is_admin: bool) -> bool:
        """Set user admin status.
        
        Args:
            user_id: User ID
            is_admin: New admin status
            
        Returns:
            True if user existed
        """
        cursor = self._execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if is_admin else 0, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    # Encryption key management
    
    def get_encryption_keys(self, user_id: int) -> dict | None:
        """Get user's encryption keys (DEK and salt).
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with encrypted_dek, dek_salt, encryption_version or None
        """
        cursor = self._execute(
            """SELECT encrypted_dek, dek_salt, encryption_version, recovery_encrypted_dek 
               FROM user_settings WHERE user_id = ?""",
            (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "encrypted_dek": row["encrypted_dek"],
                "dek_salt": row["dek_salt"],
                "encryption_version": row["encryption_version"],
                "recovery_encrypted_dek": row["recovery_encrypted_dek"]
            }
        return None
    
    def set_encryption_keys(self, user_id: int, encrypted_dek: bytes, dek_salt: bytes) -> bool:
        """Set user's encryption keys.
        
        Args:
            user_id: User ID
            encrypted_dek: Encrypted DEK
            dek_salt: Salt for KEK derivation
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                """INSERT OR REPLACE INTO user_settings 
                   (user_id, encrypted_dek, dek_salt, encryption_version)
                   VALUES (?, ?, ?, 1)""",
                (user_id, encrypted_dek, dek_salt)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_recovery_encrypted_dek(self, user_id: int) -> bytes | None:
        """Get recovery-encrypted DEK.
        
        Args:
            user_id: User ID
            
        Returns:
            Recovery-encrypted DEK or None
        """
        cursor = self._execute(
            "SELECT recovery_encrypted_dek FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["recovery_encrypted_dek"] if row else None
    
    def set_recovery_encrypted_dek(self, user_id: int, recovery_encrypted_dek: bytes) -> bool:
        """Set recovery-encrypted DEK.
        
        Args:
            user_id: User ID
            recovery_encrypted_dek: DEK encrypted with recovery key
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                """INSERT OR REPLACE INTO user_settings 
                   (user_id, recovery_encrypted_dek)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET 
                   recovery_encrypted_dek = excluded.recovery_encrypted_dek""",
                (user_id, recovery_encrypted_dek)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    def get_public_key(self, user_id: int) -> bytes | None:
        """Get user's public key for envelope encryption.
        
        Args:
            user_id: User ID
            
        Returns:
            Public key bytes or None
        """
        cursor = self._execute(
            "SELECT public_key FROM user_public_keys WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["public_key"] if row else None
    
    def set_public_key(self, user_id: int, public_key: bytes) -> bool:
        """Set user's public key for envelope encryption.
        
        Args:
            user_id: User ID
            public_key: Public key bytes
            
        Returns:
            True if successful
        """
        try:
            self._execute(
                """INSERT OR REPLACE INTO user_public_keys 
                   (user_id, public_key, updated_at)
                   VALUES (?, ?, datetime('now'))""",
                (user_id, public_key)
            )
            self._commit()
            return True
        except Exception:
            return False
    
    # Private helper methods
    
    def _hash_password(self, password: str) -> tuple[str, str]:
        """Hash password using bcrypt.
        
        Args:
            password: Plain text password
            
        Returns:
            Tuple of (hash, empty_string) for API compatibility
        """
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        return hashed.decode('utf-8'), ""
    
    def _verify_password(self, password: str, hashed: str, salt: str) -> bool:
        """Verify password against hash.
        
        Supports both bcrypt and legacy SHA-256.
        
        Args:
            password: Plain text password
            hashed: Stored hash
            salt: Stored salt (for legacy)
            
        Returns:
            True if password matches
        """
        # Check if bcrypt hash
        if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        
        # Legacy SHA-256 (for migration)
        if salt:
            import hashlib
            check_hash = hashlib.sha256((salt + password).encode()).hexdigest()
            return check_hash == hashed
        
        return False
