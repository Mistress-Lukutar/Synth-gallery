'''
File:   folder_service.py
Brief:  Folder service - handles folder management operations.
Author: Mistress-Lukutar
Date:   2026-07-13
Version: v1.0.0
'''

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from app.infrastructure.repositories import FolderRepository, PermissionRepository


class FolderService:
    '''Service for folder management operations.

    Responsibilities:
    - Folder CRUD operations
    - Parent-child folder relationships
    - User folder tree retrieval
    '''

    def __init__(
        self,
        folder_repository: FolderRepository,
        permission_repository: Optional[PermissionRepository] = None,
    ) -> None:
        self.folder_repo = folder_repository
        self.perm_repo = permission_repository

    def create_folder(
        self,
        name: str,
        user_id: int,
        parent_id: Optional[str] = None,
    ) -> dict:
        '''Create a new folder.

        Args:
            name: Folder name
            user_id: Owner user ID
            parent_id: Optional parent folder ID

        Returns:
            Created folder dict

        Raises:
            HTTPException: On validation errors or permission issues
        '''
        if parent_id:
            parent = self.folder_repo.get_by_id(parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail='Parent folder not found')

            if parent['user_id'] != user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot create folder in another user's folder",
                )

        folder_id = self.folder_repo.create(
            name=name,
            user_id=user_id,
            parent_id=parent_id,
        )

        return self.folder_repo.get_by_id(folder_id)

    def update_folder(self, folder_id: str, name: str, user_id: int) -> dict:
        '''Update folder name.

        Args:
            folder_id: Folder ID to update
            name: New name
            user_id: User making the request (must be owner)

        Returns:
            Updated folder dict

        Raises:
            HTTPException: If folder not found or user not owner
        '''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail='Folder not found')

        if folder['user_id'] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")

        self.folder_repo.update(folder_id, name=name)
        return self.folder_repo.get_by_id(folder_id)

    def delete_folder(self, folder_id: str, user_id: int) -> list[str]:
        '''Delete a folder and all its contents.

        Args:
            folder_id: Folder ID to delete
            user_id: User making the request (must be owner)

        Returns:
            List of filenames (item IDs) that were deleted

        Raises:
            HTTPException: If folder not found or user not owner
        '''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail='Folder not found')

        if folder['user_id'] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")

        return self.folder_repo.delete(folder_id)

    def get_breadcrumbs(self, folder_id: str) -> list[dict]:
        '''Get breadcrumb path from root to folder.

        Args:
            folder_id: Target folder ID

        Returns:
            List of {id, name} dicts from root to target
        '''
        breadcrumbs: list[dict] = []
        current_id: Optional[str] = folder_id

        while current_id:
            folder = self.folder_repo.get_by_id(current_id)
            if not folder:
                break

            breadcrumbs.insert(0, {'id': folder['id'], 'name': folder['name']})
            current_id = folder.get('parent_id')

        return breadcrumbs

    def move_folder(
        self,
        folder_id: str,
        new_parent_id: Optional[str],
        user_id: int,
    ) -> bool:
        '''Move folder to new parent.

        Args:
            folder_id: Folder to move
            new_parent_id: New parent folder ID (None for root)
            user_id: User making the request

        Returns:
            True if successful

        Raises:
            HTTPException: On validation errors
        '''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail='Folder not found')

        if folder['user_id'] != user_id:
            raise HTTPException(status_code=403, detail="You don't own this folder")

        if new_parent_id:
            parent = self.folder_repo.get_by_id(new_parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail='Parent folder not found')

            if parent['user_id'] != user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot move to another user's folder",
                )

            if self._is_descendant(new_parent_id, folder_id):
                raise HTTPException(
                    status_code=400,
                    detail='Cannot move folder into its own subfolder',
                )

        return self.folder_repo.move_to_folder(folder_id, new_parent_id)

    def _is_descendant(self, potential_descendant: str, ancestor: str) -> bool:
        '''Check if potential_descendant is a descendant of ancestor.'''
        current_id: Optional[str] = potential_descendant

        while current_id:
            if current_id == ancestor:
                return True

            folder = self.folder_repo.get_by_id(current_id)
            if not folder:
                return False

            current_id = folder.get('parent_id')

        return False

    # =========================================================================
    # Folder Tree & Contents
    # =========================================================================

    def get_folder_tree(self, user_id: int) -> list[dict]:
        '''Get folder tree for sidebar with metadata.

        Args:
            user_id: User ID

        Returns:
            List of folder dicts with metadata
        '''
        return self.folder_repo.list_with_metadata(user_id)

    def get_folder_contents(self, folder_id: str, user_id: int) -> dict:
        '''Get contents of a folder (subfolders, albums, photos).

        Args:
            folder_id: Folder ID
            user_id: User ID

        Returns:
            Dict with subfolders, albums, photos

        Raises:
            HTTPException: If no access to folder
        '''
        folder = self.folder_repo.get_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail='Folder not found')

        has_access = False
        if folder['user_id'] == user_id:
            has_access = True
        elif self.perm_repo:
            perm = self.perm_repo.get_permission(folder_id, user_id)
            if perm in ('viewer', 'editor'):
                has_access = True

        if not has_access:
            raise HTTPException(status_code=403, detail='Access denied')

        subfolders = self.folder_repo.get_subfolders(folder_id, user_id)
        albums = self.folder_repo.get_albums_in_folder(folder_id)
        items = self.folder_repo.get_standalone_items(folder_id)

        return {
            'subfolders': subfolders,
            'albums': albums,
            'items': items,
            'photos': items,  # Legacy alias for backward compatibility
        }
