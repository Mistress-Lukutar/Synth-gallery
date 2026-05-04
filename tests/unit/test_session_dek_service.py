"""Unit tests for SessionDEKService."""
import pytest

from app.infrastructure.services.session_dek import SessionDEKService


class TestSessionDEKService:
    """Test suite for session-based DEK encryption."""

    def test_derive_key_deterministic(self):
        """Same session_id should always produce same key."""
        session_id = "test-session-id-12345"
        
        key1 = SessionDEKService._derive_key(session_id)
        key2 = SessionDEKService._derive_key(session_id)
        
        assert key1 == key2
        assert len(key1) == 32  # 256 bits

    def test_derive_key_different_sessions(self):
        """Different session_ids should produce different keys."""
        key1 = SessionDEKService._derive_key("session-1")
        key2 = SessionDEKService._derive_key("session-2")
        
        assert key1 != key2

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt and decrypt should return original DEK."""
        session_id = "test-session-abc123"
        original_dek = b"x" * 32  # 32 bytes DEK
        
        encrypted = SessionDEKService.encrypt_dek(original_dek, session_id)
        decrypted = SessionDEKService.decrypt_dek(encrypted, session_id)
        
        assert decrypted == original_dek

    def test_decrypt_with_wrong_session_fails(self):
        """Decrypting with wrong session_id should fail."""
        session_id = "correct-session"
        wrong_session = "wrong-session"
        original_dek = b"x" * 32
        
        encrypted = SessionDEKService.encrypt_dek(original_dek, session_id)
        
        with pytest.raises(Exception):
            SessionDEKService.decrypt_dek(encrypted, wrong_session)

    def test_decrypt_with_tampered_data_fails(self):
        """Decrypting tampered data should fail."""
        session_id = "test-session"
        original_dek = b"x" * 32
        
        encrypted = SessionDEKService.encrypt_dek(original_dek, session_id)
        
        # Tamper with encrypted data
        tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 0xFF])
        
        with pytest.raises(Exception):
            SessionDEKService.decrypt_dek(tampered, session_id)

    def test_different_deks_different_ciphertexts(self):
        """Same session, different DEKs should produce different ciphertexts."""
        session_id = "test-session"
        dek1 = b"a" * 32
        dek2 = b"b" * 32
        
        encrypted1 = SessionDEKService.encrypt_dek(dek1, session_id)
        encrypted2 = SessionDEKService.encrypt_dek(dek2, session_id)
        
        assert encrypted1 != encrypted2

    def test_real_random_dek(self):
        """Test with cryptographically random DEK."""
        import os
        
        session_id = "test-session-random"
        original_dek = os.urandom(32)
        
        encrypted = SessionDEKService.encrypt_dek(original_dek, session_id)
        decrypted = SessionDEKService.decrypt_dek(encrypted, session_id)
        
        assert decrypted == original_dek

    def test_decrypt_legacy_pbkdf2_session(self):
        """Decrypting a session created with legacy PBKDF2 should still work."""
        session_id = "legacy-session-123"
        original_dek = b"y" * 32

        # Encrypt with legacy PBKDF2 key derivation
        legacy_key = SessionDEKService._derive_key_legacy(session_id)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os
        aesgcm = AESGCM(legacy_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, original_dek, None)
        encrypted_legacy = nonce + ciphertext

        # Decrypt should fall back to PBKDF2 and succeed
        decrypted = SessionDEKService.decrypt_dek(encrypted_legacy, session_id)
        assert decrypted == original_dek
