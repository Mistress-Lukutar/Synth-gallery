"""Per-user media encryption service using AES-256-GCM."""
import os
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
