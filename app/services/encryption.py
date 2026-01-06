"""Per-user media encryption service using AES-256-GCM."""
import base64
import os
import secrets
import time
import threading
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Constants
PBKDF2_ITERATIONS = 600_000  # OWASP recommendation
SALT_SIZE = 32   # 256 bits
DEK_SIZE = 32    # 256 bits for AES-256
NONCE_SIZE = 12  # 96 bits for GCM
RECOVERY_KEY_SIZE = 32  # 256 bits for recovery key


class EncryptionService:
    """Handles per-user file encryption/decryption."""

    @staticmethod
    def derive_kek(password: str, salt: bytes) -> bytes:
        """Derive Key Encryption Key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=DEK_SIZE,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password.encode('utf-8'))

    @staticmethod
    def generate_dek() -> bytes:
        """Generate a random Data Encryption Key."""
        return os.urandom(DEK_SIZE)

    @staticmethod
    def generate_salt() -> bytes:
        """Generate a random salt for key derivation."""
        return os.urandom(SALT_SIZE)

    @staticmethod
    def encrypt_dek(dek: bytes, kek: bytes) -> bytes:
        """Encrypt DEK with KEK using AES-256-GCM."""
        aesgcm = AESGCM(kek)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_dek(encrypted_dek: bytes, kek: bytes) -> bytes:
        """Decrypt DEK with KEK."""
        nonce = encrypted_dek[:NONCE_SIZE]
        ciphertext = encrypted_dek[NONCE_SIZE:]
        aesgcm = AESGCM(kek)
        return aesgcm.decrypt(nonce, ciphertext, None)

    @staticmethod
    def encrypt_file(plaintext: bytes, dek: bytes) -> bytes:
        """Encrypt file data. Returns nonce + ciphertext."""
        aesgcm = AESGCM(dek)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_file(encrypted_data: bytes, dek: bytes) -> bytes:
        """Decrypt file data."""
        nonce = encrypted_data[:NONCE_SIZE]
        ciphertext = encrypted_data[NONCE_SIZE:]
        aesgcm = AESGCM(dek)
        return aesgcm.decrypt(nonce, ciphertext, None)

    # Recovery Key methods
    @staticmethod
    def generate_recovery_key() -> tuple[str, bytes]:
        """
        Generate a new recovery key.

        Returns:
            Tuple of (human_readable_key, raw_key_bytes)
            The human_readable_key is what's shown to the user (base64).
        """
        raw_key = secrets.token_bytes(RECOVERY_KEY_SIZE)
        # Format as base64 for human readability, with dashes for easier reading
        b64_key = base64.urlsafe_b64encode(raw_key).decode('ascii').rstrip('=')
        # Split into groups of 8 for readability: XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX
        formatted_key = '-'.join([b64_key[i:i+8] for i in range(0, len(b64_key), 8)])
        return formatted_key, raw_key

    @staticmethod
    def parse_recovery_key(formatted_key: str) -> bytes:
        """
        Parse a human-readable recovery key back to bytes.

        Args:
            formatted_key: The key as shown to user (with dashes)

        Returns:
            Raw key bytes
        """
        # Remove dashes and restore base64 padding
        b64_key = formatted_key.replace('-', '')
        # Add padding back
        padding = 4 - len(b64_key) % 4
        if padding != 4:
            b64_key += '=' * padding
        return base64.urlsafe_b64decode(b64_key)

    @staticmethod
    def encrypt_dek_with_recovery_key(dek: bytes, recovery_key: bytes) -> bytes:
        """Encrypt DEK with recovery key for backup purposes."""
        aesgcm = AESGCM(recovery_key)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_dek_with_recovery_key(encrypted_dek: bytes, recovery_key: bytes) -> bytes:
        """Decrypt DEK using recovery key."""
        nonce = encrypted_dek[:NONCE_SIZE]
        ciphertext = encrypted_dek[NONCE_SIZE:]
        aesgcm = AESGCM(recovery_key)
        return aesgcm.decrypt(nonce, ciphertext, None)


class DEKCache:
    """Thread-safe cache for decrypted DEKs during session."""

    def __init__(self):
        self._cache: dict[int, tuple[bytes, float]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: int) -> Optional[bytes]:
        """Get cached DEK for user."""
        with self._lock:
            entry = self._cache.get(user_id)
            if entry:
                dek, expires_at = entry
                if time.time() < expires_at:
                    return dek
                else:
                    del self._cache[user_id]
        return None

    def set(self, user_id: int, dek: bytes, ttl_seconds: int = 7 * 24 * 3600):
        """Cache DEK with TTL (default 7 days to match session)."""
        with self._lock:
            expires_at = time.time() + ttl_seconds
            self._cache[user_id] = (dek, expires_at)

    def invalidate(self, user_id: int):
        """Remove DEK from cache (on logout/password change)."""
        with self._lock:
            self._cache.pop(user_id, None)

    def clear_expired(self):
        """Remove all expired entries."""
        now = time.time()
        with self._lock:
            expired = [uid for uid, (_, exp) in self._cache.items() if now >= exp]
            for uid in expired:
                del self._cache[uid]


# Global cache instance
dek_cache = DEKCache()
