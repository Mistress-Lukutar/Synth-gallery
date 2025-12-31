"""Database backup service."""
import shutil
from datetime import datetime
from pathlib import Path

from ..config import BASE_DIR

DATABASE_PATH = BASE_DIR / "gallery.db"
BACKUPS_DIR = BASE_DIR / "backups"
MAX_BACKUPS = 5


def ensure_backups_dir():
    """Create backups directory if it doesn't exist."""
    BACKUPS_DIR.mkdir(exist_ok=True)


def create_backup(reason: str = "manual") -> str | None:
    """Create a database backup.

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

    # Rotate old backups
    rotate_backups()

    return filename


def list_backups() -> list[dict]:
    """List all backups with metadata.

    Returns:
        List of backup info dicts: {name, size, created_at, reason}
    """
    ensure_backups_dir()

    backups = []
    for file in BACKUPS_DIR.glob("gallery_*.db"):
        stat = file.stat()

        # Parse reason from filename: gallery_YYYYMMDD_HHMMSS_reason.db
        parts = file.stem.split("_")
        reason = parts[3] if len(parts) > 3 else "unknown"

        # Parse date from filename
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

    # Sort by date, newest first
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups


def get_backup_path(filename: str) -> Path | None:
    """Get backup file path if it exists.

    Args:
        filename: Backup filename

    Returns:
        Path to backup file or None if not found
    """
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return None

    backup_path = BACKUPS_DIR / filename
    if backup_path.exists() and backup_path.is_file():
        return backup_path
    return None


def restore_backup(filename: str) -> bool:
    """Restore database from backup.

    Creates a backup of current database before restoring.

    Args:
        filename: Backup filename to restore

    Returns:
        True if successful, False otherwise
    """
    backup_path = get_backup_path(filename)
    if not backup_path:
        return False

    # Create backup of current database before restoring
    if DATABASE_PATH.exists():
        create_backup("pre-restore")

    shutil.copy2(backup_path, DATABASE_PATH)
    return True


def delete_backup(filename: str) -> bool:
    """Delete a backup file.

    Args:
        filename: Backup filename to delete

    Returns:
        True if deleted, False otherwise
    """
    backup_path = get_backup_path(filename)
    if not backup_path:
        return False

    backup_path.unlink()
    return True


def rotate_backups(keep: int = MAX_BACKUPS):
    """Delete old backups, keeping only the most recent ones.

    Args:
        keep: Number of backups to keep
    """
    backups = list_backups()

    if len(backups) <= keep:
        return

    # Delete oldest backups
    for backup in backups[keep:]:
        backup_path = BACKUPS_DIR / backup["name"]
        if backup_path.exists():
            backup_path.unlink()


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
