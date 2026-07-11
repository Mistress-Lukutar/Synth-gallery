'''
File:   test_jxl_fallback_service.py
Brief:  Unit tests for the JXL to JPEG fallback service.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.2.0
'''

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image

from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.services.jxl_fallback_service import (
    JxlFallbackError,
    JxlFallbackService,
)


@pytest.fixture
def jxl_bytes() -> bytes:
    '''Return fake JXL bytes (magic bytes only).'''
    return b'\x00\x00\x00\x0cJXL fake'


@pytest.fixture
def decoded_png_bytes() -> bytes:
    '''Return a PNG byte string representing a decoded JXL image.'''
    img = Image.new('RGB', (100, 80), color='green')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


@pytest.fixture
def dek() -> bytes:
    '''Return a random data encryption key for testing.'''
    return EncryptionService.generate_dek()


@pytest.fixture
def fallback_service(tmp_path) -> JxlFallbackService:
    '''Return a fallback service using a temporary local storage backend.'''
    from app.infrastructure.storage import LocalStorage, StorageConfig

    config = StorageConfig(backend='local', base_path=tmp_path)
    storage = LocalStorage(config)
    return JxlFallbackService(storage=storage, quality=85)


@pytest.mark.asyncio
async def test_generates_jpeg_fallback(
    fallback_service: JxlFallbackService,
    jxl_bytes: bytes,
    decoded_png_bytes: bytes,
    dek: bytes,
) -> None:
    '''The service should produce valid JPEG bytes from JXL input.'''
    with patch(
        'app.infrastructure.services.jxl_fallback_service.decode_jxl',
        return_value=decoded_png_bytes,
    ):
        jpeg_bytes = await fallback_service.get_fallback('test-id', jxl_bytes, dek)

    assert jpeg_bytes
    assert jpeg_bytes != jxl_bytes

    with Image.open(io.BytesIO(jpeg_bytes)) as img:
        assert img.format == 'JPEG'
        assert img.size == (100, 80)


@pytest.mark.asyncio
async def test_caches_fallback_result(
    fallback_service: JxlFallbackService,
    jxl_bytes: bytes,
    decoded_png_bytes: bytes,
    dek: bytes,
) -> None:
    '''The second request should return the same bytes from cache.'''
    with patch(
        'app.infrastructure.services.jxl_fallback_service.decode_jxl',
        return_value=decoded_png_bytes,
    ):
        first = await fallback_service.get_fallback('cached-id', jxl_bytes, dek)
        second = await fallback_service.get_fallback('cached-id', jxl_bytes, dek)
    assert first == second


@pytest.mark.asyncio
async def test_invalidate_removes_cache(
    fallback_service: JxlFallbackService,
    jxl_bytes: bytes,
    decoded_png_bytes: bytes,
    dek: bytes,
) -> None:
    '''Invalidating a fallback should force regeneration on next request.'''
    with patch(
        'app.infrastructure.services.jxl_fallback_service.decode_jxl',
        return_value=decoded_png_bytes,
    ):
        await fallback_service.get_fallback('invalidated-id', jxl_bytes, dek)
        await fallback_service.invalidate('invalidated-id')

        regenerated = await fallback_service.get_fallback('invalidated-id', jxl_bytes, dek)

    with Image.open(io.BytesIO(regenerated)) as img:
        assert img.format == 'JPEG'


@pytest.mark.asyncio
async def test_invalid_jxl_data_raises(
    fallback_service: JxlFallbackService,
    dek: bytes,
) -> None:
    '''Passing non-JXL data should raise a clear error.'''
    with patch(
        'app.infrastructure.services.jxl_fallback_service.decode_jxl',
        side_effect=RuntimeError('not a JXL file'),
    ):
        with pytest.raises(JxlFallbackError):
            await fallback_service.get_fallback('bad-id', b'not an image', dek)
