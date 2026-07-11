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
import shutil
import subprocess
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
from .jxl import _get_jxl_binary

logger = logging.getLogger(__name__)


class JxlEncodeError(Exception):
    '''Raised when JPEG XL encoding fails.'''


def is_jxl_encoding_available() -> bool:
    '''Return True if a JPEG XL encoder is available.

    Prefer the official ``cjxl`` CLI (required for progressive encoding);
    fall back to the pillow_jxl plugin.

    Returns:
        True when either cjxl or the pillow_jxl save handler is available.
    '''
    if _get_jxl_binary('cjxl.exe' if os.name == 'nt' else 'cjxl') is not None:
        return True

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


def _extension_for_content_type(is_jpeg: bool) -> str:
    '''Return a file extension for the source image type.'''
    return '.jpg' if is_jpeg else '.png'


def _encode_with_cjxl(image_bytes: bytes, is_jpeg: bool) -> bytes:
    '''Encode image bytes to lossless progressive JPEG XL using the cjxl CLI.

    Args:
        image_bytes: Raw image file bytes.
        is_jpeg: Whether the source is a JPEG file.

    Returns:
        Lossless progressive JXL bytes.

    Raises:
        JxlEncodeError: If cjxl is unavailable or encoding fails.
    '''
    binary = _get_jxl_binary('cjxl.exe' if os.name == 'nt' else 'cjxl')
    if binary is None:
        raise JxlEncodeError('cjxl encoder not found')

    source_path: Path | None = None
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=_extension_for_content_type(is_jpeg),
            delete=False,
        ) as source_tmp:
            source_tmp.write(image_bytes)
            source_path = Path(source_tmp.name)

        with tempfile.NamedTemporaryFile(suffix='.jxl', delete=False) as output_tmp:
            output_path = Path(output_tmp.name)

        cmd: list[str | Path] = [
            binary,
            source_path,
            output_path,
            '--progressive',
            '-e', str(JXL_EFFORT),
            '--num_threads', str(JXL_THREADS),
            '--container=1',
        ]

        if is_jpeg and JXL_LOSSLESS_TRANSCODE_JPEG:
            cmd.append('--lossless_jpeg=1')
        else:
            cmd.extend(['-d', '0'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
            encoding='utf-8',
            errors='ignore',
        )
        if result.returncode != 0:
            raise JxlEncodeError(
                f'cjxl failed (code {result.returncode}): {result.stderr}'
            )

        return output_path.read_bytes()
    except JxlEncodeError:
        raise
    except Exception as exc:
        logger.exception('Failed to encode with cjxl')
        raise JxlEncodeError(f'cjxl encoding failed: {exc}') from exc
    finally:
        for path in (source_path, output_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass


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


def _encode_with_pillow(image_bytes: bytes, is_jpeg: bool) -> bytes:
    '''Fallback encoding using pillow_jxl.

    Args:
        image_bytes: Raw image file bytes.
        is_jpeg: Whether the source is a JPEG file.

    Returns:
        Lossless JXL bytes.

    Raises:
        JxlEncodeError: If encoding fails.
    '''
    try:
        import pillow_jxl  # noqa: F401
    except ImportError as exc:
        raise JxlEncodeError('No JPEG XL encoder available') from exc

    tmp_path: Path | None = None
    try:
        if is_jpeg and JXL_LOSSLESS_TRANSCODE_JPEG:
            # pillow_jxl performs true lossless JPEG transcode only when the
            # source image is opened from a named file.
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp.write(image_bytes)
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
    except JxlEncodeError:
        raise
    except Exception as exc:
        logger.exception('Failed to encode image to JPEG XL')
        raise JxlEncodeError(f'JPEG XL encoding failed: {exc}') from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def encode_to_lossless_jxl(
    source: bytes | BinaryIO,
    is_jpeg: bool | None = None,
) -> bytes:
    '''Encode raster image data to lossless JPEG XL.

    JPEG sources are transcoded losslessly when the feature flag
    JXL_LOSSLESS_TRANSCODE_JPEG is enabled. Other formats are
    re-encoded losslessly. EXIF metadata is preserved inside the
    JXL container.

    Prefer the official ``cjxl`` CLI for progressive encoding; fall back
    to the pillow_jxl plugin when cjxl is unavailable.

    Args:
        source: Raw image bytes or file-like object.
        is_jpeg: Whether the source is a JPEG file. If None, the
            value is detected from magic bytes.

    Returns:
        Lossless JPEG XL bytes.

    Raises:
        JxlEncodeError: If the encoder is unavailable or encoding fails.
    '''
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

    binary = _get_jxl_binary('cjxl.exe' if os.name == 'nt' else 'cjxl')
    if binary is not None:
        try:
            return _encode_with_cjxl(image_bytes, is_jpeg)
        except JxlEncodeError:
            logger.warning('cjxl encode failed, falling back to pillow_jxl')

    return _encode_with_pillow(image_bytes, is_jpeg)
