'''
File:   jxl_fallback_service.py
Brief:  Generate and cache JPEG fallbacks for JXL originals.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.2.0
'''

from __future__ import annotations

import io
import logging

from PIL import Image

from app.config import JXL_FALLBACK_QUALITY
from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.services.jxl import decode_jxl
from app.infrastructure.storage import get_storage
from app.infrastructure.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class JxlFallbackError(Exception):
    '''Raised when JPEG fallback generation fails.'''


class JxlFallbackService:
    '''Generate on-demand JPEG fallbacks for JXL originals.

    Fallbacks are cached in the configured storage backend under the
    ``fallbacks`` folder and encrypted with the same DEK as the original.
    '''

    _FOLDER = 'fallbacks'

    def __init__(
        self,
        storage: StorageInterface | None = None,
        quality: int = JXL_FALLBACK_QUALITY,
    ) -> None:
        '''Initialize the service.

        Args:
            storage: Storage backend. Defaults to the configured backend.
            quality: JPEG quality for generated fallbacks.
        '''
        self.storage = storage or get_storage()
        self.quality = quality

    async def get_fallback(
        self,
        photo_id: str,
        jxl_bytes: bytes,
        dek: bytes,
    ) -> bytes:
        '''Return a JPEG fallback for the given JXL bytes.

        Returns a cached fallback when available, otherwise decodes the JXL
        image, encodes it as JPEG and caches the encrypted result.

        Args:
            photo_id: UUID of the media item.
            jxl_bytes: Decrypted JXL bytes.
            dek: Data encryption key for caching the fallback.

        Returns:
            JPEG bytes.

        Raises:
            JxlFallbackError: If decoding or encoding fails.
        '''
        cached = await self._get_cached(photo_id, dek)
        if cached is not None:
            return cached

        try:
            jpeg_bytes = self._jxl_to_jpeg(jxl_bytes)
        except Exception as exc:
            logger.exception('Failed to generate JPEG fallback for %s', photo_id)
            raise JxlFallbackError(f'Fallback generation failed: {exc}') from exc

        await self._cache_fallback(photo_id, jpeg_bytes, dek)
        return jpeg_bytes

    async def invalidate(self, photo_id: str) -> None:
        '''Remove cached fallback for the given item.

        Args:
            photo_id: UUID of the media item.
        '''
        if not self.storage.exists(photo_id, self._FOLDER):
            return

        try:
            await self.storage.delete(photo_id, self._FOLDER)
        except Exception as exc:
            logger.warning('Failed to invalidate fallback for %s: %s', photo_id, exc)

    async def _get_cached(
        self,
        photo_id: str,
        dek: bytes,
    ) -> bytes | None:
        '''Return decrypted cached fallback or None if unavailable.

        Args:
            photo_id: UUID of the media item.
            dek: Data encryption key.

        Returns:
            Decrypted JPEG bytes or None.
        '''
        if not self.storage.exists(photo_id, self._FOLDER):
            return None

        try:
            encrypted = await self.storage.download(photo_id, self._FOLDER)
            return EncryptionService.decrypt_bytes(encrypted, dek)
        except Exception as exc:
            logger.warning('Cached fallback for %s is unusable: %s', photo_id, exc)
            await self.invalidate(photo_id)
            return None

    async def _cache_fallback(
        self,
        photo_id: str,
        jpeg_bytes: bytes,
        dek: bytes,
    ) -> None:
        '''Encrypt and store a generated fallback.

        Args:
            photo_id: UUID of the media item.
            jpeg_bytes: Generated JPEG bytes.
            dek: Data encryption key.
        '''
        try:
            encrypted = EncryptionService.encrypt_bytes(jpeg_bytes, dek)
            await self.storage.upload(
                photo_id,
                encrypted,
                folder=self._FOLDER,
                content_type='image/jpeg',
            )
        except Exception as exc:
            logger.warning('Failed to cache fallback for %s: %s', photo_id, exc)

    def _jxl_to_jpeg(self, jxl_bytes: bytes) -> bytes:
        '''Decode JXL bytes and re-encode as JPEG.

        Args:
            jxl_bytes: Decoded JXL image bytes.

        Returns:
            JPEG bytes.

        Raises:
            JxlFallbackError: If the image cannot be decoded or encoded.
        '''
        try:
            png_bytes = decode_jxl(jxl_bytes)
            with Image.open(io.BytesIO(png_bytes)) as img:
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=self.quality)
                return output.getvalue()
        except Exception as exc:
            raise JxlFallbackError(f'JPEG encoding failed: {exc}') from exc
