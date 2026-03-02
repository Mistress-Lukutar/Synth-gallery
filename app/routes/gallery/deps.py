"""Shared dependencies for gallery routes.

This module contains factory functions for creating services
used across all gallery sub-modules.
"""
from ...application.services import UploadService, PhotoService, FolderService, PermissionService
from ...config import UPLOADS_DIR, THUMBNAILS_DIR
from ...database import create_connection
from ...infrastructure.repositories import (
    PhotoRepository, FolderRepository, PermissionRepository,
    SafeRepository, ItemRepository, ItemMediaRepository
)


def get_upload_service(db) -> UploadService:
    """Create UploadService with repositories."""
    return UploadService(
        photo_repository=PhotoRepository(db),
        uploads_dir=UPLOADS_DIR,
        thumbnails_dir=THUMBNAILS_DIR,
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db)
    )


def get_photo_service(db) -> PhotoService:
    """Create PhotoService with repositories."""
    return PhotoService(
        photo_repository=PhotoRepository(db),
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db)
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
        photo_repository=PhotoRepository(db),
        safe_repository=SafeRepository(db)
    )
