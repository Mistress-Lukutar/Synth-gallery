"""Integration tests for backup service using storage abstraction layer."""
import pytest
import tempfile
import zipfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio

from app.infrastructure.storage import LocalStorage, get_storage, StorageConfig
from app.infrastructure.services.backup import FullBackupService


@pytest.fixture
def temp_backup_env():
    """Create temporary environment for backup tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        uploads_dir = tmp_path / "uploads"
        thumbnails_dir = tmp_path / "thumbnails"
        backup_dir = tmp_path / "backups"
        db_path = tmp_path / "gallery.db"
        
        uploads_dir.mkdir()
        thumbnails_dir.mkdir()
        backup_dir.mkdir()
        
        # Create a minimal database
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("INSERT INTO users (username) VALUES ('testuser')")
        conn.commit()
        conn.close()
        
        config = StorageConfig(backend="local", base_path=tmp_path)
        storage = LocalStorage(config)
        
        yield {
            "tmp_path": tmp_path,
            "uploads_dir": uploads_dir,
            "thumbnails_dir": thumbnails_dir,
            "backup_dir": backup_dir,
            "db_path": db_path,
            "storage": storage
        }


@pytest.fixture
def run_async():
    """Helper to run async functions in sync context."""
    def _run(coro):
        return asyncio.run(coro)
    return _run


class TestFullBackupWithStorage:
    """Test full backup using storage abstraction."""
    
    def test_backup_collects_files_from_storage(self, temp_backup_env, run_async):
        """Backup should collect files from storage.uploads folder."""
        env = temp_backup_env
        
        # Upload files via storage (simulating normal app usage)
        run_async(env["storage"].upload("photo1.jpg", b"photo1 content", "uploads"))
        run_async(env["storage"].upload("photo2.jpg", b"photo2 content", "uploads"))
        run_async(env["storage"].upload("video.mp4", b"video content", "uploads"))
        
        # Create backup
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        assert result["success"] is True
        
        # Verify backup contents
        backup_path = Path(result["path"])
        assert backup_path.exists()
        
        with zipfile.ZipFile(backup_path, "r") as zf:
            files = zf.namelist()
            assert "gallery.db" in files
            assert "uploads/photo1.jpg" in files
            assert "uploads/photo2.jpg" in files
            assert "uploads/video.mp4" in files
            assert "manifest.json" in files
            
            # Verify content
            assert zf.read("uploads/photo1.jpg") == b"photo1 content"
            assert zf.read("uploads/photo2.jpg") == b"photo2 content"
    
    def test_backup_excludes_thumbnails(self, temp_backup_env, run_async):
        """Backup should exclude thumbnails (they regenerate)."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("photo.jpg", b"original", "uploads"))
        run_async(env["storage"].upload("photo_thumb.jpg", b"thumbnail", "thumbnails"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        assert result["success"] is True
        
        backup_path = Path(result["path"])
        with zipfile.ZipFile(backup_path, "r") as zf:
            files = zf.namelist()
            assert "uploads/photo.jpg" in files
            assert "thumbnails/photo_thumb.jpg" not in files
    
    def test_backup_includes_checksums(self, temp_backup_env, run_async):
        """Backup should include SHA-256 checksums for all files."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file.txt", b"test content", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        backup_path = Path(result["path"])
        with zipfile.ZipFile(backup_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode())
            
            assert "checksums" in manifest
            assert "gallery.db" in manifest["checksums"]
            assert "uploads/file.txt" in manifest["checksums"]
            
            # Verify checksum format
            for filename, checksum in manifest["checksums"].items():
                assert checksum.startswith("sha256:")
                assert len(checksum) == 71  # "sha256:" + 64 hex chars
    
    def test_backup_manifest_includes_stats(self, temp_backup_env, run_async):
        """Backup manifest should include file stats."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file1.txt", b"content1", "uploads"))
        run_async(env["storage"].upload("file2.txt", b"content2", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        assert result["stats"]["total_files"] == 3  # 2 uploads + db
        assert result["stats"]["total_size_bytes"] > 0
        assert "users" in result["stats"]
        assert "testuser" in result["stats"]["users"]


class TestRestoreWithStorage:
    """Test restore using storage abstraction."""
    
    def test_restore_uploads_to_storage(self, temp_backup_env, run_async):
        """Restore should upload files to storage backend."""
        env = temp_backup_env
        
        # Create a backup
        run_async(env["storage"].upload("photo.jpg", b"original photo", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        # Clear storage
        for file_id in list(env["storage"].list_files("uploads")):
            run_async(env["storage"].delete(file_id, "uploads"))
        
        # Restore
        backup_path = Path(result["path"])
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                with patch("app.infrastructure.services.backup.THUMBNAILS_DIR", env["thumbnails_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        restore_result = FullBackupService.restore_full_backup(backup_path)
        
        assert restore_result["success"] is True
        assert "photo.jpg" in list(env["storage"].list_files("uploads"))
        content = run_async(env["storage"].download("photo.jpg", "uploads"))
        assert content == b"original photo"
    
    def test_restore_with_progress_callback(self, temp_backup_env, run_async):
        """Restore should call progress callback."""
        env = temp_backup_env
        
        # Create backup
        run_async(env["storage"].upload("file1.txt", b"1", "uploads"))
        run_async(env["storage"].upload("file2.txt", b"2", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        # Track progress
        progress_calls = []
        def progress_callback(current, total, message):
            progress_calls.append((current, total, message))
        
        # Restore with progress
        backup_path = Path(result["path"])
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                with patch("app.infrastructure.services.backup.THUMBNAILS_DIR", env["thumbnails_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        FullBackupService.restore_full_backup(backup_path, progress_callback)
        
        assert len(progress_calls) > 0
        # Last call should indicate completion
        assert progress_calls[-1][0] == progress_calls[-1][1]  # current == total


class TestBackupVerification:
    """Test backup verification."""
    
    def test_verify_valid_backup(self, temp_backup_env, run_async):
        """Verify should return valid=True for good backup."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file.txt", b"content", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        backup_path = Path(result["path"])
        verification = FullBackupService.verify_full_backup(backup_path)
        
        assert verification["valid"] is True
        assert verification["verified_files"] == 2  # db + file
        assert verification["errors"] is None
    
    def test_verify_detects_corrupted_file(self, temp_backup_env, run_async):
        """Verify should detect corrupted files."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file.txt", b"content", "uploads"))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        # Corrupt the backup
        backup_path = Path(result["path"])
        with zipfile.ZipFile(backup_path, "a") as zf:
            # Replace file with corrupted content
            zf.writestr("uploads/file.txt", b"corrupted content")
        
        verification = FullBackupService.verify_full_backup(backup_path)
        
        assert verification["valid"] is False
        assert len(verification["errors"]) > 0


class TestBackupRotation:
    """Test backup rotation."""
    
    def test_rotate_keeps_recent_backups(self, temp_backup_env):
        """Rotation should keep most recent backups."""
        env = temp_backup_env
        
        # Manually create backup files with different timestamps in names
        for i in range(7):
            # Create files with timestamps spread across seconds
            timestamp = f"2026-02-26-01200{i}"
            backup_file = env["backup_dir"] / f"backup-{timestamp}.zip"
            backup_file.write_bytes(b"fake backup content")
        
        # Set rotation to keep 5 - need to patch BACKUP_PATH too
        with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
            with patch("app.infrastructure.services.backup.BACKUP_ROTATION_COUNT", 5):
                FullBackupService.rotate_full_backups()
        
        # Check that only 5 remain (the most recent ones)
        backups = sorted(env["backup_dir"].glob("backup-*.zip"))
        assert len(backups) == 5


class TestBackupWithEmptyStorage:
    """Test backup behavior with empty storage."""
    
    def test_backup_with_no_files(self, temp_backup_env):
        """Backup should work even with no uploaded files."""
        env = temp_backup_env
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        result = FullBackupService.create_full_backup()
        
        assert result["success"] is True
        assert result["stats"]["total_files"] == 1  # Just the database
        
        backup_path = Path(result["path"])
        with zipfile.ZipFile(backup_path, "r") as zf:
            files = zf.namelist()
            assert "gallery.db" in files
            assert "manifest.json" in files


class TestBackupProgressCallback:
    """Test progress callback during backup."""
    
    def test_progress_callback_called(self, temp_backup_env, run_async):
        """Progress callback should be called during backup."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file1.txt", b"1", "uploads"))
        run_async(env["storage"].upload("file2.txt", b"2", "uploads"))
        
        progress_calls = []
        def progress_callback(current, total, message):
            progress_calls.append((current, total, message))
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        FullBackupService.create_full_backup(progress_callback)
        
        # Should have been called for each file + final
        assert len(progress_calls) >= 3  # db + 2 files + final at least
        
        # First call should be 0/total
        assert progress_calls[0][0] == 0
        
        # Last call should have current == total
        assert progress_calls[-1][0] == progress_calls[-1][1]
    
    def test_progress_callback_messages(self, temp_backup_env, run_async):
        """Progress callback should include descriptive messages."""
        env = temp_backup_env
        
        run_async(env["storage"].upload("file.txt", b"content", "uploads"))
        
        messages = []
        def progress_callback(current, total, message):
            messages.append(message)
        
        with patch("app.infrastructure.services.backup.BASE_DIR", env["tmp_path"]):
            with patch("app.infrastructure.services.backup.BACKUP_PATH", env["backup_dir"]):
                with patch("app.infrastructure.services.backup.UPLOADS_DIR", env["uploads_dir"]):
                    with patch("app.infrastructure.services.backup.get_storage", return_value=env["storage"]):
                        FullBackupService.create_full_backup(progress_callback)
        
        # Messages should describe what's being added
        assert any("gallery.db" in msg for msg in messages)
        assert any("uploads/" in msg for msg in messages)
        assert any("complete" in msg.lower() for msg in messages)
