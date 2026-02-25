"""Photo service - handles photo and album management.

This service encapsulates business logic for:
- Moving photos and albums between folders
- Album management (add/remove/reorder photos)
- Album cover management
- Batch operations on photos and albums
"""
import uuid
from typing import Optional

from fastapi import HTTPException

from ...infrastructure.repositories import PhotoRepository, FolderRepository, PermissionRepository


class PhotoService:
    """Service for managing photos and albums.
    
    Responsibilities:
    - Move photos and albums between folders
    - Album photo management (add/remove/reorder)
    - Album cover management
    - Batch move operations
    """
    
    def __init__(
        self, 
        photo_repository: PhotoRepository,
        folder_repository: FolderRepository = None,
        permission_repository: PermissionRepository = None
    ):
        self.photo_repo = photo_repository
        self.folder_repo = folder_repository
        self.perm_repo = permission_repository
    
    def create_album(self, name: str, folder_id: str, photo_ids: list[str], user_id: int) -> dict:
        """Create a new album with photos.
        
        Args:
            name: Album name
            folder_id: Folder ID to create album in
            photo_ids: List of photo IDs to add to album
            user_id: User creating the album
            
        Returns:
            Created album dict
            
        Raises:
            HTTPException: If validation fails
        """
        # Check folder permissions
        if self.folder_repo:
            folder = self.folder_repo.get_by_id(folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")
            if folder["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Cannot create album in this folder")
        
        # Generate album ID
        album_id = str(uuid.uuid4())
        
        # Create album
        success = self.photo_repo.create_album(album_id, folder_id, user_id, name)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to create album")
        
        # Add photos to album
        if photo_ids:
            self.add_photos_to_album(album_id, photo_ids, user_id)
        
        # Return created album
        return {
            "id": album_id,
            "name": name,
            "folder_id": folder_id,
            "photo_count": len(photo_ids)
        }
    
    def _can_delete_photo(self, photo_id: str, user_id: int) -> bool:
        """Check if user can delete photo."""
        if not self.perm_repo or not self.folder_repo:
            # Fallback to checking photo ownership
            photo = self.photo_repo.get_by_id(photo_id)
            if not photo:
                return False
            if photo["user_id"] == user_id:
                return True
            return False
        
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            return False
        
        # Photo owner can always delete
        if photo["user_id"] == user_id:
            return True
        
        # Check folder permissions
        if photo.get("folder_id"):
            folder = self.folder_repo.get_by_id(photo["folder_id"])
            if folder:
                # Folder owner can delete any photo
                if folder["user_id"] == user_id:
                    return True
        
        return False
    
    def _can_delete_album(self, album_id: str, user_id: int) -> bool:
        """Check if user can delete album."""
        album = self.photo_repo.get_album(album_id)
        if not album:
            return False
        
        # Album owner can always delete
        if album["user_id"] == user_id:
            return True
        
        # Check folder permissions
        if album.get("folder_id"):
            folder = self.folder_repo.get_by_id(album["folder_id"])
            if folder and folder["user_id"] == user_id:
                return True
        
        return False
    
    def _can_edit_folder(self, folder_id: str, user_id: int) -> bool:
        """Check if user can edit folder."""
        if not self.perm_repo:
            return False
        return self.perm_repo.can_edit(folder_id, user_id)
    
    def _can_edit_album(self, album_id: str, user_id: int) -> bool:
        """Check if user can edit album."""
        album = self.photo_repo.get_album(album_id)
        if not album:
            return False
        
        # Album owner can always edit
        if album["user_id"] == user_id:
            return True
        
        # Check folder permissions
        if album.get("folder_id") and self.perm_repo:
            return self.perm_repo.can_edit(album["folder_id"], user_id)
        
        return False
    
    def _move_photo_to_folder(self, photo_id: str, folder_id: str) -> bool:
        """Move photo to folder."""
        return self.photo_repo.move_to_folder(photo_id, folder_id)
    
    def _move_album_to_folder(self, album_id: str, folder_id: str) -> bool:
        """Move album to folder."""
        return self.photo_repo.move_album_to_folder(album_id, folder_id)
    
    def move_photo(self, photo_id: str, dest_folder_id: str, user_id: int) -> dict:
        """Move a standalone photo to another folder.
        
        Args:
            photo_id: Photo UUID to move
            dest_folder_id: Destination folder UUID
            user_id: User performing the move
            
        Returns:
            Dict with status and message
            
        Raises:
            HTTPException: If photo not found, no permission, or move failed
        """
        # Get photo info
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail="Photo not found")
        
        # Cannot move photos that are in albums
        if photo.get("album_id"):
            raise HTTPException(
                status_code=400, 
                detail="Cannot move photo in album. Move the album instead."
            )
        
        # Check source permission
        if not self._can_delete_photo(photo_id, user_id):
            raise HTTPException(status_code=403, detail="No permission to move this photo")
        
        # Check destination permission
        if not self._can_edit_folder(dest_folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        # Check if already in target folder
        if photo.get("folder_id") == dest_folder_id:
            return {"status": "ok", "message": "Photo already in this folder"}
        
        # Move photo
        success = self._move_photo_to_folder(photo_id, dest_folder_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to move photo")
        
        return {"status": "ok"}
    
    def move_album(self, album_id: str, dest_folder_id: str, user_id: int) -> dict:
        """Move an album and all its photos to another folder.
        
        Args:
            album_id: Album UUID to move
            dest_folder_id: Destination folder UUID
            user_id: User performing the move
            
        Returns:
            Dict with status
            
        Raises:
            HTTPException: If album not found, no permission, or move failed
        """
        # Get album info
        album = self.photo_repo.get_album(album_id)
        
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        # Check source permission
        if not self._can_delete_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="No permission to move this album")
        
        # Check destination permission
        if not self._can_edit_folder(dest_folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        # Check if already in target folder
        if album["folder_id"] == dest_folder_id:
            return {"status": "ok", "message": "Album already in this folder"}
        
        # Move album
        success = self._move_album_to_folder(album_id, dest_folder_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to move album")
        
        return {"status": "ok"}
    
    def batch_move(
        self, 
        photo_ids: list[str], 
        album_ids: list[str], 
        dest_folder_id: str, 
        user_id: int
    ) -> dict:
        """Move multiple photos and albums to another folder.
        
        Only moves standalone photos (not in albums).
        
        Args:
            photo_ids: List of photo UUIDs to move
            album_ids: List of album UUIDs to move
            dest_folder_id: Destination folder UUID
            user_id: User performing the move
            
        Returns:
            Dict with counts of moved and skipped items
            
        Raises:
            HTTPException: If no permission on destination folder
        """
        # Check destination permission
        if not self._can_edit_folder(dest_folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        moved_photos = 0
        moved_albums = 0
        skipped_photos = 0
        skipped_albums = 0
        
        # Move photos
        for photo_id in photo_ids:
            photo = self.photo_repo.get_by_id(photo_id)
            
            if not photo:
                skipped_photos += 1
                continue
            
            # Skip photos in albums
            if photo.get("album_id"):
                skipped_photos += 1
                continue
            
            # Check source permission
            if not self._can_delete_photo(photo_id, user_id):
                skipped_photos += 1
                continue
            
            # Skip if already in target folder
            if photo.get("folder_id") == dest_folder_id:
                continue
            
            if self._move_photo_to_folder(photo_id, dest_folder_id):
                moved_photos += 1
            else:
                skipped_photos += 1
        
        # Move albums
        for album_id in album_ids:
            album = self.photo_repo.get_album(album_id)
            
            if not album:
                skipped_albums += 1
                continue
            
            # Check source permission
            if not self._can_delete_album(album_id, user_id):
                skipped_albums += 1
                continue
            
            # Skip if already in target folder
            if album["folder_id"] == dest_folder_id:
                continue
            
            if self._move_album_to_folder(album_id, dest_folder_id):
                moved_albums += 1
            else:
                skipped_albums += 1
        
        return {
            "status": "ok",
            "moved_photos": moved_photos,
            "moved_albums": moved_albums,
            "skipped_photos": skipped_photos,
            "skipped_albums": skipped_albums
        }
    
    # === Album Management ===
    
    def add_photos_to_album(self, album_id: str, photo_ids: list[str], user_id: int) -> int:
        """Add photos to album.
        
        Args:
            album_id: Album UUID
            photo_ids: List of photo UUIDs to add
            user_id: User performing the action
            
        Returns:
            Number of photos added
            
        Raises:
            HTTPException: If no edit permission on album
        """
        if not self._can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        count = 0
        for photo_id in photo_ids:
            if self.photo_repo.add_to_album(photo_id, album_id):
                count += 1
        return count
    
    def remove_photos_from_album(self, album_id: str, photo_ids: list[str], user_id: int) -> int:
        """Remove photos from album. Photos stay in folder.
        
        Args:
            album_id: Album UUID
            photo_ids: List of photo UUIDs to remove
            user_id: User performing the action
            
        Returns:
            Number of photos removed
            
        Raises:
            HTTPException: If no edit permission on album
        """
        if not self._can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        count = 0
        for photo_id in photo_ids:
            if self.photo_repo.remove_from_album(photo_id):
                count += 1
        return count
    
    def reorder_album_photos(self, album_id: str, photo_ids: list[str], user_id: int) -> dict:
        """Reorder photos in album.
        
        Args:
            album_id: Album UUID
            photo_ids: List of photo UUIDs in new order
            user_id: User performing the action
            
        Returns:
            Dict with status
            
        Raises:
            HTTPException: If no edit permission or invalid photo IDs
        """
        if not self._can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        success = self.photo_repo.reorder_album(album_id, photo_ids)
        if not success:
            raise HTTPException(status_code=400, detail="Invalid photo IDs")
        
        return {"status": "ok"}
    
    def set_album_cover(self, album_id: str, photo_id: Optional[str], user_id: int) -> dict:
        """Set album cover photo.
        
        Args:
            album_id: Album UUID
            photo_id: Photo UUID for cover (None to reset to default)
            user_id: User performing the action
            
        Returns:
            Dict with status
            
        Raises:
            HTTPException: If no edit permission or photo not in album
        """
        if not self._can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        success = self.photo_repo.set_album_cover(album_id, photo_id)
        if not success and photo_id:
            raise HTTPException(status_code=400, detail="Photo not in album")
        
        return {"status": "ok"}
    
    def move_photos_to_folder(
        self, 
        photo_ids: list[str], 
        folder_id: str, 
        user_id: int
    ) -> dict:
        """Move multiple photos to a folder.
        
        Args:
            photo_ids: List of photo UUIDs
            folder_id: Target folder ID
            user_id: User performing the action
            
        Returns:
            Dict with status and count
            
        Raises:
            HTTPException: If no edit permission
        """
        if not self.perm_repo:
            raise HTTPException(status_code=500, detail="Permission repository not configured")
        
        if not self.perm_repo.can_edit(folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this folder")
        
        moved = 0
        failed = []
        
        for photo_id in photo_ids:
            photo = self.photo_repo.get_by_id(photo_id)
            if not photo:
                failed.append({"photo_id": photo_id, "error": "Photo not found"})
                continue
            
            # Check permission on source folder
            if photo.get("folder_id") and not self.perm_repo.can_edit(photo["folder_id"], user_id):
                failed.append({"photo_id": photo_id, "error": "Cannot move from this folder"})
                continue
            
            try:
                self.photo_repo.move_to_folder(photo_id, folder_id)
                moved += 1
            except Exception as e:
                failed.append({"photo_id": photo_id, "error": str(e)})
        
        return {
            "status": "ok",
            "moved": moved,
            "failed": failed
        }
    
    def move_albums_to_folder(
        self, 
        album_ids: list[str], 
        folder_id: str, 
        user_id: int
    ) -> dict:
        """Move multiple albums to a folder.
        
        Args:
            album_ids: List of album UUIDs
            folder_id: Target folder ID
            user_id: User performing the action
            
        Returns:
            Dict with status and count
            
        Raises:
            HTTPException: If no edit permission
        """
        if not self.perm_repo or not self.folder_repo:
            raise HTTPException(status_code=500, detail="Required repositories not configured")
        
        if not self.perm_repo.can_edit(folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this folder")
        
        moved = 0
        failed = []
        
        for album_id in album_ids:
            album = self.photo_repo.get_album(album_id)
            if not album:
                failed.append({"album_id": album_id, "error": "Album not found"})
                continue
            
            # Check permission on source folder
            if album.get("folder_id") and not self.perm_repo.can_edit(album["folder_id"], user_id):
                failed.append({"album_id": album_id, "error": "Cannot move from this folder"})
                continue
            
            try:
                self.photo_repo.move_album_to_folder(album_id, folder_id)
                moved += 1
            except Exception as e:
                failed.append({"album_id": album_id, "error": str(e)})
        
        return {
            "status": "ok",
            "moved": moved,
            "failed": failed
        }
