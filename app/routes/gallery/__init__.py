'''
File:   __init__.py
Brief:  Gallery routes package.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

This module aggregates all gallery-related routes:
- main: Main gallery page and folder content API
- uploads: Photo/video upload endpoints (single, album, bulk)
- items: Unified polymorphic items API (photos, videos, albums)
- files: File serving (uploads and thumbnails)
'''
from fastapi import APIRouter

from . import main, uploads, items, files

router = APIRouter()

router.include_router(main.router)
router.include_router(uploads.router)
router.include_router(items.router)   # Unified polymorphic items API (includes albums)
router.include_router(files.router)

__all__ = ["router"]
