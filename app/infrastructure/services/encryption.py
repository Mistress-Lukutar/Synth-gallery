'''
File:   encryption.py
Brief:  Per-user media encryption service. Chunked AES-256-GCM streaming
        format for arbitrary-size files, plus KEK/recovery-key helpers.
Author: Mistress-Lukutar
Date:   2026-07-21
'''

from __future__ import annotations

import base64
import io
import os
import secrets
import struct
import threading
import time
from typing import BinaryIO, Iterator, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import ENCRYPTION_CHUNK_SIZE
from app.logging_config import get_logger

logger = get_logger(__name__)

# Crypto constants.
PBKDF2_ITERATIONS = 600_000  # OWASP recommendation.
SALT_SIZE = 32               # 256 bits.
DEK_SIZE = 32                # 256 bits for AES-256.
NONCE_SIZE = 12              # 96 bits for GCM.
GCM_TAG_SIZE = 16            # 128-bit authentication tag.
RECOVERY_KEY_SIZE = 32       # 256 bits for recovery key.

# Streaming envelope layout.
# A file on disk is:
#   [MAGIC 4B][VERSION 1B][RESERVED 1B][CHUNK_SIZE 4B BE]
#   for each plaintext chunk:
#     [NONCE 12B][CIPHERTEXT + GCM_TAG (<= CHUNK_SIZE + 16B)]
# The final chunk may be shorter; it still carries its own nonce+tag, so
# each chunk is independently decryptable. This enables O(1) RAM encryption
# and decryption plus HTTP Range serving by seeking to the right chunk.
ENVELOPE_MAGIC = b"SGE1"
ENVELOPE_VERSION = 1
ENVELOPE_HEADER_SIZE = 4 + 1 + 1 + 4  # 10 bytes


class EncryptionError(Exception):
    '''Raised when an encryption/decryption operation fails.'''


def _reader_to_binaryio(source: BinaryIO | bytes) -> BinaryIO:
    '''Wrap raw bytes into a BinaryIO reader.'''
    if isinstance(source, (bytes, bytearray, memoryview)):
        return io.BytesIO(source)
    return source


class EncryptionService:
    '''Handles per-user file encryption (chunked AES-256-GCM) and KEK/DEK
    management.

    The on-disk file envelope is documented at the top of this module.
    Small objects (thumbnails, JXL fallbacks) should use :meth:`encrypt_bytes`
    and :meth:`decrypt_bytes`; large files must use the streaming variants
    :meth:`encrypt_to_stream`, :meth:`decrypt_to_stream`, or
    :meth:`decrypt_range`.
    '''

    # ==================================================================
    # KEK / DEK derivation (unchanged contract).
    # ==================================================================

    @staticmethod
    def derive_kek(password: str, salt: bytes) -> bytes:
        '''Derive a Key Encryption Key from a password using PBKDF2.

        Args:
            password: User password.
            salt: Per-user salt.

        Returns:
            Derived 32-byte KEK.
        '''
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=DEK_SIZE,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def generate_dek() -> bytes:
        '''Generate a random Data Encryption Key.'''
        return os.urandom(DEK_SIZE)

    @staticmethod
    def generate_salt() -> bytes:
        '''Generate a random salt for key derivation.'''
        return os.urandom(SALT_SIZE)

    @staticmethod
    def encrypt_dek(dek: bytes, kek: bytes) -> bytes:
        '''Encrypt a DEK with a KEK using AES-256-GCM.

        The DEK is small (32 bytes), so a single whole-file GCM envelope
        is appropriate here. The output is ``nonce + ciphertext``.
        '''
        aesgcm = AESGCM(kek)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_dek(encrypted_dek: bytes, kek: bytes) -> bytes:
        '''Decrypt a DEK wrapped by :meth:`encrypt_dek`.'''
        nonce = encrypted_dek[:NONCE_SIZE]
        ciphertext = encrypted_dek[NONCE_SIZE:]
        aesgcm = AESGCM(kek)
        return aesgcm.decrypt(nonce, ciphertext, None)

    # ==================================================================
    # Small-object convenience (thumbnails, fallbacks, etc.).
    # Same envelope format as the streaming variant.
    # ==================================================================

    @staticmethod
    def encrypt_bytes(plaintext: bytes, dek: bytes) -> bytes:
        '''Encrypt a small in-memory object using the chunked envelope.

        Args:
            plaintext: Plaintext bytes (typically < a few MiB).
            dek: Data Encryption Key.

        Returns:
            Encrypted envelope as bytes.
        '''
        out = io.BytesIO()
        EncryptionService.encrypt_to_stream(io.BytesIO(plaintext), out, dek)
        return out.getvalue()

    @staticmethod
    def decrypt_bytes(encrypted: bytes, dek: bytes) -> bytes:
        '''Decrypt a chunked envelope produced by :meth:`encrypt_bytes`.

        Args:
            encrypted: Encrypted envelope bytes.
            dek: Data Encryption Key.

        Returns:
            Plaintext bytes.

        Raises:
            EncryptionError: If the envelope is malformed or authentication
                fails.
        '''
        out = io.BytesIO()
        EncryptionService.decrypt_to_stream(io.BytesIO(encrypted), out, dek)
        return out.getvalue()

    # ==================================================================
    # Streaming encrypt/decrypt.
    # ==================================================================

    @staticmethod
    def encrypt_to_stream(
        reader: BinaryIO,
        writer: BinaryIO,
        dek: bytes,
        chunk_size: int = ENCRYPTION_CHUNK_SIZE,
    ) -> None:
        '''Encrypt a binary stream into ``writer`` using the chunked envelope.

        Reads ``chunk_size`` plaintext bytes at a time, never holding the
        whole file in memory.

        Args:
            reader: Source plaintext (file-like, ``rb``).
            writer: Destination for the encrypted envelope (file-like, ``wb``).
            dek: Data Encryption Key.
            chunk_size: Plaintext chunk size in bytes (default 1 MiB).

        Raises:
            EncryptionError: If ``chunk_size`` is invalid.
        '''
        if chunk_size <= 0:
            raise EncryptionError("chunk_size must be positive")

        aesgcm = AESGCM(dek)
        writer.write(ENVELOPE_MAGIC)
        writer.write(bytes([ENVELOPE_VERSION]))
        writer.write(bytes([0]))  # reserved
        writer.write(struct.pack(">I", chunk_size))

        while True:
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            nonce = os.urandom(NONCE_SIZE)
            ciphertext = aesgcm.encrypt(nonce, chunk, None)
            writer.write(nonce)
            writer.write(ciphertext)

    @staticmethod
    def decrypt_to_stream(
        reader: BinaryIO,
        writer: BinaryIO,
        dek: bytes,
    ) -> None:
        '''Decrypt a chunked envelope into ``writer``.

        Args:
            reader: Source envelope (file-like, ``rb``).
            writer: Destination plaintext (file-like, ``wb``).
            dek: Data Encryption Key.

        Raises:
            EncryptionError: If the envelope is malformed or authentication
                fails on any chunk.
        '''
        for chunk in EncryptionService.iter_decrypt(reader, dek):
            writer.write(chunk)

    @staticmethod
    def parse_envelope_header(head: bytes) -> int:
        '''Validate a raw envelope header.

        Args:
            head: The first ``ENVELOPE_HEADER_SIZE`` bytes read from a
                stored envelope.

        Returns:
            The plaintext chunk size declared by the envelope.

        Raises:
            EncryptionError: If the header is truncated, has a bad magic
                value, an unsupported version or an invalid chunk size.
        '''
        if len(head) < ENVELOPE_HEADER_SIZE:
            raise EncryptionError("truncated envelope header")
        if head[:4] != ENVELOPE_MAGIC:
            raise EncryptionError("invalid envelope magic")
        version = head[4]
        if version != ENVELOPE_VERSION:
            raise EncryptionError(f"unsupported envelope version {version}")
        chunk_size = struct.unpack(">I", head[6:10])[0]
        if chunk_size <= 0:
            raise EncryptionError("invalid chunk size in envelope")
        return chunk_size

    @staticmethod
    def iter_decrypt(reader: BinaryIO, dek: bytes) -> Iterator[bytes]:
        '''Lazily yield plaintext chunks from a chunked envelope.

        Only one chunk's worth of plaintext lives in memory at a time, so
        this is suitable for streaming arbitrary-size files. Used by
        :class:`fastapi.responses.StreamingResponse`.

        Args:
            reader: Source envelope (file-like, ``rb``).
            dek: Data Encryption Key.

        Yields:
            Plaintext byte chunks (one per encrypted chunk).

        Raises:
            EncryptionError: If the envelope is malformed or authentication
                fails on any chunk.
        '''
        header = reader.read(ENVELOPE_HEADER_SIZE)
        chunk_size = EncryptionService.parse_envelope_header(header)

        aesgcm = AESGCM(dek)
        cipher_chunk_size = NONCE_SIZE + chunk_size + GCM_TAG_SIZE

        while True:
            block = reader.read(cipher_chunk_size)
            if not block:
                break
            if len(block) < NONCE_SIZE + GCM_TAG_SIZE:
                raise EncryptionError("truncated chunk")
            nonce = block[:NONCE_SIZE]
            ciphertext = block[NONCE_SIZE:]
            try:
                plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            except Exception as exc:
                raise EncryptionError("chunk authentication failed") from exc
            yield plaintext

    @staticmethod
    def decrypt_range(
        reader: BinaryIO,
        dek: bytes,
        start: int,
        end: int,
    ) -> Iterator[bytes]:
        '''Yield plaintext bytes for ``[start, end]`` (inclusive HTTP Range).

        Only chunks that overlap the requested range are decrypted; other
        chunks are skipped via ``seek``. The reader must be seekable.

        Args:
            reader: Source envelope (file-like, ``rb``), must support
                ``seek`` and ``tell``.
            dek: Data Encryption Key.
            start: Inclusive start byte offset in plaintext.
            end: Inclusive end byte offset in plaintext.

        Yields:
            Plaintext byte slices covering exactly ``[start, end]``.

        Raises:
            EncryptionError: If the envelope is malformed or authentication
                fails.
            ValueError: If the range is invalid.
        '''
        if start < 0 or end < start:
            raise ValueError(f"invalid range [{start}, {end}]")

        reader.seek(0, io.SEEK_SET)
        header = reader.read(ENVELOPE_HEADER_SIZE)
        chunk_size = EncryptionService.parse_envelope_header(header)

        aesgcm = AESGCM(dek)
        cipher_chunk_size = NONCE_SIZE + chunk_size + GCM_TAG_SIZE

        first_chunk = start // chunk_size
        last_chunk = end // chunk_size

        # Seek past earlier chunks in the ciphertext stream.
        reader.seek(
            ENVELOPE_HEADER_SIZE + first_chunk * cipher_chunk_size, io.SEEK_SET
        )

        for chunk_idx in range(first_chunk, last_chunk + 1):
            block = reader.read(cipher_chunk_size)
            if not block:
                break
            if len(block) < NONCE_SIZE + GCM_TAG_SIZE:
                raise EncryptionError("truncated chunk")
            nonce = block[:NONCE_SIZE]
            ciphertext = block[NONCE_SIZE:]
            try:
                plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            except Exception as exc:
                raise EncryptionError("chunk authentication failed") from exc

            chunk_start = chunk_idx * chunk_size
            slice_start = max(0, start - chunk_start)
            slice_end = min(len(plaintext), end - chunk_start + 1)
            if slice_start >= slice_end:
                continue
            yield plaintext[slice_start:slice_end]

    @staticmethod
    def get_plaintext_size(
        encrypted_size: int,
        chunk_size: int = ENCRYPTION_CHUNK_SIZE,
    ) -> int:
        '''Compute plaintext byte length from the encrypted envelope size.

        Args:
            encrypted_size: Total size of the encrypted envelope in bytes.
            chunk_size: Plaintext chunk size in bytes.

        Returns:
            Plaintext length in bytes. Raises ``EncryptionError`` if the
            size is inconsistent with the envelope layout.
        '''
        if encrypted_size < ENVELOPE_HEADER_SIZE:
            raise EncryptionError("file smaller than envelope header")
        body_size = encrypted_size - ENVELOPE_HEADER_SIZE
        cipher_chunk_size = NONCE_SIZE + chunk_size + GCM_TAG_SIZE
        full_chunks, remainder = divmod(body_size, cipher_chunk_size)
        if remainder == 0:
            plaintext_from_full = full_chunks * chunk_size
            return plaintext_from_full
        if remainder < NONCE_SIZE + GCM_TAG_SIZE:
            raise EncryptionError("inconsistent envelope size")
        plaintext_from_full = full_chunks * chunk_size
        plaintext_from_last = remainder - NONCE_SIZE - GCM_TAG_SIZE
        return plaintext_from_full + plaintext_from_last

    # ==================================================================
    # Recovery key methods (unchanged contract).
    # ==================================================================

    @staticmethod
    def generate_recovery_key() -> tuple[str, bytes]:
        '''Generate a new recovery key.

        Returns:
            Tuple of (human_readable_key, raw_key_bytes). The human-readable
            key is base32 (A-Z, 2-7) split into 4-char groups.
        '''
        raw_key = secrets.token_bytes(RECOVERY_KEY_SIZE)
        b32_key = base64.b32encode(raw_key).decode("ascii")
        formatted_key = "-".join(
            [b32_key[i : i + 4] for i in range(0, len(b32_key), 4)]
        )
        return formatted_key, raw_key

    @staticmethod
    def parse_recovery_key(formatted_key: str) -> bytes:
        '''Parse a human-readable recovery key back to bytes.

        Args:
            formatted_key: Key as shown to the user (with dashes).

        Returns:
            Raw key bytes.
        '''
        b32_key = formatted_key.replace("-", "").upper()
        return base64.b32decode(b32_key)

    @staticmethod
    def encrypt_dek_with_recovery_key(dek: bytes, recovery_key: bytes) -> bytes:
        '''Encrypt a DEK with a recovery key for backup purposes.'''
        aesgcm = AESGCM(recovery_key)
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, dek, None)
        return nonce + ciphertext

    @staticmethod
    def decrypt_dek_with_recovery_key(encrypted_dek: bytes, recovery_key: bytes) -> bytes:
        '''Decrypt a DEK wrapped by :meth:`encrypt_dek_with_recovery_key`.'''
        nonce = encrypted_dek[:NONCE_SIZE]
        ciphertext = encrypted_dek[NONCE_SIZE:]
        aesgcm = AESGCM(recovery_key)
        return aesgcm.decrypt(nonce, ciphertext, None)


class DEKCache:
    '''Thread-safe cache for decrypted DEKs during a session.'''

    def __init__(self) -> None:
        self._cache: dict[int, tuple[bytes, float]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: int) -> Optional[bytes]:
        '''Get a cached DEK for the user, or ``None`` if absent/expired.'''
        with self._lock:
            entry = self._cache.get(user_id)
            if entry:
                dek, expires_at = entry
                if time.time() < expires_at:
                    return dek
                del self._cache[user_id]
        return None

    def set(
        self, user_id: int, dek: bytes, ttl_seconds: float = 7 * 24 * 3600
    ) -> None:
        '''Cache a DEK with the given TTL (default 7 days, matching session).'''
        with self._lock:
            expires_at = time.time() + ttl_seconds
            self._cache[user_id] = (dek, expires_at)

    def invalidate(self, user_id: int) -> None:
        '''Remove a cached DEK (on logout or password change).'''
        with self._lock:
            self._cache.pop(user_id, None)

    def clear_expired(self) -> None:
        '''Drop all expired entries.'''
        now = time.time()
        with self._lock:
            expired = [uid for uid, (_, exp) in self._cache.items() if now >= exp]
            for uid in expired:
                del self._cache[uid]


# Global cache instance.
dek_cache = DEKCache()
