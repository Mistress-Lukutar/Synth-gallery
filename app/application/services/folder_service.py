"""Folder service - handles folder management operations.

This service encapsulates business logic for creating, updating, and managing folders,
including safe folder operations and permission checks.
"""
from typing import Optional, List, Dict

from fastapi import HTTPException

from ...infrastructure.repositories import FolderRepository, SafeRepository, PhotoRepository
from ...database import get_folder_tree as db_get_folder_tree


class FolderService:
    """Service for folder management operations.
    
    Responsibilities:
    - Folder CRUD operations
    - Safe folder creation and validation
    - Parent-child folder relationships
    - User folder tree retrieval
    """
    
    def __init__(
        self,
        folder_repository: FolderRepository,
        safe_repository: Optional[SafeRepository] = None,
        photo_repository: Optional[PhotoRepository] = None
    ):
        self.folder_repo = folder_repository
        self.safe_repo = safe_repository
        self.photo_repo = photo_repository
    
    def create_folder(
        self,
        name: str,
        user_id: int,
        parent_id: Optional[str] = None,
        safe_id: Optional[str] = None
    ) -> dict:
        """Create a new folder.
        
        Args:
            name: Folder name
            user_id: Owner user ID
            parent_id: Optional parent folder ID
            safe_id: Optional safe ID (if creating inside a safe)
            
        Returns:
            Created folder dict
            
        Raises:
            HTTPException: On validation errors or permission issues
        """
        if safe_id:
            # Creating folder inside a safe
            return self._create_safe_folder(
                name, user_id, safe_id, parent_id
            )
        else:
            # Regular folder creation
            return self._create_regular_folder(
                name, user_id, parent_id
            )
    
    def _create_safe_folder(
        self,
        name: str,
        user_id: int,
        safe_id: str,
        parent_id: Optional[str] = None
    ) -> dict:
        """Create a folder inside an encrypted safe."""
        if not self.safe_repo:
            raise HTTPException(status_code=500, detail="Safe repository not configured")
        
        # Verify safe exists and user owns it
        safe = self.safe_repo.get_by_folder_id(safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        # Note: Safe ownership check should be done at route level
        # This service focuses on business logic, not auth
        
        # Validate parent if provided
        if parent_id:
            parent = self.folder_repo.get_by_id(parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent folder not found")
            # Parent must be in the same safe
            if parent.get("safe_id") != safe_id:
                raise HTTPException(
                    status_code=403,
                    detail="Parent folder must be in the same safe"
                )
        
        # Create the folder (repository generates UUID)
        folder_id = self.folder_repo.create(
            name=name,
            user_id=user_id,
            parent_id=parent_id
        )
        
        return self.folder_repo.get_by_id(folder_id)
    
    def _create_regular_folder(
        self,
        name: str,
        user_id: int,
        parent_id: Optional[str] = None
    ) -> dict:
        """Create a regular (non-safe) folder."""
        # Validate parent if provided
        if parent_id:
            parent = self.folder_repo.get_by_id(parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent folder not found")
            
            # Cannot create subfolder in another user's folder
            if parent["user_id"] != user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot create folder in another user's folder"
                )
            
            # Cannot create subfolder outside of safe if parent is in safe
            if parent.get("safe_id"):
                raise HTTPException(
                    status_code=403,
                    detail="Cannot create subfolder outside of safe"
                )
        
        # Create the folder (repository generates UUID)
        folder_id = self.folder_repo.create(
            name=name,
            user_id=user_id,
            parent_id=parent_id
        )
        
        return self.folder_repo.get_by_id(folder_id)
    
    def update_folder(self, folder_id: str, name: str, user_id: int) -> dict:
        """Update folder name.
        
        Args:
            folder_id: Folder ID to update
            name: New name
            user_id: User making the request (must be owner)
            
        Returns:
            Updated folder dict
            
        Raises:
            HTTPException: If folder not found or user not owner
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")
        
        self.folder_repo.update_name(folder_id, name)
        return self.folder_repo.get_by_id(folder_id)
    
    def delete_folder(self, folder_id: str, user_id: int) -> List[str]:
        """Delete a folder and all its contents.
        
        Args:
            folder_id: Folder ID to delete
            user_id: User making the request (must be owner)
            
        Returns:
            List of filenames that were deleted
            
        Raises:
            HTTPException: If folder not found or user not owner
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")
        
        # Get all photos in folder for file cleanup
        filenames = []
        if self.photo_repo:
            photos = self.photo_repo.get_by_folder(folder_id, include_subfolders=True)
            filenames = [p["filename"] for p in photos]
        
        # Delete folder (cascade will handle photos in DB)
        self.folder_repo.delete(folder_id)
        
        return filenames
    
    def get_folder_tree(self, user_id: int) -> List[dict]:
        """Get folder tree for user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of folders with metadata
        """
        return db_get_folder_tree(user_id)
    
    def get_breadcrumbs(self, folder_id: str) -> List[dict]:
        """Get breadcrumb path from root to folder.
        
        Args:
            folder_id: Target folder ID
            
        Returns:
            List of {id, name} dicts from root to target
        """
        breadcrumbs = []
        current_id = folder_id
        
        while current_id:
            folder = self.folder_repo.get_by_id(current_id)
            if not folder:
                break
            
            breadcrumbs.insert(0, {"id": folder["id"], "name": folder["name"]})
            current_id = folder.get("parent_id")
        
        return breadcrumbs
    
    def move_folder(
        self,
        folder_id: str,
        new_parent_id: Optional[str],
        user_id: int
    ) -> bool:
        """Move folder to new parent.
        
        Args:
            folder_id: Folder to move
            new_parent_id: New parent folder ID (None for root)
            user_id: User making the request
            
        Returns:
            True if successful
            
        Raises:
            HTTPException: On validation errors
        """
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")
        
        # Validate new parent
        if new_parent_id:
            parent = self.folder_repo.get_by_id(new_parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent folder not found")
            
            if parent["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Cannot move to another user's folder")
            
            # Check for circular reference
            if self._is_descendant(new_parent_id, folder_id):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot move folder into its own subfolder"
                )
        
        return self.folder_repo.move(folder_id, new_parent_id)
    
    def _is_descendant(self, potential_descendant: str, ancestor: str) -> bool:
        """Check if potential_descendant is a descendant of ancestor."""
        current_id = potential_descendant
        
        while current_id:
            if current_id == ancestor:
                return True
            
            folder = self.folder_repo.get_by_id(current_id)
            if not folder:
                return False
            
            current_id = folder.get("parent_id")
        
        return False
