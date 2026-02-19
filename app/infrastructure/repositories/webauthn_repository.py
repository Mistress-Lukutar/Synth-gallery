"""WebAuthn repository - handles hardware key credential storage.

This repository manages WebAuthn credentials for passwordless authentication.
"""
from .base import Repository


class WebAuthnRepository(Repository):
    """Repository for WebAuthn credential management.
    
    Hardware keys (YubiKey, etc.) store credentials for passwordless auth.
    
    Examples:
        >>> repo = WebAuthnRepository(db)
        >>> cred_id = repo.create(1, credential_id=b'...', public_key=b'...', name="YubiKey")
        >>> creds = repo.get_for_user(1)
        >>> repo.update_sign_count(credential_id=b'...', new_count=5)
    """
    
    def create(
        self, 
        user_id: int, 
        credential_id: bytes, 
        public_key: bytes, 
        name: str,
        encrypted_dek: bytes | None = None
    ) -> int:
        """Add a new WebAuthn credential for a user.
        
        Args:
            user_id: User ID to associate credential with
            credential_id: Raw credential ID from WebAuthn
            public_key: Raw public key bytes
            name: Human-readable name for this key
            encrypted_dek: Optional encrypted DEK for this credential
            
        Returns:
            Database ID of the created credential
        """
        cursor = self._execute(
            """INSERT INTO webauthn_credentials 
               (user_id, credential_id, public_key, name, encrypted_dek, sign_count)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (user_id, credential_id, public_key, name, encrypted_dek)
        )
        self._commit()
        return cursor.lastrowid
    
    def get_for_user(self, user_id: int) -> list[dict]:
        """Get all WebAuthn credentials for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of credential dicts with user info
        """
        cursor = self._execute(
            """SELECT wc.*, u.username, u.display_name 
               FROM webauthn_credentials wc
               JOIN users u ON wc.user_id = u.id
               WHERE wc.user_id = ?
               ORDER BY wc.created_at DESC""",
            (user_id,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]
    
    def get_by_credential_id(self, credential_id: bytes) -> dict | None:
        """Get a WebAuthn credential by its credential_id.
        
        Args:
            credential_id: Raw credential ID bytes
            
        Returns:
            Credential dict with user info, or None if not found
        """
        cursor = self._execute(
            """SELECT wc.*, u.username, u.display_name 
               FROM webauthn_credentials wc
               JOIN users u ON wc.user_id = u.id
               WHERE wc.credential_id = ?""",
            (credential_id,)
        )
        return self._row_to_dict(cursor.fetchone())
    
    def get_credential_ids_for_user(self, user_id: int) -> list[bytes]:
        """Get all credential IDs for a user (for WebAuthn allowCredentials).
        
        Args:
            user_id: User ID
            
        Returns:
            List of raw credential ID bytes
        """
        cursor = self._execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id = ?",
            (user_id,)
        )
        return [row["credential_id"] for row in cursor.fetchall()]
    
    def get_all_credential_ids(self) -> list[bytes]:
        """Get all credential IDs in the system.
        
        Used for discoverable credentials (passwordless without username).
        
        Returns:
            List of raw credential ID bytes
        """
        cursor = self._execute(
            "SELECT credential_id FROM webauthn_credentials"
        )
        return [row["credential_id"] for row in cursor.fetchall()]
    
    def update_sign_count(self, credential_id: bytes, new_sign_count: int) -> bool:
        """Update the sign count for a credential (anti-replay protection).
        
        Args:
            credential_id: Raw credential ID bytes
            new_sign_count: New sign count value
            
        Returns:
            True if credential was found and updated
        """
        cursor = self._execute(
            "UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?",
            (new_sign_count, credential_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, credential_db_id: int, user_id: int) -> bool:
        """Delete a WebAuthn credential by its database ID.
        
        Args:
            credential_db_id: Database ID (not credential_id bytes)
            user_id: User ID (for verification)
            
        Returns:
            True if credential existed and was deleted
        """
        cursor = self._execute(
            "DELETE FROM webauthn_credentials WHERE id = ? AND user_id = ?",
            (credential_db_id, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def rename(self, credential_db_id: int, user_id: int, new_name: str) -> bool:
        """Rename a WebAuthn credential.
        
        Args:
            credential_db_id: Database ID
            user_id: User ID (for verification)
            new_name: New display name
            
        Returns:
            True if credential was found and renamed
        """
        cursor = self._execute(
            "UPDATE webauthn_credentials SET name = ? WHERE id = ? AND user_id = ?",
            (new_name, credential_db_id, user_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def user_has_credentials(self, user_id: int) -> bool:
        """Check if user has any registered WebAuthn credentials.
        
        Args:
            user_id: User ID
            
        Returns:
            True if user has at least one credential
        """
        cursor = self._execute(
            "SELECT 1 FROM webauthn_credentials WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        return cursor.fetchone() is not None
    
    def count_for_user(self, user_id: int) -> int:
        """Count credentials for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of credentials
        """
        cursor = self._execute(
            "SELECT COUNT(*) as count FROM webauthn_credentials WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["count"] if row else 0
