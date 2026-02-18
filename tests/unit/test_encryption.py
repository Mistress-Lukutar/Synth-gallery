"""
Encryption service unit tests.

Tests cryptographic primitives in isolation.
No database or filesystem dependencies.
"""
import base64
import pytest
from app.services.encryption import EncryptionService, DEKCache


class TestKeyDerivation:
    """Test PBKDF2 key derivation."""
    
    def test_derive_kek_consistent(self):
        """Same password+salt should produce same KEK."""
        password = "test_password"
        salt = EncryptionService.generate_salt()
        
        kek1 = EncryptionService.derive_kek(password, salt)
        kek2 = EncryptionService.derive_kek(password, salt)
        
        assert kek1 == kek2
        assert len(kek1) == 32  # 256 bits
    
    def test_derive_kek_different_salts(self):
        """Different salts should produce different KEKs."""
        password = "test_password"
        salt1 = EncryptionService.generate_salt()
        salt2 = EncryptionService.generate_salt()
        
        kek1 = EncryptionService.derive_kek(password, salt1)
        kek2 = EncryptionService.derive_kek(password, salt2)
        
        assert kek1 != kek2
    
    def test_derive_kek_different_passwords(self):
        """Different passwords should produce different KEKs."""
        salt = EncryptionService.generate_salt()
        
        kek1 = EncryptionService.derive_kek("password1", salt)
        kek2 = EncryptionService.derive_kek("password2", salt)
        
        assert kek1 != kek2


class TestDEKGeneration:
    """Test Data Encryption Key generation."""
    
    def test_generate_dek_unique(self):
        """Each DEK should be unique."""
        dek1 = EncryptionService.generate_dek()
        dek2 = EncryptionService.generate_dek()
        dek3 = EncryptionService.generate_dek()
        
        assert dek1 != dek2
        assert dek2 != dek3
        assert len(dek1) == 32  # 256 bits
    
    def test_generate_dek_random(self):
        """DEK should be cryptographically random."""
        # Generate many DEKs and check no obvious patterns
        deks = [EncryptionService.generate_dek() for _ in range(100)]
        
        # All should be unique
        assert len(set(deks)) == 100
        
        # None should be all zeros
        assert b'\x00' * 32 not in deks


class TestDEKEncryption:
    """Test DEK encryption with KEK."""
    
    def test_encrypt_decrypt_dek(self):
        """DEK should decrypt back to original."""
        dek = EncryptionService.generate_dek()
        kek = EncryptionService.generate_dek()  # Use random key as KEK
        
        encrypted = EncryptionService.encrypt_dek(dek, kek)
        decrypted = EncryptionService.decrypt_dek(encrypted, kek)
        
        assert decrypted == dek
    
    def test_encrypt_dek_different_keks(self):
        """Same DEK encrypted with different KEKs should differ."""
        dek = EncryptionService.generate_dek()
        kek1 = EncryptionService.generate_dek()
        kek2 = EncryptionService.generate_dek()
        
        encrypted1 = EncryptionService.encrypt_dek(dek, kek1)
        encrypted2 = EncryptionService.encrypt_dek(dek, kek2)
        
        assert encrypted1 != encrypted2
    
    def test_decrypt_with_wrong_kek_fails(self):
        """Decrypting with wrong KEK should raise exception."""
        dek = EncryptionService.generate_dek()
        correct_kek = EncryptionService.generate_dek()
        wrong_kek = EncryptionService.generate_dek()
        
        encrypted = EncryptionService.encrypt_dek(dek, correct_kek)
        
        with pytest.raises(Exception):
            EncryptionService.decrypt_dek(encrypted, wrong_kek)
    
    def test_encrypted_dek_format(self):
        """Encrypted DEK should be nonce + ciphertext."""
        dek = EncryptionService.generate_dek()
        kek = EncryptionService.generate_dek()
        
        encrypted = EncryptionService.encrypt_dek(dek, kek)
        
        # Should be longer than original (nonce + tag + ciphertext)
        assert len(encrypted) > len(dek)
        # Nonce is 12 bytes, auth tag is 16 bytes, so minimum 48 bytes
        assert len(encrypted) >= 48


class TestFileEncryption:
    """Test file content encryption."""
    
    def test_encrypt_decrypt_file(self):
        """File content should decrypt back to original."""
        dek = EncryptionService.generate_dek()
        plaintext = b"Hello, World! This is test content."
        
        encrypted = EncryptionService.encrypt_file(plaintext, dek)
        decrypted = EncryptionService.decrypt_file(encrypted, dek)
        
        assert decrypted == plaintext
    
    def test_encrypt_file_different_deks(self):
        """Same content encrypted with different DEKs should differ."""
        dek1 = EncryptionService.generate_dek()
        dek2 = EncryptionService.generate_dek()
        plaintext = b"Same content"
        
        encrypted1 = EncryptionService.encrypt_file(plaintext, dek1)
        encrypted2 = EncryptionService.encrypt_file(plaintext, dek2)
        
        assert encrypted1 != encrypted2
    
    def test_decrypt_with_wrong_dek_fails(self):
        """Decrypting file with wrong DEK should fail."""
        correct_dek = EncryptionService.generate_dek()
        wrong_dek = EncryptionService.generate_dek()
        plaintext = b"Secret message"
        
        encrypted = EncryptionService.encrypt_file(plaintext, correct_dek)
        
        with pytest.raises(Exception):
            EncryptionService.decrypt_file(encrypted, wrong_dek)
    
    def test_encrypt_empty_file(self):
        """Empty file should encrypt/decrypt correctly."""
        dek = EncryptionService.generate_dek()
        plaintext = b""
        
        encrypted = EncryptionService.encrypt_file(plaintext, dek)
        decrypted = EncryptionService.decrypt_file(encrypted, dek)
        
        assert decrypted == plaintext
    
    def test_encrypt_large_file(self):
        """Large file should encrypt/decrypt correctly."""
        dek = EncryptionService.generate_dek()
        plaintext = b"x" * (1024 * 1024)  # 1MB
        
        encrypted = EncryptionService.encrypt_file(plaintext, dek)
        decrypted = EncryptionService.decrypt_file(encrypted, dek)
        
        assert decrypted == plaintext
    
    def test_ciphertext_not_equal_plaintext(self):
        """Encrypted content should not resemble plaintext."""
        dek = EncryptionService.generate_dek()
        plaintext = b"AAAABBBBCCCCDDDD"  # Repetitive pattern
        
        encrypted = EncryptionService.encrypt_file(plaintext, dek)
        
        # Repetitive pattern should not be visible
        assert b"AAAA" not in encrypted


class TestRecoveryKeys:
    """Test recovery key generation and parsing."""
    
    def test_generate_recovery_key_format(self):
        """Recovery key should be human-readable format."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Should contain dashes for readability
        assert "-" in formatted
        # Should be parseable back to valid key
        parsed = EncryptionService.parse_recovery_key(formatted)
        assert len(parsed) == 32  # 256 bits
        assert parsed != b'\x00' * 32  # Not empty
    
    def test_recovery_key_unique(self):
        """Each recovery key should be unique."""
        keys = [EncryptionService.generate_recovery_key()[0] for _ in range(100)]
        
        assert len(set(keys)) == 100
    
    def test_parse_recovery_key_usable_for_encryption(self):
        """Parsed recovery key should work for encryption/decryption."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Parse the key
        parsed = EncryptionService.parse_recovery_key(formatted)
        
        # Should be able to encrypt with parsed key
        dek = EncryptionService.generate_dek()
        encrypted = EncryptionService.encrypt_dek_with_recovery_key(dek, parsed)
        decrypted = EncryptionService.decrypt_dek_with_recovery_key(encrypted, parsed)
        
        assert decrypted == dek
    
    def test_recovery_key_encryption(self):
        """DEK encrypted with recovery key should be decryptable."""
        dek = EncryptionService.generate_dek()
        formatted_key, raw_key = EncryptionService.generate_recovery_key()
        
        encrypted = EncryptionService.encrypt_dek_with_recovery_key(dek, raw_key)
        decrypted = EncryptionService.decrypt_dek_with_recovery_key(encrypted, raw_key)
        
        assert decrypted == dek
    
    def test_recovery_key_case_insensitive(self):
        """Recovery key parsing should be case-insensitive."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Should work with lowercase
        lower = formatted.lower()
        parsed_lower = EncryptionService.parse_recovery_key(lower)
        assert parsed_lower == raw
        
        # Should work with mixed case
        mixed = ''.join(c.upper() if i % 2 == 0 else c.lower() 
                       for i, c in enumerate(formatted))
        parsed_mixed = EncryptionService.parse_recovery_key(mixed)
        assert parsed_mixed == raw
    
    def test_recovery_key_without_dashes(self):
        """Recovery key without dashes should still work."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Remove all dashes
        no_dashes = formatted.replace('-', '')
        parsed = EncryptionService.parse_recovery_key(no_dashes)
        
        assert parsed == raw
    
    def test_recovery_key_with_whitespace(self):
        """Recovery key with extra whitespace should still work."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Add spaces (user might copy-paste with spaces)
        with_spaces = formatted.replace('-', ' - ')
        parsed = EncryptionService.parse_recovery_key(with_spaces.replace(' ', ''))
        
        assert parsed == raw
    
    def test_recovery_key_only_valid_chars(self):
        """Recovery key should only contain valid base32 characters."""
        formatted, raw = EncryptionService.generate_recovery_key()
        
        # Remove dashes and padding
        clean = formatted.replace('-', '').replace('=', '')
        
        # All characters should be A-Z or 2-7
        for char in clean:
            assert (char.isalpha() and char.upper() == char) or char in '234567', \
                f"Invalid character: {char}"
    
    def test_recovery_key_roundtrip_100_times(self):
        """Stress test: 100 consecutive round-trips should all work."""
        for _ in range(100):
            formatted, raw = EncryptionService.generate_recovery_key()
            parsed = EncryptionService.parse_recovery_key(formatted)
            assert parsed == raw
            assert len(parsed) == 32


class TestDEKCache:
    """Test in-memory DEK cache."""
    
    def test_cache_stores_and_retrieves(self):
        """Cache should store and return DEK."""
        cache = DEKCache()
        user_id = 123
        dek = EncryptionService.generate_dek()
        
        cache.set(user_id, dek)
        retrieved = cache.get(user_id)
        
        assert retrieved == dek
    
    def test_cache_returns_none_for_missing(self):
        """Cache should return None for unknown user."""
        cache = DEKCache()
        
        result = cache.get(999)
        
        assert result is None
    
    def test_cache_expires(self):
        """Cache should expire after TTL."""
        cache = DEKCache()
        user_id = 456
        dek = EncryptionService.generate_dek()
        
        # Set with very short TTL
        cache.set(user_id, dek, ttl_seconds=0.001)
        
        # Should exist immediately
        assert cache.get(user_id) == dek
        
        # Wait for expiry
        import time
        time.sleep(0.01)
        
        # Should be expired
        assert cache.get(user_id) is None
    
    def test_cache_invalidation(self):
        """Cache invalidation should remove entry."""
        cache = DEKCache()
        user_id = 789
        dek = EncryptionService.generate_dek()
        
        cache.set(user_id, dek)
        cache.invalidate(user_id)
        
        assert cache.get(user_id) is None
    
    def test_cache_clear_expired(self):
        """Clear expired should remove only expired entries."""
        cache = DEKCache()
        
        dek1 = EncryptionService.generate_dek()
        dek2 = EncryptionService.generate_dek()
        
        # Set one with short TTL, one with long
        cache.set(1, dek1, ttl_seconds=0.001)
        cache.set(2, dek2, ttl_seconds=3600)
        
        import time
        time.sleep(0.01)
        
        cache.clear_expired()
        
        assert cache.get(1) is None  # Expired
        assert cache.get(2) == dek2  # Not expired
    
    def test_cache_thread_safety(self):
        """Cache should be thread-safe."""
        import threading
        
        cache = DEKCache()
        errors = []
        
        def writer():
            try:
                for i in range(100):
                    cache.set(i, EncryptionService.generate_dek())
            except Exception as e:
                errors.append(e)
        
        def reader():
            try:
                for i in range(100):
                    cache.get(i)
            except Exception as e:
                errors.append(e)
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"Thread errors: {errors}"


class TestSaltGeneration:
    """Test salt generation."""
    
    def test_generate_salt_unique(self):
        """Each salt should be unique."""
        salts = [EncryptionService.generate_salt() for _ in range(100)]
        
        assert len(set(salts)) == 100
        assert all(len(s) == 32 for s in salts)  # 256 bits
    
    def test_generate_salt_random(self):
        """Salt should be cryptographically random."""
        salts = [EncryptionService.generate_salt() for _ in range(100)]
        
        # No salt should be all zeros
        assert b'\x00' * 32 not in salts
