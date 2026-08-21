'''
File:   deps.py
Brief:  Shared dependencies for gallery routes.
Author: Mistress-Lukutar
Date:   2026-07-13
'''
from app.application.services import FolderService, PermissionService, AlbumService
from app.infrastructure.repositories import (
    FolderRepository,
    PermissionRepository,
    ItemRepository,
    AlbumRepository,
    ItemMediaRepository,
)


def get_folder_service(db) -> FolderService:
    """Create FolderService with repositories."""
    return FolderService(
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db)
    )


def get_permission_service(db) -> PermissionService:
    """Create PermissionService with repositories."""
    return PermissionService(
        permission_repository=PermissionRepository(db),
        folder_repository=FolderRepository(db),
        item_repository=ItemRepository(db),
        album_repository=AlbumRepository(db)
    )


def get_album_service(db) -> AlbumService:
    """Create AlbumService with repositories."""
    return AlbumService(
        album_repository=AlbumRepository(db),
        item_repository=ItemRepository(db),
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db),
        item_media_repository=ItemMediaRepository(db)
    )
