"""Authentication service - handles login/logout and session management."""
from typing import Optional, Tuple

from fastapi import HTTPException

from ...infrastructure.repositories import UserRepository, SessionRepository
from ...services.encryption import EncryptionService, dek_cache


class AuthService:
    """Service for authentication operations.
    
    Responsibilities:
    - User authentication
    - Session management
    - Encryption key handling during login
    """
    
    def __init__(
        self,
        user_repository: UserRepository,
        session_repository: SessionRepository
    ):
        self.user_repo = user_repository
        self.session_repo = session_repository
    
    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """Authenticate user with username and password.
        
        Args:
            username: Username
            password: Password
            
        Returns:
            User dict if authenticated, None otherwise
        """
        return self.user_repo.authenticate(username, password)
    
    def create_session(self, user_id: int, expires_hours: int = 24 * 7) -> str:
        """Create new session for user.
        
        Args:
            user_id: User ID
            expires_hours: Session lifetime
            
        Returns:
            Session ID
        """
        return self.session_repo.create(user_id, expires_hours)
    
    def get_session(self, session_id: str) -> Optional[dict]:
        """Get valid session by ID.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session dict if valid, None otherwise
        """
        return self.session_repo.get_valid(session_id)
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session (logout).
        
        Args:
            session_id: Session ID
            
        Returns:
            True if deleted
        """
        return self.session_repo.delete(session_id)
    
    def get_encryption_keys(self, user_id: int) -> Optional[dict]:
        """Get user's encryption keys.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with encrypted_dek, dek_salt, etc. or None
        """
        db = self.user_repo._conn
        
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
    
    def setup_encryption(
        self,
        user_id: int,
        password: str
    ) -> Tuple[bytes, bytes]:
        """Set up encryption for new user.
        
        Args:
            user_id: User ID
            password: Password
            
        Returns:
            Tuple of (dek, salt)
        """
        dek = EncryptionService.generate_dek()
        salt = EncryptionService.generate_salt()
        kek = EncryptionService.derive_kek(password, salt)
        encrypted_dek = EncryptionService.encrypt_dek(dek, kek)
        
        # Store in database
        db = self.user_repo._conn
        
        existing = db.execute(
            "SELECT user_id FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        
        if existing:
            db.execute(
                """UPDATE user_settings 
                   SET encrypted_dek = ?, dek_salt = ?, encryption_version = 1
                   WHERE user_id = ?""",
                (encrypted_dek, salt, user_id)
            )
        else:
            db.execute(
                """INSERT INTO user_settings 
                   (user_id, encrypted_dek, dek_salt, encryption_version)
                   VALUES (?, ?, ?, 1)""",
                (user_id, encrypted_dek, salt)
            )
        
        db.commit()
        
        # Cache DEK
        dek_cache.set(user_id, dek)
        
        return dek, salt
    
    def decrypt_and_cache_dek(
        self,
        user_id: int,
        password: str,
        ttl_seconds: int = 7 * 24 * 3600
    ) -> Optional[bytes]:
        """Decrypt DEK with password and cache it.
        
        Args:
            user_id: User ID
            password: Password
            ttl_seconds: Cache TTL
            
        Returns:
            DEK bytes or None if failed
        """
        enc_keys = self.get_encryption_keys(user_id)
        
        if not enc_keys:
            return None
        
        try:
            kek = EncryptionService.derive_kek(password, enc_keys["dek_salt"])
            dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], kek)
            dek_cache.set(user_id, dek, ttl_seconds=ttl_seconds)
            return dek
        except Exception:
            return None
    
    def is_dek_cached(self, user_id: int) -> bool:
        """Check if DEK is cached for user.
        
        Args:
            user_id: User ID
            
        Returns:
            True if DEK is in cache
        """
        return dek_cache.get(user_id) is not None
