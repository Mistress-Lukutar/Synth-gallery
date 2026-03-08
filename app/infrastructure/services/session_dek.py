"""Session-based DEK encryption service.

This service allows storing DEKs in the database encrypted with a key
derived from the session ID. This provides:
- Persistent DEK storage across server restarts
- Multi-worker compatibility (Gunicorn)
- Remote session invalidation capability
- No plaintext DEK storage

Security model:
- DEK is encrypted with AES-256-GCM using a key derived from session_id
- session_id is only transmitted in HTTP-only cookies (never in response body)
- Even with full database access, DEK cannot be decrypted without session_id
- Session invalidation removes encrypted DEK from database
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Constants
PBKDF2_ITERATIONS = 100_000  # Lower than main KEK since session_id is already random
SALT_SIZE = 32
NONCE_SIZE = 12


class SessionDEKService:
    """Service for encrypting/decrypting DEKs with session-based keys."""

    @staticmethod
    def _derive_key(session_id: str) -> bytes:
        """Derive encryption key from session_id using PBKDF2.
        
        Args:
            session_id: Random session ID from cookie
            
        Returns:
            256-bit key derived from session_id
        """
        # Use session_id itself as "salt" for deterministic key derivation
        # This ensures same session_id always produces same key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 256 bits for AES-256
            salt=session_id.encode('utf-8'),  # session_id is random, acts as salt
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(session_id.encode('utf-8'))

    @staticmethod
    def encrypt_dek(dek: bytes, session_id: str) -> bytes:
        """Encrypt DEK with session-derived key.
        
        Args:
            dek: Raw DEK bytes (32 bytes)
            session_id: Session ID to derive encryption key from
            
        Returns:
            Encrypted DEK (nonce + ciphertext)
        """
        key = SessionDEKService._derive_key(session_id)
        aesgcm = AESGCM(key)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_dek(encrypted_dek: bytes, session_id: str) -> bytes:
        """Decrypt DEK with session-derived key.
        
        Args:
            encrypted_dek: Encrypted DEK (nonce + ciphertext)
            session_id: Session ID to derive decryption key from
            
        Returns:
            Raw DEK bytes
        """
        key = SessionDEKService._derive_key(session_id)
        aesgcm = AESGCM(key)
        nonce = encrypted_dek[:NONCE_SIZE]
        ciphertext = encrypted_dek[NONCE_SIZE:]
        return aesgcm.decrypt(nonce, ciphertext, None)
