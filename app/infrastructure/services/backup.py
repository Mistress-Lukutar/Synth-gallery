"""Backup service for database and encrypted content."""
import asyncio
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..storage import get_storage
from ...config import (
    BASE_DIR, BACKUP_PATH, BACKUP_ROTATION_COUNT, BACKUP_SCHEDULE, BACKUP_ENCRYPTION_KEY
)

DATABASE_PATH = BASE_DIR / "gallery.db"
BACKUPS_DIR = BASE_DIR / "backups"  # Legacy DB-only backups
UPLOADS_DIR = BASE_DIR / "uploads"   # Kept for test compatibility
THUMBNAILS_DIR = BASE_DIR / "thumbnails"  # Kept for test compatibility
MAX_BACKUPS = 5


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _get_backup_key() -> bytes | None:
    """Derive 256-bit AES key from SYNTH_BACKUP_KEY env var.

    Returns None if no backup encryption key is configured.
    """
    if not BACKUP_ENCRYPTION_KEY:
        return None
    return hashlib.sha256(BACKUP_ENCRYPTION_KEY.encode("utf-8")).digest()


def _encrypt_backup_data(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt backup data with AES-256-GCM.

    Returns: nonce (12 bytes) + ciphertext + tag
    """
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def _decrypt_backup_data(encrypted_data: bytes, key: bytes) -> bytes:
    """Decrypt backup data with AES-256-GCM.

    Args:
        encrypted_data: nonce (12 bytes) + ciphertext + tag
        key: 32-byte AES key

    Returns:
        Decrypted plaintext bytes
    """
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def _prepare_db_copy(src_path: Path, dest_path: Path) -> None:
    """Create a copy of the database with sensitive tables cleared.

    Removes sessions and ai_api_keys to avoid leaking session tokens
    and API key hashes in backups.
    """
    shutil.copy2(src_path, dest_path)
    conn = sqlite3.connect(str(dest_path))
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sessions', 'ai_api_keys')"
        )
        existing_tables = {row[0] for row in cursor.fetchall()}
        if "sessions" in existing_tables:
            conn.execute("DELETE FROM sessions")
        if "ai_api_keys" in existing_tables:
            conn.execute("DELETE FROM ai_api_keys")
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# Legacy Database-only Backup Functions (for backwards compatibility)
# =============================================================================

def ensure_backups_dir():
    """Create backups directory if it doesn't exist."""
    BACKUPS_DIR.mkdir(exist_ok=True)


def create_backup(reason: str = "manual") -> str | None:
    """Create a database backup (legacy function).

    Args:
        reason: Reason for backup (e.g., "manual", "pre-migration", "pre-restore")

    Returns:
        Backup filename or None if database doesn't exist
    """
    if not DATABASE_PATH.exists():
        return None

    ensure_backups_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"gallery_{timestamp}_{reason}.db"
    backup_path = BACKUPS_DIR / filename

    shutil.copy2(DATABASE_PATH, backup_path)
    rotate_backups()

    return filename


def list_backups() -> list[dict]:
    """List all database backups with metadata (legacy function)."""
    ensure_backups_dir()

    backups = []
    for file in BACKUPS_DIR.glob("gallery_*.db"):
        stat = file.stat()
        parts = file.stem.split("_")
        reason = parts[3] if len(parts) > 3 else "unknown"

        try:
            date_str = f"{parts[1]}_{parts[2]}"
            created_at = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
        except (IndexError, ValueError):
            created_at = datetime.fromtimestamp(stat.st_mtime)

        backups.append({
            "name": file.name,
            "size": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "created_at": created_at.isoformat(),
            "reason": reason
        })

    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups


def get_backup_path(filename: str) -> Path | None:
    """Get backup file path if it exists."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None

    backup_path = BACKUPS_DIR / filename
    if backup_path.exists() and backup_path.is_file():
        return backup_path
    return None


def restore_backup(filename: str) -> bool:
    """Restore database from backup (legacy function)."""
    backup_path = get_backup_path(filename)
    if not backup_path:
        return False

    if DATABASE_PATH.exists():
        create_backup("pre-restore")

    shutil.copy2(backup_path, DATABASE_PATH)
    return True


def delete_backup(filename: str) -> bool:
    """Delete a backup file."""
    backup_path = get_backup_path(filename)
    if not backup_path:
        return False

    backup_path.unlink()
    return True


def rotate_backups(keep: int = MAX_BACKUPS):
    """Delete old backups, keeping only the most recent ones."""
    backups = list_backups()

    if len(backups) <= keep:
        return

    for backup in backups[keep:]:
        backup_path = BACKUPS_DIR / backup["name"]
        if backup_path.exists():
            backup_path.unlink()


# =============================================================================
# Full Backup Service (Database + Media Files)
# =============================================================================

class FullBackupService:
    """Handles creation, verification, and restoration of full backups."""

    MANIFEST_VERSION = "1.0"
    DB_NAME = "gallery.db"

    @staticmethod
    def get_file_checksum(file_path: Path) -> str:
        """Calculate SHA-256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"

    @staticmethod
    def list_full_backups() -> list[dict]:
        """List all available full backups with metadata."""
        backups = []
        if not BACKUP_PATH.exists():
            return backups

        for file in sorted(BACKUP_PATH.glob("backup-*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(file, "r") as zf:
                    if "manifest.json" in zf.namelist():
                        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                        backups.append({
                            "filename": file.name,
                            "path": str(file),
                            "size": file.stat().st_size,
                            "size_human": _format_size(file.stat().st_size),
                            "created_at": manifest.get("created_at"),
                            "version": manifest.get("synth_gallery_version"),
                            "stats": manifest.get("stats", {}),
                            "valid": True
                        })
                    else:
                        backups.append({
                            "filename": file.name,
                            "path": str(file),
                            "size": file.stat().st_size,
                            "size_human": _format_size(file.stat().st_size),
                            "created_at": None,
                            "valid": False,
                            "error": "Missing manifest"
                        })
            except Exception as e:
                backups.append({
                    "filename": file.name,
                    "path": str(file),
                    "size": file.stat().st_size if file.exists() else 0,
                    "size_human": _format_size(file.stat().st_size) if file.exists() else "0 B",
                    "valid": False,
                    "error": str(e)
                })

        return backups

    @staticmethod
    def create_full_backup(progress_callback: Optional[Callable] = None) -> dict:
        """
        Create a full backup of database and media files.

        Args:
            progress_callback: Optional callable(current, total, message) for progress updates

        Returns:
            dict with backup info or error
        """
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        backup_filename = f"backup-{timestamp}.zip"
        backup_path = BACKUP_PATH / backup_filename
        temp_path = BACKUP_PATH / f".{backup_filename}.tmp"

        db_path = BASE_DIR / FullBackupService.DB_NAME

        if not db_path.exists():
            return {"success": False, "error": "Database not found"}

        # Get storage for media files
        storage = get_storage()

        # Prepare sanitized database copy
        temp_db_path = BACKUP_PATH / f".backup-db-{timestamp}.tmp"
        try:
            _prepare_db_copy(db_path, temp_db_path)

            # Determine backup encryption settings
            backup_key = _get_backup_key()
            db_arc_name = "gallery.db"
            encrypted = False

            if backup_key:
                # Encrypt the sanitized database
                with open(temp_db_path, "rb") as f:
                    db_plaintext = f.read()
                db_ciphertext = _encrypt_backup_data(db_plaintext, backup_key)
                db_arc_name = "gallery.db.enc"
                encrypted = True

            # Collect all files to backup
            files_to_backup = []
            checksums = {}

            # Database (sanitized, possibly encrypted)
            files_to_backup.append((db_arc_name, temp_db_path, None, encrypted))

            # Uploads from storage (works for both local and S3)
            uploads = storage.list_files("uploads")
            for file_id in uploads:
                files_to_backup.append((f"uploads/{file_id}", None, file_id, False))

            total_files = len(files_to_backup)
            total_size = 0

            # Create zip with files
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for idx, (arc_name, file_path, file_id, is_encrypted_db) in enumerate(files_to_backup):
                    if progress_callback:
                        progress_callback(idx, total_files, f"Adding {arc_name}")

                    if is_encrypted_db:
                        # Write encrypted database bytes
                        zf.writestr(arc_name, db_ciphertext)
                        checksum = f"sha256:{hashlib.sha256(db_ciphertext).hexdigest()}"
                        file_size = len(db_ciphertext)
                    elif arc_name == db_arc_name and not encrypted:
                        # Unencrypted sanitized database copy
                        zf.write(file_path, arc_name)
                        checksum = FullBackupService.get_file_checksum(file_path)
                        file_size = file_path.stat().st_size
                    else:
                        # Media file from storage (download if needed)
                        content = asyncio.run(storage.download(file_id, "uploads"))
                        zf.writestr(arc_name, content)
                        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
                        file_size = len(content)

                    checksums[arc_name] = checksum
                    total_size += file_size

                # Get user list from sanitized database copy
                users = []
                try:
                    conn = sqlite3.connect(str(temp_db_path))
                    cursor = conn.execute("SELECT username FROM users")
                    users = [row[0] for row in cursor.fetchall()]
                    conn.close()
                except Exception:
                    pass

                # Create and add manifest
                manifest = {
                    "version": FullBackupService.MANIFEST_VERSION,
                    "created_at": datetime.now().isoformat(),
                    "synth_gallery_version": FullBackupService._get_app_version(),
                    "checksums": checksums,
                    "encrypted": encrypted,
                    "encryption_method": "aes-256-gcm-v1" if encrypted else None,
                    "stats": {
                        "total_files": total_files,
                        "total_size_bytes": total_size,
                        "total_size_human": _format_size(total_size),
                        "users": users
                    }
                }

                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            # Rename temp to final
            temp_path.rename(backup_path)

            if progress_callback:
                progress_callback(total_files, total_files, "Backup complete")

            # Rotate old backups
            FullBackupService.rotate_full_backups()

            result = {
                "success": True,
                "filename": backup_filename,
                "path": str(backup_path),
                "size": backup_path.stat().st_size,
                "size_human": _format_size(backup_path.stat().st_size),
                "stats": manifest["stats"],
                "encrypted": encrypted,
            }
            if not encrypted:
                result["warning"] = (
                    "Backup created without encryption. "
                    "Set SYNTH_BACKUP_KEY environment variable for encrypted backups."
                )
            return result

        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            return {"success": False, "error": str(e)}
        finally:
            if temp_db_path.exists():
                temp_db_path.unlink()

    @staticmethod
    def verify_full_backup(backup_path: Path) -> dict:
        """Verify backup integrity by checking all checksums."""
        if not backup_path.exists():
            return {"valid": False, "error": "Backup file not found"}

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    return {"valid": False, "error": "Missing manifest.json"}

                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                checksums = manifest.get("checksums", {})

                errors = []
                verified = 0

                for arc_name, expected_checksum in checksums.items():
                    if arc_name not in zf.namelist():
                        errors.append(f"Missing file: {arc_name}")
                        continue

                    data = zf.read(arc_name)
                    actual = f"sha256:{hashlib.sha256(data).hexdigest()}"

                    if actual != expected_checksum:
                        errors.append(f"Checksum mismatch: {arc_name}")
                    else:
                        verified += 1

                return {
                    "valid": len(errors) == 0,
                    "verified_files": verified,
                    "total_files": len(checksums),
                    "errors": errors if errors else None,
                    "manifest": manifest
                }

        except zipfile.BadZipFile:
            return {"valid": False, "error": "Corrupted zip file"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    @staticmethod
    def restore_full_backup(backup_path: Path, progress_callback: Optional[Callable] = None) -> dict:
        """
        Restore database and media files from backup.

        WARNING: This will overwrite existing data!
        """
        verification = FullBackupService.verify_full_backup(backup_path)
        if not verification["valid"]:
            return {
                "success": False,
                "error": f"Backup verification failed: {verification.get('error', verification.get('errors'))}"
            }

        storage = get_storage()

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                files = [f for f in zf.namelist() if f != "manifest.json"]
                total = len(files)

                # Determine encryption settings
                encrypted = manifest.get("encrypted", False)
                backup_key = _get_backup_key()

                for idx, arc_name in enumerate(files):
                    if progress_callback:
                        progress_callback(idx, total, f"Restoring {arc_name}")

                    content = zf.read(arc_name)

                    if arc_name == "gallery.db.enc":
                        if not backup_key:
                            return {
                                "success": False,
                                "error": (
                                    "Backup is encrypted but SYNTH_BACKUP_KEY is not set. "
                                    "Set the environment variable to the key used during backup creation."
                                )
                            }
                        try:
                            content = _decrypt_backup_data(content, backup_key)
                        except Exception:
                            return {
                                "success": False,
                                "error": "Failed to decrypt backup. Invalid SYNTH_BACKUP_KEY."
                            }
                        # Decrypted database to local filesystem
                        target = BASE_DIR / "gallery.db"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with open(target, "wb") as f:
                            f.write(content)
                    elif arc_name == "gallery.db":
                        # Legacy unencrypted database
                        target = BASE_DIR / arc_name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with open(target, "wb") as f:
                            f.write(content)
                    elif arc_name.startswith("uploads/"):
                        # Media files to storage
                        file_id = arc_name.split("/", 1)[1]
                        asyncio.run(storage.upload(file_id, content, "uploads"))
                    elif arc_name.startswith("thumbnails/"):
                        # Thumbnails to storage
                        file_id = arc_name.split("/", 1)[1]
                        asyncio.run(storage.upload(file_id, content, "thumbnails"))

                if progress_callback:
                    progress_callback(total, total, "Restore complete")

                return {
                    "success": True,
                    "restored_files": total,
                    "manifest": manifest
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_full_backup(backup_path: Path) -> dict:
        """Delete a full backup file."""
        try:
            if not backup_path.exists():
                return {"success": False, "error": "Backup not found"}

            backup_path.unlink()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def rotate_full_backups():
        """Remove old backups, keeping only BACKUP_ROTATION_COUNT most recent."""
        backups = sorted(BACKUP_PATH.glob("backup-*.zip"), reverse=True)

        for old_backup in backups[BACKUP_ROTATION_COUNT:]:
            try:
                old_backup.unlink()
            except Exception:
                pass

    @staticmethod
    def _get_app_version() -> str:
        """Get application version from CHANGELOG.md."""
        try:
            changelog = BASE_DIR / "CHANGELOG.md"
            if changelog.exists():
                with open(changelog, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("## ["):
                            return line.split("[")[1].split("]")[0]
        except Exception:
            pass
        return "unknown"


# =============================================================================
# Backup Scheduler
# =============================================================================

class BackupScheduler:
    """Background scheduler for automatic backups."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_backup: Optional[datetime] = None

    def start(self):
        """Start the backup scheduler thread."""
        if BACKUP_SCHEDULE == "disabled":
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the backup scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            try:
                if self._should_backup():
                    result = FullBackupService.create_full_backup()
                    if result["success"]:
                        self._last_backup = datetime.now()
            except Exception:
                pass

            # Check every hour
            self._stop_event.wait(3600)

    def _should_backup(self) -> bool:
        """Determine if a backup should be created now."""
        if BACKUP_SCHEDULE == "disabled":
            return False

        backups = FullBackupService.list_full_backups()
        if backups:
            try:
                last_backup_time = datetime.fromisoformat(backups[0]["created_at"])
                hours_since = (datetime.now() - last_backup_time).total_seconds() / 3600

                if BACKUP_SCHEDULE == "daily":
                    return hours_since >= 24
                elif BACKUP_SCHEDULE == "weekly":
                    return hours_since >= 168
            except Exception:
                pass

        return True

    @property
    def status(self) -> dict:
        """Get scheduler status."""
        backups = FullBackupService.list_full_backups()
        return {
            "enabled": BACKUP_SCHEDULE != "disabled",
            "schedule": BACKUP_SCHEDULE,
            "running": self._thread.is_alive() if self._thread else False,
            "last_backup": backups[0]["created_at"] if backups else None,
            "next_backup_approx": self._calculate_next_backup(backups)
        }

    def _calculate_next_backup(self, backups: list) -> Optional[str]:
        """Estimate next backup time."""
        if BACKUP_SCHEDULE == "disabled" or not backups:
            return None

        try:
            last = datetime.fromisoformat(backups[0]["created_at"])
            if BACKUP_SCHEDULE == "daily":
                next_time = datetime.fromtimestamp(last.timestamp() + 86400)
            elif BACKUP_SCHEDULE == "weekly":
                next_time = datetime.fromtimestamp(last.timestamp() + 604800)
            else:
                return None
            return next_time.isoformat()
        except Exception:
            return None


# Global scheduler instance
backup_scheduler = BackupScheduler()
