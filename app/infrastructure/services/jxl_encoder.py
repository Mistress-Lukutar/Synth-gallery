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
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from ...config import (
    JXL_DECODING_SPEED,
    JXL_EFFORT,
    JXL_LOSSLESS_TRANSCODE_JPEG,
    JXL_THREADS,
)

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


def _encode_jpeg_transcode(jpeg_bytes: bytes) -> bytes:
    '''Transcode a JPEG file to lossless JPEG XL via a temporary file.

    pillow_jxl performs true lossless JPEG transcode only when the source
    image is opened from a named file. This helper writes the bytes to a
    temporary file, encodes it, and cleans up afterwards.

    Args:
        jpeg_bytes: Raw JPEG file bytes.

    Returns:
        Lossless JXL bytes preserving the original JPEG bitstream.

    Raises:
        JxlEncodeError: If transcode fails.
    '''
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(jpeg_bytes)
            tmp_path = Path(tmp.name)

        with Image.open(tmp_path) as img:
            output = io.BytesIO()
            img.save(
                output,
                format='JXL',
                lossless_jpeg=True,
                use_container=True,
                decoding_speed=JXL_DECODING_SPEED,
                effort=JXL_EFFORT,
                num_threads=JXL_THREADS,
            )
            return output.getvalue()
    except Exception as exc:
        logger.exception('Failed to transcode JPEG to JPEG XL')
        raise JxlEncodeError(f'JPEG XL transcode failed: {exc}') from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _encode_lossless_reencode(image_bytes: bytes) -> bytes:
    '''Re-encode raster image bytes to lossless JPEG XL.

    Args:
        image_bytes: Raw image file bytes.

    Returns:
        Lossless JXL bytes.

    Raises:
        JxlEncodeError: If encoding fails.
    '''
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = _normalize_image_mode(img)
            output = io.BytesIO()

            save_options: dict[str, object] = {
                'format': 'JXL',
                'lossless': True,
                'use_container': True,
                'decoding_speed': JXL_DECODING_SPEED,
                'effort': JXL_EFFORT,
                'num_threads': JXL_THREADS,
            }

            exif = img.getexif()
            if exif:
                save_options['exif'] = exif.tobytes()

            img.save(output, **save_options)
            return output.getvalue()
    except Exception as exc:
        logger.exception('Failed to encode image to JPEG XL')
        raise JxlEncodeError(f'JPEG XL encoding failed: {exc}') from exc


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
        image_bytes = source
        if is_jpeg is None:
            is_jpeg = _is_jpeg_content(image_bytes)
    else:
        image_bytes = source.read()
        source.seek(0)
        if len(image_bytes) == 0:
            raise JxlEncodeError('Cannot encode empty image data')
        if is_jpeg is None:
            is_jpeg = _is_jpeg_content(image_bytes)

    if is_jpeg and JXL_LOSSLESS_TRANSCODE_JPEG:
        return _encode_jpeg_transcode(image_bytes)

    return _encode_lossless_reencode(image_bytes)
