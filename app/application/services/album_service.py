'''
File:   album_service.py
Brief:  Album service - content-agnostic album management.
Author: Mistress-Lukutar
Date:   2026-07-23
Version: v1.1.0
'''

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import HTTPException

from app.application.services.item_service import ItemService
from app.infrastructure.repositories import (
    AlbumRepository,
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
    PermissionRepository,
)


class AlbumService:
    '''Service for managing albums.

    Responsibilities:
    - Create/delete albums
    - Add/remove/reorder items in albums
    - Manage album cover
    - Move albums between folders

    Albums are content-agnostic - they can contain any item types.
    '''

    def __init__(
        self,
        album_repository: AlbumRepository,
        item_repository: ItemRepository,
        folder_repository: FolderRepository,
        permission_repository: Optional[PermissionRepository] = None,
        item_media_repository: Optional[ItemMediaRepository] = None,
    ) -> None:
        self.album_repo = album_repository
        self.item_repo = item_repository
        self.folder_repo = folder_repository
        self.perm_repo = permission_repository
        self.media_repo = item_media_repository

    # ========================================================================
    # Album CRUD
    # ========================================================================

    def create_album(
        self,
        name: str,
        folder_id: str,
        user_id: int,
        item_ids: Optional[List[str]] = None,
    ) -> Dict:
        '''Create a new album with optional items.

        Args:
            name: Album name
            folder_id: Parent folder
            user_id: Owner
            item_ids: Optional item IDs to add

        Returns:
            Created album dict
        '''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(404, 'Folder not found')

        if folder['user_id'] != user_id:
            raise HTTPException(403, 'Cannot create album here')

        if item_ids:
            for item_id in item_ids:
                item = self.item_repo.get_by_id(item_id)
                if not item:
                    raise HTTPException(400, f'Item not found: {item_id}')
                if item['folder_id'] != folder_id:
                    raise HTTPException(400, f'Item not in folder: {item_id}')

        album_id = self.album_repo.create(
            folder_id=folder_id,
            user_id=user_id,
            name=name,
        )

        if item_ids:
            for position, item_id in enumerate(item_ids):
                self.album_repo.add_item(album_id, item_id, position)

        return {
            'id': album_id,
            'name': name,
            'folder_id': folder_id,
            'item_count': len(item_ids) if item_ids else 0,
            'photo_count': len(item_ids) if item_ids else 0,  # Legacy alias
        }

    def get_album(self, album_id: str, user_id: int) -> Optional[Dict]:
        '''Get album with optimized items list.'''
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return None

        if not self._can_view(album_id, user_id):
            raise HTTPException(403, 'Access denied')

        raw_items = self.album_repo.get_items(album_id)
        items = []
        for item in raw_items:
            items.append({
                'id': item['id'],
                'title': item.get('title', ''),
                'media_type': item.get('media_type', 'image'),
                'thumb_width': item.get('thumb_width'),
                'thumb_height': item.get('thumb_height'),
                'taken_at': item.get('taken_at'),
                'position': item.get('position', 0),
            })

        return {
            'id': album['id'],
            'name': album['name'],
            'created_at': album['created_at'],
            'cover_item_id': album.get('cover_item_id'),
            'user_id': album.get('user_id'),
            'item_count': album.get('item_count', 0),
            'items': items,
            'can_edit': self._can_edit(album_id, user_id),
        }

    def delete_album(self, album_id: str, user_id: int) -> bool:
        '''Delete album and all its items including files.'''
        if not self._can_delete(album_id, user_id):
            raise HTTPException(403, 'Cannot delete this album')

        album_items = self.album_repo.get_items(album_id)

        result = self.album_repo.delete(album_id)

        item_media_repo = ItemMediaRepository(self.album_repo._conn)
        item_service = ItemService(
            item_repository=self.item_repo,
            item_media_repository=item_media_repo,
        )
        for item in album_items:
            try:
                item_service._delete_item_files(item['id'])
                self.item_repo.delete(item['id'])
            except Exception:
                pass

        return result

    def move_album(self, album_id: str, dest_folder_id: str, user_id: int) -> bool:
        '''Move album and its items to different folder.'''
        album = self.album_repo.get_by_id(album_id)
        if not album:
            raise HTTPException(404, 'Album not found')

        if not self._can_delete(album_id, user_id):
            raise HTTPException(403, 'No permission to move')

        dest_folder = self.folder_repo.get_by_id(dest_folder_id)
        if not dest_folder:
            raise HTTPException(404, 'Destination folder not found')

        if dest_folder['user_id'] != user_id:
            raise HTTPException(403, 'Cannot move to this folder')

        album_items = self.album_repo.get_items(album_id)

        for item in album_items:
            self.item_repo.move_to_folder(item['id'], dest_folder_id)

        return self.album_repo.move_to_folder(album_id, dest_folder_id)

    async def copy_album(self, album_id: str, dest_folder_id: str, user_id: int) -> str:
        '''Copy album and all its items to a different folder.

        Returns:
            New album ID
        '''
        album = self.album_repo.get_by_id(album_id)
        if not album:
            raise HTTPException(404, 'Album not found')

        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, 'No permission to copy')

        dest_folder = self.folder_repo.get_by_id(dest_folder_id)
        if not dest_folder:
            raise HTTPException(404, 'Destination folder not found')

        if dest_folder['user_id'] != user_id:
            raise HTTPException(403, 'Cannot copy to this folder')

        if not self.media_repo:
            raise HTTPException(500, 'ItemMediaRepository not available for album copy')

        album_items = self.album_repo.get_items(album_id)

        item_service = ItemService(
            item_repository=self.item_repo,
            item_media_repository=self.media_repo,
        )

        item_id_map = {}
        for item in album_items:
            try:
                new_item_id = await item_service.copy_item(
                    item_id=item['id'],
                    dest_folder_id=dest_folder_id,
                    user_id=user_id,
                )
                item_id_map[item['id']] = {
                    'new_id': new_item_id,
                    'position': item.get('position', 0),
                }
            except Exception:
                pass

        new_album_id = self.album_repo.create(
            folder_id=dest_folder_id,
            user_id=user_id,
            name=album['name'],
        )

        sorted_items = sorted(item_id_map.values(), key=lambda x: x['position'])
        for position, entry in enumerate(sorted_items):
            self.album_repo.add_item(new_album_id, entry['new_id'], position)

        old_cover_id = album.get('cover_item_id')
        if old_cover_id and old_cover_id in item_id_map:
            self.album_repo.set_cover_item(
                new_album_id,
                item_id_map[old_cover_id]['new_id'],
            )

        return new_album_id

    # ========================================================================
    # Item Management
    # ========================================================================

    def add_items(self, album_id: str, item_ids: List[str], user_id: int) -> int:
        '''Add items to album.

        Args:
            album_id: Album ID
            item_ids: Item IDs to add
            user_id: User performing action

        Returns:
            Number of items added
        '''
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, 'Cannot edit album')

        album = self.album_repo.get_by_id(album_id)

        count = 0
        for item_id in item_ids:
            item = self.item_repo.get_by_id(item_id)
            if not item:
                continue

            if item['folder_id'] != album['folder_id']:
                continue

            if self.album_repo.add_item(album_id, item_id):
                count += 1

        return count

    def remove_items(self, album_id: str, item_ids: List[str], user_id: int) -> int:
        '''Remove items from album.'''
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, 'Cannot edit album')

        count = 0
        for item_id in item_ids:
            if self.album_repo.remove_item(album_id, item_id):
                count += 1

        return count

    def reorder_items(
        self,
        album_id: str,
        item_ids: List[str],
        user_id: int,
    ) -> bool:
        '''Reorder items in album.

        Args:
            album_id: Album ID
            item_ids: Item IDs in new order
            user_id: User performing action
        '''
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, 'Cannot edit album')

        return self.album_repo.reorder_items(album_id, item_ids)

    # ========================================================================
    # Cover Management
    # ========================================================================

    def set_cover(self, album_id: str, item_id: Optional[str], user_id: int) -> bool:
        '''Set album cover item.'''
        if not self._can_edit(album_id, user_id):
            raise HTTPException(403, 'Cannot edit album')

        if item_id:
            items = self.album_repo.get_items(album_id)
            if not any(i['id'] == item_id for i in items):
                raise HTTPException(400, 'Item not in album')

        return self.album_repo.set_cover_item(album_id, item_id)

    def get_cover_item(self, album_id: str) -> Optional[str]:
        '''Get effective cover item ID.'''
        return self.album_repo.get_effective_cover(album_id)

    # ========================================================================
    # Permission Helpers
    # ========================================================================

    def _can_view(self, album_id: str, user_id: int) -> bool:
        '''Check if user can view album.'''
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return False

        if album['user_id'] == user_id:
            return True

        if album.get('folder_id'):
            folder = self.folder_repo.get_by_id(album['folder_id'])
            if folder:
                if folder['user_id'] == user_id:
                    return True

                if self.perm_repo:
                    perm = self.perm_repo.get_permission(album['folder_id'], user_id)
                    if perm in ('viewer', 'editor'):
                        return True

        return False

    def _can_edit(self, album_id: str, user_id: int) -> bool:
        '''Check if user can edit album.'''
        album = self.album_repo.get_by_id(album_id)
        if not album:
            return False

        if album['user_id'] == user_id:
            return True

        if album.get('folder_id'):
            folder = self.folder_repo.get_by_id(album['folder_id'])
            if folder and folder['user_id'] == user_id:
                return True

        return False

    def _can_delete(self, album_id: str, user_id: int) -> bool:
        '''Check if user can delete album.'''
        return self._can_edit(album_id, user_id)

    # ========================================================================
    # List Operations
    # ========================================================================

    def get_albums_by_folder(self, folder_id: str, user_id: int) -> List[Dict]:
        '''Get albums in folder.'''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            return []

        albums = self.album_repo.get_by_folder(folder_id)

        for album in albums:
            album['effective_cover_item_id'] = self.album_repo.get_effective_cover(album['id'])

        return albums
