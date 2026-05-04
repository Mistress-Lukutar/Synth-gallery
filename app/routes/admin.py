"""Admin routes - backup management and admin-only features."""
import secrets

import bcrypt
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from ..config import BACKUP_PATH, ROOT_PATH, BASE_DIR, EXTERNAL_HOST
from ..database import create_connection
from ..dependencies import require_user, get_csrf_token
from ..infrastructure.repositories import UserRepository, AiApiKeyRepository
from ..infrastructure.services.audit_log import (
    log_api_key_created,
    log_api_key_revoked,
)
from ..infrastructure.services.backup import (
    create_backup, list_backups, get_backup_path,
    restore_backup, delete_backup,
    FullBackupService, backup_scheduler
)
from ..infrastructure.services.thumbnail import (
    cleanup_orphaned_thumbnails, cleanup_orphaned_uploads,
    regenerate_missing_thumbnails, get_thumbnail_stats
)

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH
templates.env.globals["external_host"] = EXTERNAL_HOST

router = APIRouter()


def require_admin(request: Request):
    """Check if current user is admin. Raises 403 if not."""
    user = require_user(request)
    db = create_connection()
    try:
        user_repo = UserRepository(db)
        user_data = user_repo.get_by_id(user["id"])
        if not user_data or not user_data.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin access required")
        return user
    finally:
        db.close()


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
        "admin_backups.html",
        {
            "request": request,
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


# === User Management Page ===

@router.get("/admin/users")
def users_page(request: Request):
    """User management page."""
    user = require_admin(request)

    db = create_connection()
    try:
        user_repo = UserRepository(db)
        users = user_repo.list_all()
    finally:
        db.close()

    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH,
            "default_admin_created": request.query_params.get("default_admin") == "1"
        }
    )


# === User Management API ===

class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    password: str
    is_admin: bool = False


@router.post("/api/admin/users")
def create_user_endpoint(request: Request, data: CreateUserRequest):
    """Create a new user."""
    require_admin(request)

    if len(data.password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")

    db = create_connection()
    try:
        user_repo = UserRepository(db)

        # Check if username already exists
        existing = user_repo.get_by_username(data.username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")

        # Create user
        user_id = user_repo.create(data.username, data.password, data.display_name)

        # Set admin status if requested
        if data.is_admin:
            user_repo.set_admin(user_id, True)

        return {"status": "ok", "user_id": user_id}
    finally:
        db.close()


@router.delete("/api/admin/users/{user_id}")
def delete_user_endpoint(request: Request, user_id: int):
    """Delete a user."""
    current_user = require_admin(request)

    # Prevent self-deletion
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    db = create_connection()
    try:
        user_repo = UserRepository(db)

        # Check if user exists
        user = user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Delete user
        user_repo.delete(user_id)

        return {"status": "ok"}
    finally:
        db.close()


class SetAdminRequest(BaseModel):
    is_admin: bool


@router.post("/api/admin/users/{user_id}/admin")
def set_admin_endpoint(request: Request, user_id: int, data: SetAdminRequest):
    """Set user admin status."""
    current_user = require_admin(request)

    # Prevent removing own admin rights
    if user_id == current_user["id"] and not data.is_admin:
        raise HTTPException(status_code=400, detail="Cannot revoke your own admin rights")

    db = create_connection()
    try:
        user_repo = UserRepository(db)

        # Check if user exists
        user = user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Update admin status
        user_repo.set_admin(user_id, data.is_admin)

        return {"status": "ok", "is_admin": data.is_admin}
    finally:
        db.close()


# === Maintenance Page ===

@router.get("/admin/maintenance")
def maintenance_page(request: Request):
    """Maintenance tasks page - thumbnail management."""
    user = require_admin(request)

    stats = get_thumbnail_stats()

    return templates.TemplateResponse(
        "admin_maintenance.html",
        {
            "request": request,
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


@router.post("/api/admin/uploads/cleanup")
def cleanup_uploads_endpoint(request: Request):
    """Remove orphaned uploads (files not registered in database)."""
    require_admin(request)

    result = cleanup_orphaned_uploads()
    return {"status": "ok", **result}


@router.post("/api/admin/thumbnails/regenerate")
def regenerate_thumbnails_endpoint(request: Request):
    """Regenerate all missing thumbnails."""
    require_admin(request)

    result = regenerate_missing_thumbnails()
    return {"status": "ok", **result}


# === API Key Management Page ===

@router.get("/admin/api-keys")
def api_keys_page(request: Request):
    """API key management page."""
    user = require_admin(request)

    db = create_connection()
    try:
        user_repo = UserRepository(db)
        users = user_repo.list_all()
    finally:
        db.close()

    return templates.TemplateResponse(
        "admin_api_keys.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH,
        }
    )


# === API Key Management API ===

class CreateApiKeyRequest(BaseModel):
    name: str
    user_id: int
    expires_days: int | None = None


class CreateApiKeyResponse(BaseModel):
    status: str
    key_id: int
    api_key: str


@router.post("/api/admin/api-keys")
def create_api_key_endpoint(request: Request, data: CreateApiKeyRequest):
    """Create a new API key for AI agent access.

    The plaintext key is returned exactly once and cannot be retrieved later.
    """
    current_user = require_admin(request)

    db = create_connection()
    try:
        user_repo = UserRepository(db)
        key_repo = AiApiKeyRepository(db)

        # Validate target user exists
        target_user = user_repo.get_by_id(data.user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Generate plaintext key
        raw_key = "sg_" + secrets.token_urlsafe(32)
        key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()

        from datetime import datetime, timedelta
        expires_at = None
        if data.expires_days and data.expires_days > 0:
            expires_at = datetime.now() + timedelta(days=data.expires_days)

        key_id = key_repo.create(
            name=data.name.strip(),
            key_hash=key_hash,
            user_id=data.user_id,
            created_by=current_user["id"],
            expires_at=expires_at
        )

        log_api_key_created(
            key_id=key_id,
            key_name=data.name.strip(),
            admin_id=current_user["id"],
            user_id=data.user_id
        )

        return {
            "status": "ok",
            "key_id": key_id,
            "api_key": raw_key,
        }
    finally:
        db.close()


@router.get("/api/admin/api-keys")
def list_api_keys_endpoint(request: Request):
    """List all API keys (without hashes)."""
    require_admin(request)

    db = create_connection()
    try:
        key_repo = AiApiKeyRepository(db)
        keys = key_repo.list_all()
    finally:
        db.close()

    return {"keys": keys}


@router.delete("/api/admin/api-keys/{key_id}")
def delete_api_key_endpoint(request: Request, key_id: int):
    """Revoke (delete) an API key."""
    require_admin(request)

    db = create_connection()
    try:
        key_repo = AiApiKeyRepository(db)
        key = key_repo.get_by_id(key_id)
        if not key_repo.delete(key_id):
            raise HTTPException(status_code=404, detail="API key not found")

        log_api_key_revoked(
            key_id=key_id,
            admin_id=current_user["id"]
        )
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/api/admin/api-keys/{key_id}/toggle")
def toggle_api_key_endpoint(request: Request, key_id: int):
    """Enable or disable an API key."""
    require_admin(request)

    db = create_connection()
    try:
        key_repo = AiApiKeyRepository(db)
        key = key_repo.get_by_id(key_id)
        if not key:
            raise HTTPException(status_code=404, detail="API key not found")

        new_state = not key["is_active"]
        key_repo.set_active(key_id, new_state)
        return {"status": "ok", "is_active": new_state}
    finally:
        db.close()
