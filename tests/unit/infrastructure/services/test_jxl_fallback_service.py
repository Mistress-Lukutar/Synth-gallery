'''
File:   test_jxl_fallback_service.py
Brief:  Unit tests for the JXL to JPEG fallback service.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.1.0
'''

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.services.jxl_fallback_service import (
    JxlFallbackError,
    JxlFallbackService,
)


@pytest.fixture
def jxl_bytes() -> bytes:
    '''Return a minimal lossless JXL byte string.'''
    img = Image.new('RGB', (100, 80), color='green')
    buf = io.BytesIO()
    img.save(buf, format='JXL', lossless=True)
    return buf.getvalue()


@pytest.fixture
def dek() -> bytes:
    '''Return a random data encryption key for testing.'''
    return EncryptionService.generate_dek()


@pytest.fixture
def fallback_service(tmp_path) -> JxlFallbackService:
    '''Return a fallback service using a temporary local storage backend.'''
    from app.infrastructure.storage import get_storage, LocalStorage, StorageConfig

    config = StorageConfig(backend='local', base_path=tmp_path)
    storage = LocalStorage(config)
    return JxlFallbackService(storage=storage, quality=85)


@pytest.mark.asyncio
async def test_generates_jpeg_fallback(
    fallback_service: JxlFallbackService,
    jxl_bytes: bytes,
    dek: bytes,
) -> None:
    '''The service should produce valid JPEG bytes from JXL input.'''
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
    dek: bytes,
) -> None:
    '''The second request should return the same bytes from cache.'''
    first = await fallback_service.get_fallback('cached-id', jxl_bytes, dek)
    second = await fallback_service.get_fallback('cached-id', jxl_bytes, dek)
    assert first == second


@pytest.mark.asyncio
async def test_invalidate_removes_cache(
    fallback_service: JxlFallbackService,
    jxl_bytes: bytes,
    dek: bytes,
) -> None:
    '''Invalidating a fallback should force regeneration on next request.'''
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
    with pytest.raises(JxlFallbackError):
        await fallback_service.get_fallback('bad-id', b'not an image', dek)
