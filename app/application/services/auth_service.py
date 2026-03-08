"""Authentication service - handles login/logout and session management."""
from typing import Optional, Tuple

import secrets
from fastapi import HTTPException

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
    
    def create_session(self, user_id: int, expires_hours: int = 24 * 7, fingerprint: str | None = None) -> str:
        """Create new session for user.
        
        Args:
            user_id: User ID
            expires_hours: Session lifetime
            fingerprint: Optional browser fingerprint for session validation
            
        Returns:
            Session ID
        """
        return self.session_repo.create(user_id, expires_hours, encrypted_dek=None, fingerprint=fingerprint)
    
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
    
    # =========================================================================
    # Recovery Key Authentication
    # =========================================================================
    
    def authenticate_with_recovery_key(
        self,
        username: str,
        recovery_key: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """Authenticate user with recovery key.
        
        Args:
            username: Username
            recovery_key: Recovery key (formatted with dashes)
            
        Returns:
            Tuple of (user dict, reset token) if successful, (None, None) otherwise
        """
        # Get user by username
        user = self.user_repo.get_by_username(username)
        if not user:
            return None, None
        
        # Get encryption keys
        enc_keys = self.get_encryption_keys(user["id"])
        if not enc_keys or not enc_keys.get("recovery_encrypted_dek"):
            return None, None
        
        # Parse recovery key
        try:
            raw_recovery_key = EncryptionService.parse_recovery_key(recovery_key)
        except Exception:
            return None, None
        
        # Decrypt DEK with recovery key
        try:
            dek = EncryptionService.decrypt_dek_with_recovery_key(
                enc_keys["recovery_encrypted_dek"],
                raw_recovery_key
            )
        except Exception:
            return None, None
        
        # Generate password reset token
        reset_token = secrets.token_urlsafe(32)
        
        # Store reset token and DEK temporarily
        # We'll store DEK in cache with the reset token as key
        dek_cache.set(f"reset_{reset_token}", dek, ttl_seconds=3600)  # 1 hour
        dek_cache.set(f"reset_user_{reset_token}", user["id"], ttl_seconds=3600)
        
        return user, reset_token
    
    def validate_reset_token(self, reset_token: str) -> Optional[int]:
        """Validate password reset token and return user ID.
        
        Args:
            reset_token: Reset token from recovery login
            
        Returns:
            User ID if valid, None otherwise
        """
        user_id = dek_cache.get(f"reset_user_{reset_token}")
        return user_id
    
    def complete_password_reset(
        self,
        reset_token: str,
        new_password: str,
        fingerprint: str | None = None
    ) -> tuple[Optional[dict], Optional[str]]:
        """Complete password reset after recovery login.
        
        Args:
            reset_token: Reset token from recovery login
            new_password: New password
            fingerprint: Optional browser fingerprint
            
        Returns:
            Tuple of (user dict, session_id) if successful
        """
        # Validate token
        user_id = self.validate_reset_token(reset_token)
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")
        
        # Get DEK from cache
        dek = dek_cache.get(f"reset_{reset_token}")
        if not dek:
            raise HTTPException(status_code=400, detail="Session expired. Please use recovery key again.")
        
        # Update password
        self.user_repo.update_password(user_id, new_password)
        
        # Re-encrypt DEK with new password
        salt = EncryptionService.generate_salt()
        kek = EncryptionService.derive_kek(new_password, salt)
        encrypted_dek = EncryptionService.encrypt_dek(dek, kek)
        self.user_repo.save_encryption_keys(user_id, encrypted_dek, salt)
        
        # Create session
        session_id = self.create_session(user_id, fingerprint=fingerprint)
        
        # Store DEK in session
        self.store_dek_in_session(session_id, dek)
        
        # Cache DEK
        dek_cache.set(user_id, dek)
        
        # Clean up reset tokens
        dek_cache.invalidate(f"reset_{reset_token}")
        dek_cache.invalidate(f"reset_user_{reset_token}")
        
        # Return user info
        user = self.user_repo.get_by_id(user_id)
        return user, session_id
