"""Abstract storage interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Union, Optional, Iterator


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class FileNotFoundError(StorageError):
    """File not found in storage."""
    pass


class UploadError(StorageError):
    """Failed to upload file."""
    pass


class DownloadError(StorageError):
    """Failed to download file."""
    pass


class DeleteError(StorageError):
    """Failed to delete file."""
    pass


@dataclass
class StorageConfig:
    """Storage configuration."""
    backend: str  # 'local', 's3', 'minio'
    
    # Local storage settings
    base_path: Optional[Path] = None
    
    # S3/MinIO settings
    endpoint_url: Optional[str] = None
    bucket_name: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    region: str = "us-east-1"
    use_ssl: bool = True
    
    # Thumbnail settings (for S3 - separate bucket or path)
    thumbnail_path: Optional[str] = None
    
    def __post_init__(self):
        if self.backend == "local" and self.base_path is None:
            from ...config import UPLOADS_DIR
            self.base_path = Path(UPLOADS_DIR).parent


class RandomAccessReader(ABC):
    """Seekable byte-reader over a stored (here: encrypted) object.

    Required by HTTP Range serving and chunked-envelope decryption, which
    need ``seek``/``tell`` to skip to the chunks overlapping the requested
    byte range. Backends that cannot provide a seekable stream (e.g. S3's
    single-pass ``StreamingBody``) should raise ``NotImplementedError`` from
    :meth:`StorageInterface.get_random_access_reader` rather than return a
    broken reader.

    Implementations are synchronous and must support use as a context
    manager (``with reader: ...``) and be safe to iterate / read / seek /
    close. The ``size`` property reports the total stored byte length.
    """

    @abstractmethod
    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes (all remaining if ``-1``)."""

    @abstractmethod
    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to ``offset``; ``whence`` follows :pyclass:`io.SEEK_*`."""

    @abstractmethod
    def tell(self) -> int:
        """Return the current stream position."""

    @abstractmethod
    def close(self) -> None:
        """Release any underlying resource."""

    @property
    @abstractmethod
    def size(self) -> int:
        """Total size of the stored object in bytes."""

    def __enter__(self) -> "RandomAccessReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class StorageInterface(ABC):
    """Abstract interface for file storage operations.

    Implementations:
    - LocalStorage: Filesystem storage
    - S3Storage: AWS S3 / MinIO / DigitalOcean Spaces
    """
    
    @abstractmethod
    async def upload(
        self,
        file_id: str,
        content: Union[bytes, BinaryIO],
        folder: str = "uploads",
        content_type: Optional[str] = None
    ) -> str:
        """Upload a file to storage.
        
        Args:
            file_id: Unique file identifier
            content: File content as bytes or file-like object
            folder: Subfolder (uploads, thumbnails, backups)
            content_type: MIME type of the file
            
        Returns:
            Storage key/path of the uploaded file
            
        Raises:
            UploadError: If upload fails
        """
        pass
    
    @abstractmethod
    async def download(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> bytes:
        """Download a file from storage.
        
        Args:
            file_id: Unique file identifier
            folder: Subfolder
            
        Returns:
            File content as bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
            DownloadError: If download fails
        """
        pass
    
    @abstractmethod
    async def get_stream(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> BinaryIO:
        """Get a file as a stream for reading.

        Args:
            file_id: Unique file identifier
            folder: Subfolder

        Returns:
            File-like object for reading

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        pass
    
    @abstractmethod
    async def delete(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> bool:
        """Delete a file from storage.
        
        Args:
            file_id: Unique file identifier
            folder: Subfolder
            
        Returns:
            True if deleted, False if didn't exist
            
        Raises:
            DeleteError: If deletion fails for other reasons
        """
        pass
    
    @abstractmethod
    def exists(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> bool:
        """Check if a file exists in storage.
        
        Args:
            file_id: Unique file identifier
            folder: Subfolder
            
        Returns:
            True if file exists
        """
        pass
    
    @abstractmethod
    def get_url(
        self,
        file_id: str,
        folder: str = "uploads",
        expires: Optional[int] = None
    ) -> str:
        """Get URL for accessing the file.
        
        For local storage: returns relative path
        For S3: returns presigned URL or public URL
        
        Args:
            file_id: Unique file identifier
            folder: Subfolder
            expires: URL expiration time in seconds (for presigned URLs)
            
        Returns:
            URL to access the file
        """
        pass
    
    @abstractmethod
    def get_path(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> Union[str, Path]:
        """Get storage path/key for the file.
        
        Args:
            file_id: Unique file identifier
            folder: Subfolder
            
        Returns:
            Path (local) or key (S3) for the file
        """
        pass
    
    @abstractmethod
    async def copy(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Copy a file within storage.
        
        Args:
            source_id: Source file identifier
            dest_id: Destination file identifier
            source_folder: Source subfolder
            dest_folder: Destination subfolder
            
        Returns:
            Storage key/path of the copied file
        """
        pass
    
    @abstractmethod
    async def move(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Move a file within storage.
        
        Args:
            source_id: Source file identifier
            dest_id: Destination file identifier
            source_folder: Source subfolder
            dest_folder: Destination subfolder
            
        Returns:
            Storage key/path of the moved file
        """
        pass
    
    @abstractmethod
    def list_files(
        self,
        folder: str = "uploads",
        prefix: Optional[str] = None
    ) -> Iterator[str]:
        """List files in storage.
        
        Args:
            folder: Subfolder
            prefix: Filter by prefix
            
        Returns:
            Iterator of file IDs
        """
        pass
    
    @abstractmethod
    async def get_size(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> int:
        """Get file size in bytes.

        Args:
            file_id: Unique file identifier
            folder: Subfolder

        Returns:
            File size in bytes

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        pass

    def get_random_access_reader(
        self,
        file_id: str,
        folder: str = "uploads"
    ) -> RandomAccessReader:
        """Return a seekable reader over the stored object.

        Used by HTTP Range serving and chunked-envelope decryption, which
        require ``seek``/``tell``. Backends that cannot satisfy random
        access (e.g. S3's single-pass object stream) raise
        ``NotImplementedError``; callers must fall back to whole-file
        streaming for those backends.

        Args:
            file_id: Unique file identifier
            folder: Subfolder

        Returns:
            A :class:`RandomAccessReader` positioned at byte 0.

        Raises:
            FileNotFoundError: If file doesn't exist
            NotImplementedError: If the backend cannot provide random access
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support random-access reads"
        )

    async def upload_batch(
        self,
        files: list[tuple[str, Union[bytes, BinaryIO], str]]
    ) -> list[str]:
        """Upload multiple files.
        
        Args:
            files: List of (file_id, content, folder) tuples
            
        Returns:
            List of storage keys/paths
        """
        results = []
        for file_id, content, folder in files:
            key = await self.upload(file_id, content, folder)
            results.append(key)
        return results
    
    async def delete_batch(
        self,
        file_ids: list[str],
        folder: str = "uploads"
    ) -> list[bool]:
        """Delete multiple files.
        
        Args:
            file_ids: List of file identifiers
            folder: Subfolder
            
        Returns:
            List of deletion results
        """
        results = []
        for file_id in file_ids:
            result = await self.delete(file_id, folder)
            results.append(result)
        return results
