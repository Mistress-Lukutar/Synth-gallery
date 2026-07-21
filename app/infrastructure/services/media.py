'''
File:   media.py
Brief:  Image processing helpers (thumbnail generation, dimensions).
        Video handling is delegated to :mod:`app.infrastructure.services.ffmpeg`.
Author: Mistress-Lukutar
Date:   2026-07-21
Version: v1.1.0
'''
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

from ...config import ALLOWED_VIDEO_TYPES
from .jxl import decode_jxl, get_jxl_dimensions


def create_thumbnail(
    source_path: Path,
    thumb_path: Path,
    size: tuple[int, int] = (400, 400),
) -> None:
    '''Create an image thumbnail from a source file path.

    Applies EXIF orientation and converts paletted/RGBA inputs to RGB.
    '''
    with Image.open(source_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(thumb_path, 'JPEG', quality=85)


def get_media_type(content_type: str) -> str:
    '''Return ``"video"`` or ``"image"`` based on a MIME content type.'''
    if content_type in ALLOWED_VIDEO_TYPES:
        return 'video'
    return 'image'


def create_thumbnail_bytes(
    image_data: bytes,
    size: tuple[int, int] = (400, 400),
) -> tuple[bytes, int, int]:
    '''Create a JPEG thumbnail from image bytes.

    Falls back to JXL decoding via djxl when Pillow cannot open the input.

    Returns:
        Tuple of (jpeg_bytes, width, height).
    '''
    try:
        img = Image.open(BytesIO(image_data))
    except Exception:
        image_data = decode_jxl(image_data)
        img = Image.open(BytesIO(image_data))

    with img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        thumb_width, thumb_height = img.size
        output = BytesIO()
        img.save(output, 'JPEG', quality=85)
        return output.getvalue(), thumb_width, thumb_height


def get_image_dimensions(image_data: bytes) -> Optional[Tuple[int, int]]:
    '''Return (width, height) for an image, or None on failure.'''
    try:
        with Image.open(BytesIO(image_data)) as img:
            return img.size
    except Exception:
        pass

    try:
        return get_jxl_dimensions(image_data)
    except Exception:
        return None
