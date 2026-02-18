"""Safe file service - handles file operations in encrypted safes.

This service encapsulates business logic for accessing files
in end-to-end encrypted safes.
"""
import base64
from pathlib import Path
from typing import Optional, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import SafeRepository, PhotoRepository
from ...config import UPLOADS_DIR, THUMBNAILS_DIR


class SafeFileService:
    """Service for safe file operations.
    
    Responsibilities:
    - Safe photo key retrieval
    - Safe file access validation
    - Safe thumbnail operations
    - Safe file metadata
    """
    
    def __init__(
        self,
        safe_repository: SafeRepository,
        photo_repository: Optional[PhotoRepository] = None
    ):
        self.safe_repo = safe_repository
        self.photo_repo = photo_repository
    
    def get_photo_key(
        self,
        photo_id: str,
        user_id: int,
        can_access_photo_fn: callable
    ) -> Dict:
        """Get encrypted content key for a photo in a safe.
        
        The user must have an active safe session to retrieve the key.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            can_access_photo_fn: Function to check photo access permission
            
        Returns:
            Key data dict
            
        Raises:
            HTTPException: If no access, not in safe, or safe locked
        """
        # Check photo access
        if not can_access_photo_fn(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not self.photo_repo:
            raise HTTPException(status_code=500, detail="Photo repository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Check if photo is in a safe
        if not photo.get("safe_id"):
            raise HTTPException(status_code=400, detail="Photo is not in a safe")
        
        safe_id = photo["safe_id"]
        safe = self.safe_repo.get_by_id(safe_id)
        
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if safe is unlocked (has active session)
        if not self.safe_repo.is_unlocked(safe_id, user_id):
            raise HTTPException(status_code=403, detail="Safe is locked")
        
        # Get the session
        session = self.safe_repo.get_unlock_session(safe_id, user_id)
        
        if not session:
            raise HTTPException(status_code=403, detail="Safe session expired")
        
        # Return the encrypted content key and session data
        # Note: In a full implementation, we'd have a separate content key for each file
        # For now, the client uses the safe DEK directly for files in the safe
        return {
            "photo_id": photo_id,
            "safe_id": safe_id,
            "session_id": session["id"],
            "encrypted_dek": base64.b64encode(session["encrypted_dek"]).decode(),
            "storage_mode": "safe_e2e"
        }
    
    def get_photo_file_path(
        self,
        photo_id: str,
        user_id: int,
        can_access_photo_fn: callable
    ) -> Path:
        """Get file path for a photo in a safe.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            can_access_photo_fn: Function to check photo access permission
            
        Returns:
            File path
            
        Raises:
            HTTPException: If no access, not found, or not in safe
        """
        # Check photo access
        if not can_access_photo_fn(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not self.photo_repo:
            raise HTTPException(status_code=500, detail="Photo repository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Check if photo is in a safe
        if not photo.get("safe_id"):
            # Not in safe - redirect to regular endpoint
            raise HTTPException(
                status_code=400, 
                detail="Use regular /uploads endpoint"
            )
        
        safe_id = photo["safe_id"]
        safe = self.safe_repo.get_by_id(safe_id)
        
        if not safe or safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Return file path
        file_path = UPLOADS_DIR / photo["filename"]
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        return file_path
    
    def get_photo_thumbnail_path(
        self,
        photo_id: str,
        user_id: int,
        can_access_photo_fn: callable
    ) -> Dict:
        """Get thumbnail info for a photo in a safe.
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            can_access_photo_fn: Function to check photo access permission
            
        Returns:
            Dict with path or regenerate info
            
        Raises:
            HTTPException: If no access or not found
        """
        # Check photo access
        if not can_access_photo_fn(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not self.photo_repo:
            raise HTTPException(status_code=500, detail="Photo repository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Check if photo is in a safe
        if not photo.get("safe_id"):
            raise HTTPException(
                status_code=400, 
                detail="Use regular /thumbnails endpoint"
            )
        
        safe_id = photo["safe_id"]
        safe = self.safe_repo.get_by_id(safe_id)
        
        if not safe or safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if thumbnail exists
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        
        if not thumb_path.exists():
            # Thumbnail missing - check if safe is unlocked (client can regenerate)
            safe_unlocked = self.safe_repo.is_unlocked(safe_id, user_id)
            
            if safe_unlocked:
                # Safe is unlocked, client can regenerate the thumbnail
                # Return info to trigger client-side regeneration
                return {
                    "exists": False,
                    "regenerate": True,
                    "photo_id": photo_id,
                    "safe_id": safe_id,
                    "original_endpoint": f"/api/safe-files/photos/{photo_id}/file"
                }
            else:
                # Safe is locked, client cannot regenerate
                raise HTTPException(
                    status_code=404, 
                    detail="Thumbnail not found and safe is locked"
                )
        
        # Thumbnail exists
        return {
            "exists": True,
            "path": thumb_path,
            "safe_id": safe_id
        }
    
    def upload_thumbnail(
        self,
        photo_id: str,
        user_id: int,
        thumbnail_content: bytes,
        thumb_width: int,
        thumb_height: int,
        can_access_photo_fn: callable,
        update_dimensions_fn: callable
    ) -> Dict:
        """Upload a thumbnail for a photo in a safe.
        
        This is used for client-side thumbnail regeneration when the thumbnail
        is missing on the server but the client has the safe unlocked and can
        regenerate it from the original file.
        
        The thumbnail must be encrypted with the safe's DEK (same as the main file).
        
        Args:
            photo_id: Photo ID
            user_id: User ID
            thumbnail_content: Thumbnail file content
            thumb_width: Thumbnail width
            thumb_height: Thumbnail height
            can_access_photo_fn: Function to check photo access permission
            update_dimensions_fn: Function to update photo dimensions
            
        Returns:
            Success message
            
        Raises:
            HTTPException: On validation errors
        """
        # Check photo access
        if not can_access_photo_fn(photo_id, user_id):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not self.photo_repo:
            raise HTTPException(status_code=500, detail="Photo repository not configured")
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Check if photo is in a safe
        if not photo.get("safe_id"):
            raise HTTPException(status_code=400, detail="Photo is not in a safe")
        
        safe_id = photo["safe_id"]
        safe = self.safe_repo.get_by_id(safe_id)
        
        if not safe or safe["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if safe is unlocked
        if not self.safe_repo.is_unlocked(safe_id, user_id):
            raise HTTPException(
                status_code=403, 
                detail="Safe is locked. Please unlock first."
            )
        
        # Save the encrypted thumbnail
        thumb_path = THUMBNAILS_DIR / f"{photo_id}.jpg"
        
        with open(thumb_path, "wb") as f:
            f.write(thumbnail_content)
        
        # Update thumbnail dimensions in database
        update_dimensions_fn(photo_id, thumb_width, thumb_height)
        
        return {
            "success": True,
            "message": "Thumbnail uploaded successfully",
            "photo_id": photo_id
        }
