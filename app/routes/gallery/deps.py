"""Shared dependencies for gallery routes.

This module contains factory functions for creating services
used across all gallery sub-modules.
"""
from ...application.services import FolderService, PermissionService, AlbumService
from ...config import UPLOADS_DIR, THUMBNAILS_DIR
from ...database import create_connection
from ...infrastructure.repositories import (
    FolderRepository, PermissionRepository,
    SafeRepository, ItemRepository, ItemMediaRepository, AlbumRepository
)


def get_folder_service(db) -> FolderService:
    """Create FolderService with repositories."""
    return FolderService(
        folder_repository=FolderRepository(db),
        safe_repository=SafeRepository(db),
        permission_repository=PermissionRepository(db)
    )


def get_permission_service(db) -> PermissionService:
    """Create PermissionService with repositories."""
    return PermissionService(
        permission_repository=PermissionRepository(db),
        folder_repository=FolderRepository(db),
        item_repository=ItemRepository(db),
        album_repository=AlbumRepository(db),
        safe_repository=SafeRepository(db)
    )


def get_album_service(db) -> AlbumService:
    """Create AlbumService with repositories."""
    return AlbumService(
        album_repository=AlbumRepository(db),
        item_repository=ItemRepository(db),
        folder_repository=FolderRepository(db)
    )
