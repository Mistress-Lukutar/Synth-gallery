"""Unit tests for LocalStorage backend."""
import asyncio
import pytest
import tempfile
from pathlib import Path
import io

from app.infrastructure.storage import LocalStorage, StorageConfig


@pytest.fixture
def temp_storage():
    """Create a temporary LocalStorage instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir)
        config = StorageConfig(backend="local", base_path=base_path)
        storage = LocalStorage(config)
        yield storage


@pytest.fixture
def run_async():
    """Helper to run async functions in sync context."""
    def _run(coro):
        return asyncio.run(coro)
    return _run


class TestLocalStorageUpload:
    """Test upload functionality."""
    
    def test_upload_creates_file(self, temp_storage, run_async):
        """Upload should create file in uploads folder."""
        content = b"test content"
        file_id = "test-file.txt"
        
        result = run_async(temp_storage.upload(file_id, content, "uploads"))
        
        assert temp_storage.exists(file_id, "uploads") is True
        downloaded = run_async(temp_storage.download(file_id, "uploads"))
        assert downloaded == content
        assert isinstance(result, str)  # Returns path
    
    def test_upload_creates_thumbnail_folder(self, temp_storage, run_async):
        """Upload should create thumbnails folder if needed."""
        content = b"thumb content"
        file_id = "thumb.jpg"
        
        run_async(temp_storage.upload(file_id, content, "thumbnails"))
        
        assert temp_storage.exists(file_id, "thumbnails") is True
        downloaded = run_async(temp_storage.download(file_id, "thumbnails"))
        assert downloaded == content
    
    def test_upload_bytes_io(self, temp_storage, run_async):
        """Upload should accept BytesIO content."""
        content = io.BytesIO(b"stream content")
        file_id = "stream.txt"
        
        run_async(temp_storage.upload(file_id, content, "uploads"))
        
        downloaded = run_async(temp_storage.download(file_id, "uploads"))
        assert downloaded == b"stream content"
    
    def test_upload_overwrites_existing(self, temp_storage, run_async):
        """Upload should overwrite existing file."""
        file_id = "existing.txt"
        run_async(temp_storage.upload(file_id, b"old", "uploads"))
        
        run_async(temp_storage.upload(file_id, b"new", "uploads"))
        
        downloaded = run_async(temp_storage.download(file_id, "uploads"))
        assert downloaded == b"new"


class TestLocalStorageDownload:
    """Test download functionality."""
    
    def test_download_existing_file(self, temp_storage, run_async):
        """Download should return file content."""
        content = b"download me"
        file_id = "download.txt"
        run_async(temp_storage.upload(file_id, content, "uploads"))
        
        result = run_async(temp_storage.download(file_id, "uploads"))
        
        assert result == content
    
    def test_download_nonexistent_file_raises(self, temp_storage, run_async):
        """Download should raise FileNotFoundError for missing file."""
        from app.infrastructure.storage.base import FileNotFoundError as StorageFileNotFound
        
        with pytest.raises(StorageFileNotFound):
            run_async(temp_storage.download("missing.txt", "uploads"))
    
    def test_download_from_thumbnails(self, temp_storage, run_async):
        """Download should work from thumbnails folder."""
        content = b"thumbnail"
        file_id = "thumb.jpg"
        run_async(temp_storage.upload(file_id, content, "thumbnails"))
        
        result = run_async(temp_storage.download(file_id, "thumbnails"))
        
        assert result == content


class TestLocalStorageExists:
    """Test exists functionality."""
    
    def test_exists_true_for_existing_file(self, temp_storage, run_async):
        """Exists should return True for existing file."""
        run_async(temp_storage.upload("exists.txt", b"content", "uploads"))
        
        assert temp_storage.exists("exists.txt", "uploads") is True
    
    def test_exists_false_for_missing_file(self, temp_storage):
        """Exists should return False for missing file."""
        assert temp_storage.exists("missing.txt", "uploads") is False
    
    def test_exists_false_for_directory(self, temp_storage, run_async):
        """Exists should return False for directories."""
        # Create a file in a subdirectory
        run_async(temp_storage.upload("subdir/nested.txt", b"nested", "uploads"))
        
        # Check that the subdir itself is not considered a file
        assert temp_storage.exists("subdir", "uploads") is False
        assert temp_storage.exists("subdir/nested.txt", "uploads") is True


class TestLocalStorageDelete:
    """Test delete functionality."""
    
    def test_delete_removes_file(self, temp_storage, run_async):
        """Delete should remove existing file."""
        file_id = "delete.txt"
        run_async(temp_storage.upload(file_id, b"content", "uploads"))
        
        result = run_async(temp_storage.delete(file_id, "uploads"))
        
        assert result is True
        assert temp_storage.exists(file_id, "uploads") is False
    
    def test_delete_returns_false_for_missing_file(self, temp_storage, run_async):
        """Delete should return False for non-existent file."""
        result = run_async(temp_storage.delete("missing.txt", "uploads"))
        
        assert result is False


class TestLocalStorageListFiles:
    """Test list_files functionality."""
    
    def test_list_files_empty_folder(self, temp_storage):
        """List files should return empty iterator for empty folder."""
        result = list(temp_storage.list_files("uploads"))
        
        assert result == []
    
    def test_list_files_returns_file_ids(self, temp_storage, run_async):
        """List files should return file IDs in folder."""
        run_async(temp_storage.upload("file1.txt", b"1", "uploads"))
        run_async(temp_storage.upload("file2.txt", b"2", "uploads"))
        run_async(temp_storage.upload(".hidden", b"hidden", "uploads"))
        
        result = list(temp_storage.list_files("uploads"))
        
        assert sorted(result) == [".hidden", "file1.txt", "file2.txt"]
    
    def test_list_files_with_prefix(self, temp_storage, run_async):
        """List files should filter by prefix."""
        run_async(temp_storage.upload("abc.txt", b"1", "uploads"))
        run_async(temp_storage.upload("abd.txt", b"2", "uploads"))
        run_async(temp_storage.upload("xyz.txt", b"3", "uploads"))
        
        result = list(temp_storage.list_files("uploads", prefix="ab"))
        
        assert sorted(result) == ["abc.txt", "abd.txt"]
    
    def test_list_files_ignores_subdirectories(self, temp_storage, run_async):
        """List files should not include subdirectory names."""
        run_async(temp_storage.upload("file.txt", b"1", "uploads"))
        run_async(temp_storage.upload("another.txt", b"2", "uploads"))
        
        result = list(temp_storage.list_files("uploads"))
        
        # Should include files but not directories
        assert "file.txt" in result
        assert "another.txt" in result


class TestLocalStorageGetSize:
    """Test get_size functionality."""
    
    def test_get_size_returns_bytes(self, temp_storage, run_async):
        """Get size should return file size in bytes."""
        content = b"exactly 20 bytes!!"  # 18 bytes actually
        file_id = "sized.txt"
        run_async(temp_storage.upload(file_id, content, "uploads"))
        
        result = run_async(temp_storage.get_size(file_id, "uploads"))
        
        assert result == len(content)
    
    def test_get_size_raises_for_missing(self, temp_storage, run_async):
        """Get size should raise for missing file."""
        from app.infrastructure.storage.base import FileNotFoundError as StorageFileNotFound
        
        with pytest.raises(StorageFileNotFound):
            run_async(temp_storage.get_size("missing.txt", "uploads"))


class TestLocalStorageGetUrl:
    """Test get_url functionality."""
    
    def test_get_url_returns_path(self, temp_storage, run_async):
        """Get URL should return file path for local storage."""
        file_id = "url-test.txt"
        run_async(temp_storage.upload(file_id, b"content", "uploads"))
        
        result = temp_storage.get_url(file_id, "uploads")
        
        assert isinstance(result, str)
        assert file_id in result
    
    def test_get_url_returns_path_for_missing_file(self, temp_storage):
        """Get URL should return path even if file doesn't exist."""
        result = temp_storage.get_url("missing.txt", "uploads")
        
        assert isinstance(result, str)
        assert "missing.txt" in result


class TestLocalStorageStream:
    """Test get_stream functionality."""
    
    def test_get_stream_reads_content(self, temp_storage, run_async):
        """Get stream should return readable file object."""
        content = b"streamable content"
        file_id = "stream.txt"
        run_async(temp_storage.upload(file_id, content, "uploads"))
        
        stream = temp_storage.get_stream(file_id, "uploads")
        
        assert stream.read() == content
    
    def test_get_stream_raises_for_missing(self, temp_storage):
        """Get stream should raise for missing file."""
        from app.infrastructure.storage.base import FileNotFoundError as StorageFileNotFound
        
        with pytest.raises(StorageFileNotFound):
            temp_storage.get_stream("missing.txt", "uploads")


class TestLocalStorageCopyMove:
    """Test copy and move functionality."""
    
    def test_copy_creates_duplicate(self, temp_storage, run_async):
        """Copy should create duplicate file."""
        run_async(temp_storage.upload("source.txt", b"original", "uploads"))
        
        run_async(temp_storage.copy("source.txt", "dest.txt", "uploads", "uploads"))
        
        assert temp_storage.exists("dest.txt", "uploads")
        assert run_async(temp_storage.download("source.txt", "uploads")) == b"original"
        assert run_async(temp_storage.download("dest.txt", "uploads")) == b"original"
    
    def test_move_renames_file(self, temp_storage, run_async):
        """Move should rename file."""
        run_async(temp_storage.upload("old.txt", b"content", "uploads"))
        
        run_async(temp_storage.move("old.txt", "new.txt", "uploads", "uploads"))
        
        assert not temp_storage.exists("old.txt", "uploads")
        assert temp_storage.exists("new.txt", "uploads")
        assert run_async(temp_storage.download("new.txt", "uploads")) == b"content"
    
    def test_move_between_folders(self, temp_storage, run_async):
        """Move should work between folders."""
        run_async(temp_storage.upload("file.txt", b"content", "uploads"))
        
        run_async(temp_storage.move("file.txt", "file.txt", "uploads", "thumbnails"))
        
        assert not temp_storage.exists("file.txt", "uploads")
        assert temp_storage.exists("file.txt", "thumbnails")


class TestLocalStorageBatch:
    """Test batch operations."""
    
    def test_upload_batch(self, temp_storage, run_async):
        """Upload batch should upload multiple files."""
        files = [
            ("file1.txt", b"content1", "uploads"),
            ("file2.txt", b"content2", "uploads"),
            ("thumb.jpg", b"thumb", "thumbnails"),
        ]
        
        results = run_async(temp_storage.upload_batch(files))
        
        assert len(results) == 3
        assert run_async(temp_storage.download("file1.txt", "uploads")) == b"content1"
        assert run_async(temp_storage.download("file2.txt", "uploads")) == b"content2"
    
    def test_delete_batch(self, temp_storage, run_async):
        """Delete batch should delete multiple files."""
        run_async(temp_storage.upload("file1.txt", b"1", "uploads"))
        run_async(temp_storage.upload("file2.txt", b"2", "uploads"))
        run_async(temp_storage.upload("file3.txt", b"3", "uploads"))
        
        results = run_async(temp_storage.delete_batch(["file1.txt", "file2.txt", "missing.txt"], "uploads"))
        
        assert results == [True, True, False]
        assert not temp_storage.exists("file1.txt", "uploads")
        assert not temp_storage.exists("file2.txt", "uploads")
        assert temp_storage.exists("file3.txt", "uploads")
