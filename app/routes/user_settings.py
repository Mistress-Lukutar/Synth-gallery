"""User settings routes - profile, password, recovery key."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import ROOT_PATH
from ..database import create_connection
from ..dependencies import require_user, get_csrf_token
from ..infrastructure.repositories import UserRepository, FolderRepository, PermissionRepository
from ..application.services import UserSettingsService

router = APIRouter()


def get_user_settings_service() -> UserSettingsService:
    """Create UserSettingsService with repositories."""
    db = create_connection()
    return UserSettingsService(
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db),
        user_repository=UserRepository(db)
    )


# ============================================================================
# Profile Settings
# ============================================================================

class UpdateDisplayNameRequest(BaseModel):
    display_name: str


@router.post("/api/user/profile/display-name")
def update_display_name(request: Request, data: UpdateDisplayNameRequest):
    """Update user's display name."""
    user = require_user(request)
    
    if not data.display_name or len(data.display_name.strip()) < 1:
        raise HTTPException(status_code=400, detail="Display name is required")
    
    db = create_connection()
    try:
        service = get_user_settings_service()
        success = service.update_display_name(user["id"], data.display_name.strip())
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update display name")
        
        return {"status": "ok", "display_name": data.display_name.strip()}
    finally:
        db.close()


# ============================================================================
# Password Change
# ============================================================================

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/user/profile/change-password")
def change_password(request: Request, data: ChangePasswordRequest):
    """Change user password."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_user_settings_service()
        success = service.change_password(
            user["id"],
            data.old_password,
            data.new_password
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to change password")
        
        return {"status": "ok", "message": "Password changed successfully"}
    finally:
        db.close()


# ============================================================================
# Recovery Key
# ============================================================================

class GenerateRecoveryKeyRequest(BaseModel):
    password: str


@router.get("/api/user/recovery-key/status")
def recovery_key_status(request: Request):
    """Check if user has recovery key configured."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_user_settings_service()
        has_key = service.has_recovery_key(user["id"])
        
        return {"has_recovery_key": has_key}
    finally:
        db.close()


@router.post("/api/user/recovery-key/generate")
def generate_recovery_key(request: Request, data: GenerateRecoveryKeyRequest):
    """Generate recovery key for user.
    
    Returns the recovery key which should be shown ONCE to the user.
    """
    user = require_user(request)
    
    db = create_connection()
    try:
        service = get_user_settings_service()
        recovery_key = service.generate_recovery_key(user["id"], data.password)
        
        return {
            "status": "ok",
            "recovery_key": recovery_key,
            "warning": "Save this key in a secure location! It is shown ONLY ONCE!"
        }
    finally:
        db.close()
