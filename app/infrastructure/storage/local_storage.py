"""Local filesystem storage implementation."""
import os
import shutil
from pathlib import Path
from typing import BinaryIO, Optional, Union, Iterator
import io
import aiofiles

from .base import (
    StorageInterface,
    StorageConfig,
    StorageError,
    FileNotFoundError as StorageFileNotFoundError,
    UploadError,
    DownloadError,
    DeleteError
)


class LocalStorage(StorageInterface):
    """Local filesystem storage backend.
    
    Stores files in directory structure:
        base_path/
            uploads/
                <file_id>
            thumbnails/
                <file_id>.jpg
            backups/
                <timestamp>/<files>
    """
    
    def __init__(self, config: StorageConfig):
        """Initialize local storage.
        
        Args:
            config: Storage configuration with base_path
        """
        if config.backend != "local":
            raise ValueError(f"LocalStorage requires backend='local', got '{config.backend}'")
        
        self.config = config
        self.base_path = Path(config.base_path)
        
        # Create subdirectories
        for folder in ["uploads", "thumbnails", "backups"]:
            (self.base_path / folder).mkdir(parents=True, exist_ok=True)
    
    def _get_path(self, file_id: str, folder: str) -> Path:
        """Get full filesystem path for a file."""
        # Sanitize file_id to prevent directory traversal
        safe_id = Path(file_id).name
        return self.base_path / folder / safe_id
    
    async def upload(
        self,
        file_id: str,
        content: Union[bytes, BinaryIO],
        folder: str = "uploads",
        content_type: Optional[str] = None
    ) -> str:
        """Upload file to local filesystem."""
        file_path = self._get_path(file_id, folder)
        
        # Create parent directory if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            if isinstance(content, bytes):
                async with aiofiles.open(file_path, 'wb') as f:
                    await f.write(content)
            else:
                # Copy from file-like object
                async with aiofiles.open(file_path, 'wb') as f:
                    while True:
                        chunk = content.read(8192)  # 8KB chunks
                        if not chunk:
                            break
                        if isinstance(chunk, str):
                            chunk = chunk.encode('utf-8')
                        await f.write(chunk)
            
            return str(file_path.relative_to(self.base_path))
            
        except (IOError, OSError) as e:
            raise UploadError(f"Failed to upload {file_id}: {e}")
    
    async def download(self, file_id: str, folder: str = "uploads") -> bytes:
        """Download file from local filesystem."""
        file_path = self._get_path(file_id, folder)
        
        if not file_path.exists():
            raise StorageFileNotFoundError(f"File not found: {file_id}")
        
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                return await f.read()
        except (IOError, OSError) as e:
            raise DownloadError(f"Failed to download {file_id}: {e}")
    
    def get_stream(self, file_id: str, folder: str = "uploads") -> BinaryIO:
        """Get file as stream for reading."""
        file_path = self._get_path(file_id, folder)
        
        if not file_path.exists():
            raise StorageFileNotFoundError(f"File not found: {file_id}")
        
        try:
            return open(file_path, 'rb')
        except (IOError, OSError) as e:
            raise DownloadError(f"Failed to open {file_id}: {e}")
    
    async def delete(self, file_id: str, folder: str = "uploads") -> bool:
        """Delete file from local filesystem."""
        file_path = self._get_path(file_id, folder)
        
        if not file_path.exists():
            return False
        
        try:
            file_path.unlink()
            return True
        except (IOError, OSError) as e:
            raise DeleteError(f"Failed to delete {file_id}: {e}")
    
    def exists(self, file_id: str, folder: str = "uploads") -> bool:
        """Check if file exists."""
        file_path = self._get_path(file_id, folder)
        return file_path.exists() and file_path.is_file()
    
    def get_url(self, file_id: str, folder: str = "uploads", expires: Optional[int] = None) -> str:
        """Get URL for file.
        
        For local storage, returns relative URL path.
        """
        # Return URL path relative to base
        return f"/{folder}/{file_id}"
    
    def get_path(self, file_id: str, folder: str = "uploads") -> Path:
        """Get full filesystem path."""
        return self._get_path(file_id, folder)
    
    async def copy(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Copy file within local storage."""
        source_path = self._get_path(source_id, source_folder)
        dest_path = self._get_path(dest_id, dest_folder)
        
        if not source_path.exists():
            raise StorageFileNotFoundError(f"Source file not found: {source_id}")
        
        # Create parent directory
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.copy2(source_path, dest_path)
            return str(dest_path.relative_to(self.base_path))
        except (IOError, OSError) as e:
            raise StorageError(f"Failed to copy {source_id} to {dest_id}: {e}")
    
    async def move(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Move file within local storage."""
        source_path = self._get_path(source_id, source_folder)
        dest_path = self._get_path(dest_id, dest_folder)
        
        if not source_path.exists():
            raise StorageFileNotFoundError(f"Source file not found: {source_id}")
        
        # Create parent directory
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.move(str(source_path), str(dest_path))
            return str(dest_path.relative_to(self.base_path))
        except (IOError, OSError) as e:
            raise StorageError(f"Failed to move {source_id} to {dest_id}: {e}")
    
    def list_files(self, folder: str = "uploads", prefix: Optional[str] = None) -> Iterator[str]:
        """List files in folder."""
        folder_path = self.base_path / folder
        
        if not folder_path.exists():
            return
        
        for item in folder_path.iterdir():
            if item.is_file():
                file_id = item.name
                if prefix is None or file_id.startswith(prefix):
                    yield file_id
    
    async def get_size(self, file_id: str, folder: str = "uploads") -> int:
        """Get file size in bytes."""
        file_path = self._get_path(file_id, folder)
        
        if not file_path.exists():
            raise StorageFileNotFoundError(f"File not found: {file_id}")
        
        return file_path.stat().st_size
    
    def get_absolute_path(self, file_id: str, folder: str = "uploads") -> Path:
        """Get absolute filesystem path (for local operations)."""
        return self._get_path(file_id, folder).resolve()
