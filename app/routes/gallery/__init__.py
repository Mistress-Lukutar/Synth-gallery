"""Gallery routes package.

This module aggregates all gallery-related routes:
- main: Main gallery page and folder content API
- uploads: Photo/video upload endpoints (single, album, bulk)
- photos: Photo CRUD operations
- albums: Album management
- files: File serving (uploads and thumbnails)
"""
from fastapi import APIRouter

from . import main, uploads, photos, albums, files

# Create main router with all routes
router = APIRouter()

# Include all sub-routers
router.include_router(main.router)
router.include_router(uploads.router)
router.include_router(photos.router)
router.include_router(albums.router)
router.include_router(files.router)

__all__ = ["router"]
