"""Photo service - handles photo and album management.

This service encapsulates business logic for:
- Moving photos and albums between folders
- Album management (add/remove/reorder photos)
- Album cover management
- Batch operations on photos and albums
"""
from typing import Optional, Tuple
from fastapi import HTTPException

from ...database import (
    can_delete_photo, can_delete_album, can_edit_folder, can_edit_album,
    move_photo_to_folder, move_album_to_folder
)
from ...infrastructure.repositories import PhotoRepository


class PhotoService:
    """Service for managing photos and albums.
    
    Responsibilities:
    - Move photos and albums between folders
    - Album photo management (add/remove/reorder)
    - Album cover management
    - Batch move operations
    """
    
    def __init__(self, photo_repository: PhotoRepository):
        self.photo_repo = photo_repository
    
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
        if not can_delete_photo(photo_id, user_id):
            raise HTTPException(status_code=403, detail="No permission to move this photo")
        
        # Check destination permission
        if not can_edit_folder(dest_folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        # Check if already in target folder
        if photo.get("folder_id") == dest_folder_id:
            return {"status": "ok", "message": "Photo already in this folder"}
        
        # Move photo
        success = move_photo_to_folder(photo_id, dest_folder_id)
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
        album = self.photo_repo._execute(
            "SELECT folder_id, user_id FROM albums WHERE id = ?",
            (album_id,)
        ).fetchone()
        
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        # Check source permission
        if not can_delete_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="No permission to move this album")
        
        # Check destination permission
        if not can_edit_folder(dest_folder_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot move to this folder")
        
        # Check if already in target folder
        if album["folder_id"] == dest_folder_id:
            return {"status": "ok", "message": "Album already in this folder"}
        
        # Move album
        success = move_album_to_folder(album_id, dest_folder_id)
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
        if not can_edit_folder(dest_folder_id, user_id):
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
            if not can_delete_photo(photo_id, user_id):
                skipped_photos += 1
                continue
            
            # Skip if already in target folder
            if photo.get("folder_id") == dest_folder_id:
                continue
            
            if move_photo_to_folder(photo_id, dest_folder_id):
                moved_photos += 1
            else:
                skipped_photos += 1
        
        # Move albums
        for album_id in album_ids:
            album = self.photo_repo._execute(
                "SELECT folder_id FROM albums WHERE id = ?",
                (album_id,)
            ).fetchone()
            
            if not album:
                skipped_albums += 1
                continue
            
            # Check source permission
            if not can_delete_album(album_id, user_id):
                skipped_albums += 1
                continue
            
            # Skip if already in target folder
            if album["folder_id"] == dest_folder_id:
                continue
            
            if move_album_to_folder(album_id, dest_folder_id):
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
        if not can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        from ...database import add_photos_to_album
        return add_photos_to_album(album_id, photo_ids)
    
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
        if not can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        from ...database import remove_photos_from_album
        return remove_photos_from_album(album_id, photo_ids)
    
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
        if not can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        if not photo_ids:
            raise HTTPException(status_code=400, detail="No photos specified")
        
        from ...database import reorder_album_photos
        success = reorder_album_photos(album_id, photo_ids)
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
        if not can_edit_album(album_id, user_id):
            raise HTTPException(status_code=403, detail="Cannot edit this album")
        
        from ...database import set_album_cover
        success = set_album_cover(album_id, photo_id)
        if not success and photo_id:
            raise HTTPException(status_code=400, detail="Photo not in album")
        
        return {"status": "ok"}
