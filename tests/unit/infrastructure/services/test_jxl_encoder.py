'''
File:   test_jxl_encoder.py
Brief:  Unit tests for the lossless JPEG XL encoder.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.1.0
'''

from __future__ import annotations

import io

import pytest
from PIL import Image
from PIL.ExifTags import Base as ExifBase

from app.infrastructure.services.jxl_encoder import (
    encode_to_lossless_jxl,
    is_jxl_encoding_available,
    JxlEncodeError,
)


@pytest.fixture
def jpeg_with_exif() -> bytes:
    '''Return a JPEG byte string with embedded EXIF metadata.'''
    img = Image.new('RGB', (80, 60), color='blue')
    exif = img.getexif()
    exif[ExifBase.Make] = b'TestMaker'
    exif[ExifBase.Model] = b'TestModel'
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif.tobytes(), quality=90)
    return buf.getvalue()


@pytest.fixture
def png_with_alpha() -> bytes:
    '''Return a PNG byte string with an alpha channel.'''
    img = Image.new('RGBA', (40, 40), color=(255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def test_encoding_availability() -> None:
    '''The encoder should report availability based on the pillow_jxl plugin.'''
    assert isinstance(is_jxl_encoding_available(), bool)


def test_encode_jpeg_preserves_exif(jpeg_with_exif: bytes) -> None:
    '''Encoding a JPEG to JXL should preserve EXIF metadata.'''
    if not is_jxl_encoding_available():
        pytest.skip('JPEG XL encoder is not available')

    jxl_bytes = encode_to_lossless_jxl(jpeg_with_exif, is_jpeg=True)
    assert jxl_bytes
    assert jxl_bytes != jpeg_with_exif

    with Image.open(io.BytesIO(jxl_bytes)) as decoded:
        decoded_exif = decoded.getexif()
        assert decoded_exif.get(ExifBase.Make) == 'TestMaker'
        assert decoded_exif.get(ExifBase.Model) == 'TestModel'


def test_encode_png_with_alpha(png_with_alpha: bytes) -> None:
    '''Encoding a PNG with transparency should keep the alpha channel.'''
    if not is_jxl_encoding_available():
        pytest.skip('JPEG XL encoder is not available')

    jxl_bytes = encode_to_lossless_jxl(png_with_alpha)
    assert jxl_bytes

    with Image.open(io.BytesIO(jxl_bytes)) as decoded:
        assert decoded.mode == 'RGBA'
        assert decoded.size == (40, 40)


def test_encode_empty_data_raises() -> None:
    '''Encoding empty data should raise a clear error.'''
    if not is_jxl_encoding_available():
        pytest.skip('JPEG XL encoder is not available')

    with pytest.raises(JxlEncodeError):
        encode_to_lossless_jxl(b'')


def test_detects_jpeg_from_magic_bytes(jpeg_with_exif: bytes) -> None:
    '''When is_jpeg is omitted, the encoder should detect JPEG from magic bytes.'''
    if not is_jxl_encoding_available():
        pytest.skip('JPEG XL encoder is not available')

    jxl_bytes = encode_to_lossless_jxl(jpeg_with_exif)
    assert jxl_bytes

    with Image.open(io.BytesIO(jxl_bytes)) as decoded:
        assert decoded.size == (80, 60)


def test_jpeg_transcode_is_pixel_exact(jpeg_with_exif: bytes) -> None:
    '''Lossless JPEG transcode should decode to the same pixels.'''
    if not is_jxl_encoding_available():
        pytest.skip('JPEG XL encoder is not available')

    jxl_bytes = encode_to_lossless_jxl(jpeg_with_exif, is_jpeg=True)

    with Image.open(io.BytesIO(jpeg_with_exif)) as original:
        original_pixels = list(original.get_flattened_data())
    with Image.open(io.BytesIO(jxl_bytes)) as decoded:
        decoded_pixels = list(decoded.get_flattened_data())

    assert original_pixels == decoded_pixels
