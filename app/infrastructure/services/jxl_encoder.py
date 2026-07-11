'''
File:   jxl_encoder.py
Brief:  Encode raster images to lossless JPEG XL.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.1.0
'''

from __future__ import annotations

import io
import logging
from typing import BinaryIO

from PIL import Image

from ...config import JXL_LOSSLESS_TRANSCODE_JPEG

logger = logging.getLogger(__name__)


class JxlEncodeError(Exception):
    '''Raised when JPEG XL encoding fails.'''


def is_jxl_encoding_available() -> bool:
    '''Return True if Pillow can encode images to JPEG XL.

    Returns:
        True when the pillow_jxl plugin is installed and registered
        a save handler for the JXL format.
    '''
    try:
        import pillow_jxl  # noqa: F401
        return 'JXL' in Image.SAVE
    except Exception:
        return False


def _is_jpeg_content(data: bytes) -> bool:
    '''Check whether raw bytes represent a JPEG file.

    Args:
        data: First bytes of the file.

    Returns:
        True if the data starts with the JPEG SOI marker.
    '''
    return data.startswith(b'\xff\xd8')


def _normalize_image_mode(img: Image.Image) -> Image.Image:
    '''Convert unsupported color modes to encoder-compatible ones.

    The pillow_jxl encoder supports L, LA, RGB and RGBA. Palette
    and exotic modes are converted without introducing alpha when
    the original image did not have it.

    Args:
        img: Pillow image opened from source bytes.

    Returns:
        Image with a mode the JXL encoder accepts.
    '''
    if img.mode == 'P':
        return img.convert('RGB')

    if img.mode in ('L', 'LA', 'RGB', 'RGBA'):
        return img

    if 'A' in img.mode:
        return img.convert('RGBA')

    return img.convert('RGB')


def encode_to_lossless_jxl(
    source: bytes | BinaryIO,
    is_jpeg: bool | None = None,
) -> bytes:
    '''Encode raster image data to lossless JPEG XL.

    JPEG sources are transcoded losslessly when the feature flag
    JXL_LOSSLESS_TRANSCODE_JPEG is enabled. Other formats are
    re-encoded losslessly. EXIF metadata is preserved inside the
    JXL container.

    Args:
        source: Raw image bytes or file-like object.
        is_jpeg: Whether the source is a JPEG file. If None, the
            value is detected from magic bytes.

    Returns:
        Lossless JPEG XL bytes.

    Raises:
        JxlEncodeError: If the encoder is unavailable or encoding fails.
    '''
    if not is_jxl_encoding_available():
        raise JxlEncodeError('JPEG XL encoder is not available')

    if isinstance(source, bytes):
        if len(source) == 0:
            raise JxlEncodeError('Cannot encode empty image data')
        if is_jpeg is None:
            is_jpeg = _is_jpeg_content(source)
        source = io.BytesIO(source)
    elif is_jpeg is None:
        start = source.read(2)
        source.seek(0)
        is_jpeg = start == b'\xff\xd8'

    try:
        with Image.open(source) as img:
            img = _normalize_image_mode(img)
            output = io.BytesIO()

            save_options: dict[str, object] = {
                'format': 'JXL',
            }

            if is_jpeg and JXL_LOSSLESS_TRANSCODE_JPEG:
                save_options['lossless_jpeg'] = True
            else:
                save_options['lossless'] = True

            exif = img.getexif()
            if exif:
                save_options['exif'] = exif.tobytes()

            img.save(output, **save_options)
            return output.getvalue()
    except JxlEncodeError:
        raise
    except Exception as exc:
        logger.exception('Failed to encode image to JPEG XL')
        raise JxlEncodeError(f'JPEG XL encoding failed: {exc}') from exc
