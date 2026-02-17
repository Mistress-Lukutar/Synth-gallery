"""Admin routes - backup management and admin-only features."""
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ..config import BACKUP_PATH, ROOT_PATH, BASE_DIR
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH
from ..database import is_user_admin
from ..dependencies import get_current_user, require_user, get_csrf_token
from ..services.backup import (
    create_backup, list_backups, get_backup_path,
    restore_backup, delete_backup,
    FullBackupService, backup_scheduler
)
from ..services.thumbnail import (
    cleanup_orphaned_thumbnails, regenerate_missing_thumbnails,
    get_thumbnail_stats
)

router = APIRouter()


def require_admin(request: Request):
    """Check if current user is admin. Raises 403 if not."""
    user = require_user(request)
    if not is_user_admin(user["id"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# === Admin Pages ===

@router.get("/admin")
def admin_index(request: Request):
    """Redirect to backups page."""
    return RedirectResponse(url=f"{ROOT_PATH}/admin/backups", status_code=302)


@router.get("/admin/backups")
def backups_page(request: Request):
    """Backup management page."""
    user = require_admin(request)

    db_backups = list_backups()
    full_backups = FullBackupService.list_full_backups()
    scheduler_status = backup_scheduler.status

    return templates.TemplateResponse(
        request,
        "admin_backups.html",
        {
            "user": user,
            "backups": db_backups,
            "full_backups": full_backups,
            "scheduler_status": scheduler_status,
            "backup_path": str(BACKUP_PATH),
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH
        }
    )


# === Backup API ===

@router.post("/api/admin/backup")
def create_backup_endpoint(request: Request):
    """Create a new backup."""
    require_admin(request)

    filename = create_backup("manual")
    if not filename:
        raise HTTPException(status_code=500, detail="Failed to create backup")

    return {"status": "ok", "filename": filename}


@router.get("/api/admin/backups")
def list_backups_endpoint(request: Request):
    """List all backups."""
    require_admin(request)

    return {"backups": list_backups()}


@router.get("/api/admin/backup/{filename}/download")
def download_backup(request: Request, filename: str):
    """Download a backup file."""
    require_admin(request)

    backup_path = get_backup_path(filename)
    if not backup_path:
        raise HTTPException(status_code=404, detail="Backup not found")

    return FileResponse(
        backup_path,
        media_type="application/x-sqlite3",
        filename=filename
    )


@router.post("/api/admin/backup/{filename}/restore")
def restore_backup_endpoint(request: Request, filename: str):
    """Restore database from backup."""
    require_admin(request)

    success = restore_backup(filename)
    if not success:
        raise HTTPException(status_code=404, detail="Backup not found")

    return {"status": "ok", "message": "Database restored. Please restart the application."}


@router.delete("/api/admin/backup/{filename}")
def delete_backup_endpoint(request: Request, filename: str):
    """Delete a backup file."""
    require_admin(request)

    success = delete_backup(filename)
    if not success:
        raise HTTPException(status_code=404, detail="Backup not found")

    return {"status": "ok"}


# === Full Backup API (Database + Media) ===

@router.post("/api/admin/full-backup")
def create_full_backup_endpoint(request: Request):
    """Create a full backup of database and all media files."""
    require_admin(request)

    result = FullBackupService.create_full_backup()
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Backup failed"))

    return {
        "status": "ok",
        "filename": result["filename"],
        "size": result["size_human"],
        "stats": result["stats"]
    }


@router.get("/api/admin/full-backups")
def list_full_backups_endpoint(request: Request):
    """List all full backups."""
    require_admin(request)

    return {
        "backups": FullBackupService.list_full_backups(),
        "scheduler": backup_scheduler.status
    }


@router.get("/api/admin/full-backup/{filename}/download")
def download_full_backup(request: Request, filename: str):
    """Download a full backup file."""
    require_admin(request)

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    backup_path = BACKUP_PATH / filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    return FileResponse(
        backup_path,
        media_type="application/zip",
        filename=filename
    )


@router.get("/api/admin/full-backup/{filename}/verify")
def verify_full_backup_endpoint(request: Request, filename: str):
    """Verify a full backup's integrity."""
    require_admin(request)

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    backup_path = BACKUP_PATH / filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    result = FullBackupService.verify_full_backup(backup_path)
    return result


@router.post("/api/admin/full-backup/{filename}/restore")
def restore_full_backup_endpoint(request: Request, filename: str):
    """Restore from a full backup."""
    require_admin(request)

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    backup_path = BACKUP_PATH / filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    result = FullBackupService.restore_full_backup(backup_path)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Restore failed"))

    return {
        "status": "ok",
        "restored_files": result["restored_files"],
        "message": "Backup restored. Please restart the application."
    }


@router.delete("/api/admin/full-backup/{filename}")
def delete_full_backup_endpoint(request: Request, filename: str):
    """Delete a full backup file."""
    require_admin(request)

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    backup_path = BACKUP_PATH / filename
    result = FullBackupService.delete_full_backup(backup_path)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error", "Delete failed"))

    return {"status": "ok"}


# === Maintenance Page ===

@router.get("/admin/maintenance")
def maintenance_page(request: Request):
    """Maintenance tasks page - thumbnail management."""
    user = require_admin(request)

    stats = get_thumbnail_stats()

    return templates.TemplateResponse(
        request,
        "admin_maintenance.html",
        {
            "user": user,
            "stats": stats,
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH
        }
    )


# === Thumbnail Management API ===

@router.get("/api/admin/thumbnails/stats")
def thumbnail_stats_endpoint(request: Request):
    """Get thumbnail statistics."""
    require_admin(request)

    return get_thumbnail_stats()


@router.post("/api/admin/thumbnails/cleanup")
def cleanup_thumbnails_endpoint(request: Request):
    """Remove orphaned thumbnails (thumbnails without photos in database)."""
    require_admin(request)

    result = cleanup_orphaned_thumbnails()
    return {"status": "ok", **result}


@router.post("/api/admin/thumbnails/regenerate")
def regenerate_thumbnails_endpoint(request: Request):
    """Regenerate all missing thumbnails."""
    require_admin(request)

    result = regenerate_missing_thumbnails()
    return {"status": "ok", **result}
