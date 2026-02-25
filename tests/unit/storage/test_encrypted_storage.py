"""Unit tests for EncryptedStorage wrapper."""
import asyncio
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from app.infrastructure.storage import LocalStorage, EncryptedStorage, StorageConfig


@pytest.fixture
def temp_storage():
    """Create a temporary encrypted storage instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = StorageConfig(backend="local", base_path=Path(tmpdir))
        local = LocalStorage(config)
        
        # Create mock encryption service with real AES-like encryption
        import hashlib
        encryption_service = MagicMock()
        encryption_service.get_dek.return_value = b"x" * 32  # 32-byte DEK
        
        # Setup sync encrypt/decrypt methods (used by EncryptedStorage)
        # Use simple encryption with nonce prefix for testing
        def mock_encrypt(data, dek):
            key = hashlib.sha256(dek).digest()
            result = bytearray()
            for i, b in enumerate(data):
                result.append(b ^ key[i % len(key)])
            # Add nonce prefix and tag suffix to simulate real encryption overhead
            return b"nonce123:" + bytes(result) + b":tag456"
        
        def mock_decrypt(data, dek):
            # Remove nonce and tag, then decrypt
            if data.startswith(b"nonce123:") and data.endswith(b":tag456"):
                data = data[9:-7]  # Remove prefix and suffix
            key = hashlib.sha256(dek).digest()
            result = bytearray()
            for i, b in enumerate(data):
                result.append(b ^ key[i % len(key)])
            return bytes(result)
        
        encryption_service.encrypt_data = mock_encrypt
        encryption_service.decrypt_data = mock_decrypt
        
        storage = EncryptedStorage(
            backend=local,
            encryption_service=encryption_service,
            user_id=1
        )
        
        yield storage, local, encryption_service


@pytest.fixture
def run_async():
    """Helper to run async functions in sync context."""
    def _run(coro):
        return asyncio.run(coro)
    return _run


class TestEncryptedStorageEncryption:
    """Test that content is properly encrypted."""
    
    def test_upload_encrypts_content(self, temp_storage, run_async):
        """Upload should encrypt content before storing."""
        storage, local, encryption_service = temp_storage
        content = b"secret message"
        file_id = "encrypted.txt"
        
        run_async(storage.upload(file_id, content, "uploads"))
        
        # Raw content should be encrypted (not plaintext)
        raw_content = run_async(local.download(file_id, "uploads"))
        assert raw_content != content
        assert b"secret message" not in raw_content
        # Verify our mock was called (has nonce prefix and tag suffix)
        assert raw_content.startswith(b"nonce123:")
        assert raw_content.endswith(b":tag456")
    
    def test_download_decrypts_content(self, temp_storage, run_async):
        """Download should decrypt content."""
        storage, local, encryption_service = temp_storage
        content = b"decrypt me"
        file_id = "decrypt.txt"
        # Upload via storage to encrypt properly
        run_async(storage.upload(file_id, content, "uploads"))
        
        # Download and verify decryption
        result = run_async(storage.download(file_id, "uploads"))
        
        assert result == content


class TestEncryptedStoragePassThrough:
    """Test that non-encrypted operations pass through to underlying storage."""
    
    def test_exists_passes_through(self, temp_storage, run_async):
        """Exists should delegate to underlying storage."""
        storage, local, encryption_service = temp_storage
        
        # File doesn't exist yet
        assert storage.exists("test.txt", "uploads") is False
        
        # Create file directly in local storage
        run_async(local.upload("test.txt", b"content", "uploads"))
        
        # Now it should exist
        assert storage.exists("test.txt", "uploads") is True
    
    def test_delete_passes_through(self, temp_storage, run_async):
        """Delete should delegate to underlying storage."""
        storage, local, encryption_service = temp_storage
        run_async(local.upload("delete.txt", b"content", "uploads"))
        
        result = run_async(storage.delete("delete.txt", "uploads"))
        
        assert result is True
        assert not local.exists("delete.txt", "uploads")
    
    def test_list_files_passes_through(self, temp_storage, run_async):
        """List files should delegate to underlying storage."""
        storage, local, encryption_service = temp_storage
        run_async(local.upload("file1.txt", b"1", "uploads"))
        run_async(local.upload("file2.txt", b"2", "uploads"))
        
        result = list(storage.list_files("uploads"))
        
        assert sorted(result) == ["file1.txt", "file2.txt"]
    
    def test_get_size_passes_through_encrypted_size(self, temp_storage, run_async):
        """Get size should return encrypted size (not original)."""
        storage, local, encryption_service = temp_storage
        content = b"x" * 1000  # 1000 bytes
        run_async(storage.upload("sized.txt", content, "uploads"))
        
        encrypted_size = run_async(storage.get_size("sized.txt", "uploads"))
        
        # Encrypted size should be larger due to encryption overhead
        assert encrypted_size > 1000
    
    def test_get_url_passes_through(self, temp_storage):
        """Get URL should delegate to underlying storage."""
        storage, local, encryption_service = temp_storage
        
        result = storage.get_url("test.txt", "uploads")
        expected = local.get_url("test.txt", "uploads")
        
        assert result == expected


class TestEncryptedStorageCopyMove:
    """Test copy and move with encrypted storage."""
    
    def test_copy_preserves_encryption(self, temp_storage, run_async):
        """Copy should work with encrypted files."""
        storage, local, encryption_service = temp_storage
        run_async(storage.upload("source.txt", b"secret", "uploads"))
        
        run_async(storage.copy("source.txt", "dest.txt", "uploads", "uploads"))
        
        # Both files should be decryptable
        assert run_async(storage.download("source.txt", "uploads")) == b"secret"
        assert run_async(storage.download("dest.txt", "uploads")) == b"secret"
    
    def test_move_preserves_encryption(self, temp_storage, run_async):
        """Move should work with encrypted files."""
        storage, local, encryption_service = temp_storage
        run_async(storage.upload("old.txt", b"moved", "uploads"))
        
        run_async(storage.move("old.txt", "new.txt", "uploads", "uploads"))
        
        assert not storage.exists("old.txt", "uploads")
        assert run_async(storage.download("new.txt", "uploads")) == b"moved"


class TestEncryptedStorageEdgeCases:
    """Test edge cases."""
    
    def test_encrypt_empty_file(self, temp_storage, run_async):
        """Encrypt should handle empty files."""
        storage, local, encryption_service = temp_storage
        
        run_async(storage.upload("empty.txt", b"", "uploads"))
        result = run_async(storage.download("empty.txt", "uploads"))
        
        assert result == b""
    
    def test_encrypt_large_file(self, temp_storage, run_async):
        """Encrypt should handle large files."""
        storage, local, encryption_service = temp_storage
        content = b"x" * (1024 * 1024)  # 1MB
        
        run_async(storage.upload("large.bin", content, "uploads"))
        result = run_async(storage.download("large.bin", "uploads"))
        
        assert result == content
    
    def test_encrypt_binary_data(self, temp_storage, run_async):
        """Encrypt should handle binary data with null bytes."""
        storage, local, encryption_service = temp_storage
        content = bytes(range(256)) * 100  # All byte values
        
        run_async(storage.upload("binary.bin", content, "uploads"))
        result = run_async(storage.download("binary.bin", "uploads"))
        
        assert result == content
    
    def test_missing_dek_raises_error(self, temp_storage, run_async):
        """Operations should fail if DEK is not available."""
        storage, local, encryption_service = temp_storage
        encryption_service.get_dek.return_value = None
        
        from app.infrastructure.storage.base import StorageError
        with pytest.raises(StorageError):
            run_async(storage.upload("test.txt", b"content", "uploads"))
