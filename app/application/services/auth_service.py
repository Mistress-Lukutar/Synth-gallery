"""Authentication service - handles login/logout and session management."""
from typing import Optional, Tuple

from ...infrastructure.repositories import UserRepository, SessionRepository
from ...infrastructure.services.encryption import EncryptionService, dek_cache
from ...infrastructure.services.session_dek import SessionDEKService


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
        return self.session_repo.create(user_id, expires_hours, encrypted_dek=None)
    
    def store_dek_in_session(self, session_id: str, dek: bytes) -> bool:
        """Encrypt DEK with session key and store in database.
        
        This allows DEK to persist across server restarts and work
        with multiple workers (Gunicorn).
        
        Args:
            session_id: Session ID to encrypt DEK with
            dek: Raw DEK bytes
            
        Returns:
            True if stored successfully
        """
        encrypted_dek = SessionDEKService.encrypt_dek(dek, session_id)
        return self.session_repo.set_encrypted_dek(session_id, encrypted_dek)
    
    def get_dek_from_session(self, session_id: str) -> Optional[bytes]:
        """Get DEK from session storage.
        
        Retrieves encrypted DEK from database and decrypts it
        using session-derived key.
        
        Args:
            session_id: Session ID
            
        Returns:
            Raw DEK bytes or None if not found/invalid
        """
        encrypted_dek = self.session_repo.get_encrypted_dek(session_id)
        if not encrypted_dek:
            return None
        
        try:
            return SessionDEKService.decrypt_dek(encrypted_dek, session_id)
        except Exception:
            # Decryption failed (invalid session_id or corrupted data)
            return None
    
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
        keys = self.user_repo.get_encryption_keys(user_id)
        if not keys:
            return None
        
        return {
            "encrypted_dek": keys["encrypted_dek"],
            "dek_salt": keys["dek_salt"],
            "encryption_version": keys.get("encryption_version", 1),
            "recovery_encrypted_dek": keys.get("recovery_encrypted_dek")
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
        self.user_repo.save_encryption_keys(user_id, encrypted_dek, salt)
        
        # Cache DEK
        dek_cache.set(user_id, dek)
        
        return dek, salt
    
    def decrypt_and_cache_dek(
        self,
        user_id: int,
        password: str,
        session_id: str | None = None,
        ttl_seconds: int = 7 * 24 * 3600
    ) -> Optional[bytes]:
        """Decrypt DEK with password and cache it.
        
        If session_id is provided, also stores encrypted DEK in session
        for persistence across restarts.
        
        Args:
            user_id: User ID
            password: Password
            session_id: Optional session ID for DB storage
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
            
            # Cache in memory (legacy, for backward compatibility)
            dek_cache.set(user_id, dek, ttl_seconds=ttl_seconds)
            
            # Also store in session for persistence (Issue #18)
            if session_id:
                self.store_dek_in_session(session_id, dek)
            
            return dek
        except Exception:
            return None
    
    def is_dek_cached(self, user_id: int, session_id: str | None = None) -> bool:
        """Check if DEK is cached for user.
        
        First checks memory cache, then falls back to session storage.
        
        Args:
            user_id: User ID
            session_id: Optional session ID for DB storage check
            
        Returns:
            True if DEK is available
        """
        # Check memory cache first
        if dek_cache.get(user_id) is not None:
            return True
        
        # Fall back to session storage
        if session_id:
            return self.get_dek_from_session(session_id) is not None
        
        return False
