'''
File:   jxl_encoder.py
Brief:  Encode raster images to lossless JPEG XL via the official cjxl CLI.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.2.0
'''

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.config import (
    JXL_EFFORT,
    JXL_LOSSLESS_TRANSCODE_JPEG,
    JXL_PROGRESSIVE_AC,
    JXL_PROGRESSIVE_DC,
    JXL_QPROGRESSIVE_AC,
    JXL_THREADS,
)
from app.infrastructure.services.jxl import _get_jxl_binary

logger = logging.getLogger(__name__)


class JxlEncodeError(Exception):
    '''Raised when JPEG XL encoding fails.'''


def is_jxl_encoding_available() -> bool:
    '''Return True if the official ``cjxl`` CLI is available.

    Returns:
        True when the cjxl binary is found on the system or project PATH.
    '''
    return _get_jxl_binary('cjxl.exe' if os.name == 'nt' else 'cjxl') is not None


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

    Progressive encoding is controlled through the ``JXL_PROGRESSIVE_AC``,
    ``JXL_QPROGRESSIVE_AC`` and ``JXL_PROGRESSIVE_DC`` configuration flags.

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

        if JXL_PROGRESSIVE_AC:
            cmd.append('--progressive_ac')
        if JXL_QPROGRESSIVE_AC:
            cmd.append('--qprogressive_ac')
        if JXL_PROGRESSIVE_DC != -1:
            cmd.extend(['--progressive_dc', str(JXL_PROGRESSIVE_DC)])

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


def encode_to_lossless_jxl(
    source: bytes | BinaryIO,
    is_jpeg: bool | None = None,
) -> bytes:
    '''Encode raster image data to lossless JPEG XL.

    JPEG sources are transcoded losslessly when the feature flag
    JXL_LOSSLESS_TRANSCODE_JPEG is enabled. Other formats are
    re-encoded losslessly. EXIF metadata is preserved inside the
    JXL container.

    Encoding is performed exclusively through the official ``cjxl`` CLI.

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
    if binary is None:
        raise JxlEncodeError('cjxl encoder not found')

    return _encode_with_cjxl(image_bytes, is_jpeg)
