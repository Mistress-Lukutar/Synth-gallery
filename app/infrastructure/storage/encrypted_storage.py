"""Encrypted storage wrapper - encrypts/decrypts data on top of any storage backend."""
from typing import BinaryIO, Optional, Union, Iterator
import io

from .base import StorageInterface, StorageError, FileNotFoundError


class EncryptedStorage(StorageInterface):
    """Storage wrapper that encrypts/decrypts data.
    
    Uses server-side encryption with user's DEK (Data Encryption Key).
    This is different from Safe encryption which is client-side.
    
    Use case: Encrypt files at rest in S3 or local storage using server keys.
    """
    
    def __init__(
        self,
        backend: StorageInterface,
        encryption_service,
        user_id: int
    ):
        """Initialize encrypted storage wrapper.
        
        Args:
            backend: Underlying storage (LocalStorage, S3Storage, etc.)
            encryption_service: Service providing encrypt/decrypt methods
            user_id: User ID for retrieving DEK
        """
        self.backend = backend
        self.encryption = encryption_service
        self.user_id = user_id
        self.config = backend.config
    
    async def _get_dek(self):
        """Get user's Data Encryption Key."""
        dek = self.encryption.get_dek(self.user_id)
        if not dek:
            raise StorageError("Encryption key not available")
        return dek
    
    async def upload(
        self,
        file_id: str,
        content: Union[bytes, BinaryIO],
        folder: str = "uploads",
        content_type: Optional[str] = None
    ) -> str:
        """Upload encrypted file."""
        dek = await self._get_dek()
        
        # Read content
        if isinstance(content, bytes):
            data = content
        else:
            data = content.read()
        
        # Encrypt data
        try:
            encrypted_data = self.encryption.encrypt_data(data, dek)
        except Exception as e:
            raise StorageError(f"Encryption failed: {e}")
        
        # Upload encrypted data
        return await self.backend.upload(
            file_id,
            encrypted_data,
            folder,
            content_type
        )
    
    async def download(self, file_id: str, folder: str = "uploads") -> bytes:
        """Download and decrypt file."""
        dek = await self._get_dek()
        
        # Download encrypted data
        encrypted_data = await self.backend.download(file_id, folder)
        
        # Decrypt data
        try:
            return self.encryption.decrypt_data(encrypted_data, dek)
        except Exception as e:
            raise StorageError(f"Decryption failed: {e}")
    
    def get_stream(self, file_id: str, folder: str = "uploads") -> BinaryIO:
        """Get decrypted stream.
        
        Note: This loads entire file into memory for decryption.
        For large files, use download() with chunked processing.
        """
        # For encrypted storage, we need to decrypt first
        # This is a limitation - streaming decryption would require special handling
        import asyncio
        
        # Run async download in sync context
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        data = loop.run_until_complete(self.download(file_id, folder))
        return io.BytesIO(data)
    
    async def delete(self, file_id: str, folder: str = "uploads") -> bool:
        """Delete file from underlying storage."""
        return await self.backend.delete(file_id, folder)
    
    def exists(self, file_id: str, folder: str = "uploads") -> bool:
        """Check if file exists."""
        return self.backend.exists(file_id, folder)
    
    def get_url(self, file_id: str, folder: str = "uploads", expires: Optional[int] = None) -> str:
        """Get URL - returns URL for encrypted file."""
        # Note: The file at this URL will be encrypted
        # Client needs to decrypt it
        return self.backend.get_url(file_id, folder, expires)
    
    def get_path(self, file_id: str, folder: str = "uploads") -> Union[str, any]:
        """Get path from underlying storage."""
        return self.backend.get_path(file_id, folder)
    
    async def copy(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Copy encrypted file."""
        # For encrypted files, we can just copy the encrypted data
        # No need to decrypt/re-encrypt
        return await self.backend.copy(source_id, dest_id, source_folder, dest_folder)
    
    async def move(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Move encrypted file."""
        return await self.backend.move(source_id, dest_id, source_folder, dest_folder)
    
    def list_files(self, folder: str = "uploads", prefix: Optional[str] = None) -> Iterator[str]:
        """List files from underlying storage."""
        return self.backend.list_files(folder, prefix)
    
    async def get_size(self, file_id: str, folder: str = "uploads") -> int:
        """Get encrypted file size."""
        return await self.backend.get_size(file_id, folder)
