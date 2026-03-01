"""Album service - content-agnostic album management.

Albums can contain any type of items (media, notes, etc.).
"""
import uuid
from typing import List, Dict, Optional

from fastapi import HTTPException

from ...infrastructure.repositories import AlbumRepository, ItemRepository, FolderRepository


class AlbumService:
    """Service for managing albums.
    
    Responsibilities:
    - Create/delete albums
    - Add/remove/reorder items in albums
    - Manage album cover
    - Move albums between folders
    
    Albums are content-agnostic - they can contain any item types.
    """
    
    def __init__(
        self,
        album_repository: AlbumRepository,
        item_repository: ItemRepository,
        folder_repository: FolderRepository
    ):
        self.album_repo = album_repository
        self.item_repo = item_repository
        self.folder_repo = folder_repository
    
    # ========================================================================
    # Album CRUD
    # ========================================================================
    
    def create_album(
        self,
        name: str,
        folder_id: str,
        user_id: int,
        item_ids: List[str] = None,
        safe_id: str = None
    ) -> Dict:
        """Create a new album with optional items.
        
        Args:
            name: Album name
            folder_id: Parent folder
            user_id: Owner
            item_ids: Optional item IDs to add
            safe_id: Optional safe ID
            
        Returns:
            Created album dict
        """
        # Check permissions
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(404, "Folder not found")
        
        if folder["user_id"] != user_id:
            raise HTTPException(403, "Cannot create album here")
        
        # Validate all items exist and are in same folder/safe
        if item_ids:
            for item_id in item_ids:
                item = self.item_repo.get_by_id(item_id)
                if not item:
                    raise HTTPException(400, f"Item not found: {item_id}")
                if item['folder_id'] != folder_id:
                    raise HTTPException(400, f"Item not in folder: {item_id}")
                if item.get('safe_id') != safe_id:
                    raise HTTPException(400, f"Item safe mismatch: {item_id}")
        
        # Create album
        album_id = self.album_repo.create(
            folder_id=folder_id,
            user_id=user_id,
            name=name,
            safe_id=safe_id
        )
        
        # Add items
        if item_ids:
            for position, item_id in enumerate(item_ids):
                self.album_repo.add_item(album_id, item_id, position)
        
        return {
            'id': album_id,
            'name': name,
            'folder_id': folder_id,
            'item_count': len(item_ids) if item_ids else 0,
            'photo_count': len(item_ids) if item_ids else 0  # Legacy alias
        }
    
    def get_album(self, album_id: str, user_id: int) -> Optional[Dict]:
        """Get album with items."""
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return None
        
        # Check access
        if not self._can_view(album_id, user_id):
            raise HTTPException(403, "Access denied")
        
        # Get items with full data
        items = self.album_repo.get_items(album_id)
        
        album['items'] = items
        album['photos'] = items  # Legacy alias for backward compatibility
        album['photo_count'] = len(items)  # Legacy alias
        album['item_count'] = len(items)   # New name
        album['can_edit'] = self._can_edit(album_id, user_id)
        
        return album
    
    def delete_album(self, album_id: str, user_id: int) -> bool:
        """Delete album (items stay in folder)."""
        if not self._can_delete(album_id, user_id):
            raise HTTPException(403, "Cannot delete this album")
        
        return self.album_repo.delete(album_id)
    
    def move_album(self, album_id: str, dest_folder_id: str, user_id: int) -> bool:
        """Move album and its items to different folder."""
        album = self.album_repo.get_by_id(album_id)
        if not album:
            raise HTTPException(404, "Album not found")
        
        # Check source permission
        if not self._can_delete(album_id, user_id):
            raise HTTPException(403, "No permission to move")
        
        # Check destination permission
        dest_folder = self.folder_repo.get_by_id(dest_folder_id)
        if not dest_folder:
            raise HTTPException(404, "Destination folder not found")
        
        if dest_folder["user_id"] != user_id:
            # Check edit permission via folder sharing
            raise HTTPException(403, "Cannot move to this folder")
        
        # Check safe consistency
        if album.get('safe_id') != dest_folder.get('safe_id'):
            raise HTTPException(400, "Cannot move between different safes")
        
        # Move album
        return self.album_repo.move_to_folder(album_id, dest_folder_id)
    
    # ========================================================================
    # Item Management
    # ========================================================================
    
    def add_items(self, album_id: str, item_ids: List[str], user_id: int) -> int:
        """Add items to album.
        
        Args:
            album_id: Album ID
            item_ids: Item IDs to add
            user_id: User performing action
            
        Returns:
            Number of items added
        """
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, "Cannot edit album")
        
        album = self.album_repo.get_by_id(album_id)
        
        count = 0
        for item_id in item_ids:
            item = self.item_repo.get_by_id(item_id)
            if not item:
                continue
            
            # Verify item is in same folder/safe as album
            if item['folder_id'] != album['folder_id']:
                continue
            if item.get('safe_id') != album.get('safe_id'):
                continue
            
            if self.album_repo.add_item(album_id, item_id):
                count += 1
        
        return count
    
    def remove_items(self, album_id: str, item_ids: List[str], user_id: int) -> int:
        """Remove items from album."""
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, "Cannot edit album")
        
        count = 0
        for item_id in item_ids:
            if self.album_repo.remove_item(album_id, item_id):
                count += 1
        
        return count
    
    def reorder_items(
        self,
        album_id: str,
        item_ids: List[str],
        user_id: int
    ) -> bool:
        """Reorder items in album.
        
        Args:
            album_id: Album ID
            item_ids: Item IDs in new order
            user_id: User performing action
        """
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, "Cannot edit album")
        
        return self.album_repo.reorder_items(album_id, item_ids)
    
    # ========================================================================
    # Cover Management
    # ========================================================================
    
    def set_cover(self, album_id: str, item_id: Optional[str], user_id: int) -> bool:
        """Set album cover item."""
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, "Cannot edit album")
        
        if item_id:
            # Verify item is in album
            items = self.album_repo.get_items(album_id)
            if not any(i['id'] == item_id for i in items):
                raise HTTPException(400, "Item not in album")
        
        return self.album_repo.set_cover_item(album_id, item_id)
    
    def get_cover_item(self, album_id: str) -> Optional[str]:
        """Get effective cover item ID."""
        return self.album_repo.get_effective_cover(album_id)
    
    # ========================================================================
    # Permission Helpers
    # ========================================================================
    
    def _can_view(self, album_id: str, user_id: int) -> bool:
        """Check if user can view album."""
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return False
        
        # Owner can view
        if album['user_id'] == user_id:
            return True
        
        # Check folder permissions
        if album.get('folder_id'):
            folder = self.folder_repo.get_by_id(album['folder_id'])
            if folder:
                # Shared folder - check permissions
                # TODO: check sharing permissions
                if folder['user_id'] == user_id:
                    return True
        
        return False
    
    def _can_edit(self, album_id: str, user_id: int) -> bool:
        """Check if user can edit album."""
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return False
        
        # Owner can edit
        if album['user_id'] == user_id:
            return True
        
        # Check folder edit permission
        if album.get('folder_id'):
            folder = self.folder_repo.get_by_id(album['folder_id'])
            if folder and folder['user_id'] == user_id:
                return True
            # TODO: check explicit edit permission
        
        return False
    
    def _can_delete(self, album_id: str, user_id: int) -> bool:
        """Check if user can delete album."""
        return self._can_edit(album_id, user_id)
    
    # ========================================================================
    # List Operations
    # ========================================================================
    
    def get_albums_by_folder(self, folder_id: str, user_id: int) -> List[Dict]:
        """Get albums in folder."""
        # Check folder access
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            return []
        
        # TODO: check sharing permissions
        
        albums = self.album_repo.get_by_folder(folder_id)
        
        # Add effective cover to each
        for album in albums:
            album['effective_cover_item_id'] = self.album_repo.get_effective_cover(album['id'])
        
        return albums
