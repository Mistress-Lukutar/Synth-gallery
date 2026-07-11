'''
File:   test_upload_jxl.py
Brief:  Integration tests for JPEG XL upload and fallback serving.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.1.0
'''

from __future__ import annotations

import io
import struct
import zlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.application.services.item_service as item_service_module
import app.config as config_module
from app.config import CSRF_COOKIE_NAME


def _csrf_headers(client: TestClient) -> dict[str, str]:
    '''Get CSRF headers for POST requests.'''
    token = client.cookies.get(CSRF_COOKIE_NAME, '')
    return {'X-CSRF-Token': token}


def _make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xffffffff
    return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)


def _build_png(*extra_chunks: bytes) -> bytes:
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)
    ihdr = _make_png_chunk(b'IHDR', ihdr_data)
    idat_data = zlib.compress(b'\x00\x00')
    idat = _make_png_chunk(b'IDAT', idat_data)
    iend = _make_png_chunk(b'IEND', b'')
    return signature + ihdr + b''.join(extra_chunks) + idat + iend


def _png_with_text_chunks() -> bytes:
    text_chunk = _make_png_chunk(b'tEXt', b'prompt\x00masterpiece, best quality')
    workflow_chunk = _make_png_chunk(b'zTXt', b'workflow\x00\x00' + zlib.compress(b'{"nodes": []}'))
    return _build_png(text_chunk, workflow_chunk)


@pytest.fixture
def jxl_enabled(monkeypatch):
    '''Enable JXL transcoding for the duration of a test.'''
    monkeypatch.setattr(config_module, 'USE_JXL', True)
    monkeypatch.setattr(item_service_module, 'USE_JXL', True)


@pytest.fixture
def jpeg_bytes() -> bytes:
    '''Return a JPEG byte string for upload tests.'''
    img = Image.new('RGB', (100, 80), color='red')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


@pytest.fixture
def png_bytes_with_text() -> bytes:
    '''Return a PNG byte string with tEXt/zTXt chunks.'''
    return _png_with_text_chunks()


class TestJxlUpload:
    '''Test JXL transcoding on upload.'''

    def test_upload_transcodes_jpeg_to_jxl(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        jpeg_bytes: bytes,
        jxl_enabled,
    ) -> None:
        '''Uploading a JPEG with USE_JXL enabled should store it as image/jxl.'''
        response = authenticated_client.post(
            '/upload',
            data={'folder_id': test_folder},
            headers=_csrf_headers(authenticated_client),
            files={'file': ('test.jpg', jpeg_bytes, 'image/jpeg')},
        )

        assert response.status_code == 200
        data = response.json()
        assert data['media_type'] == 'image'

        # Fetch metadata to verify content type
        meta_response = authenticated_client.get(f'/api/items/{data["id"]}')
        assert meta_response.status_code == 200
        meta = meta_response.json()
        assert meta['content_type'] == 'image/jxl'

    def test_upload_keeps_original_without_jxl(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        jpeg_bytes: bytes,
    ) -> None:
        '''Uploading with USE_JXL disabled should keep the original JPEG type.'''
        response = authenticated_client.post(
            '/upload',
            data={'folder_id': test_folder},
            headers=_csrf_headers(authenticated_client),
            files={'file': ('test.jpg', jpeg_bytes, 'image/jpeg')},
        )

        assert response.status_code == 200
        data = response.json()

        meta_response = authenticated_client.get(f'/api/items/{data["id"]}')
        assert meta_response.status_code == 200
        meta = meta_response.json()
        assert meta['content_type'] == 'image/jpeg'


class TestJxlFallbackServing:
    '''Test Accept-based JXL serving and JPEG fallback.'''

    @pytest.fixture
    def jxl_photo_id(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        jpeg_bytes: bytes,
        jxl_enabled,
    ) -> str:
        '''Upload a JPEG and return its item ID as JXL.'''
        response = authenticated_client.post(
            '/upload',
            data={'folder_id': test_folder},
            headers=_csrf_headers(authenticated_client),
            files={'file': ('test.jpg', jpeg_bytes, 'image/jpeg')},
        )
        assert response.status_code == 200
        return response.json()['id']

    def test_serves_jxl_when_accepted(
        self,
        authenticated_client: TestClient,
        jxl_photo_id: str,
    ) -> None:
        '''Request with Accept: image/jxl should receive a JXL response.'''
        response = authenticated_client.get(
            f'/files/{jxl_photo_id}',
            headers={'Accept': 'image/jxl'},
        )

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/jxl'
        assert (
            response.content.startswith(b'\x00\x00\x00\x0cJXL ')
            or response.content.startswith(b'\xff\x0a')
        )

    def test_serves_jpeg_fallback_by_default(
        self,
        authenticated_client: TestClient,
        jxl_photo_id: str,
    ) -> None:
        '''Request without JXL support should receive a JPEG fallback.'''
        response = authenticated_client.get(
            f'/files/{jxl_photo_id}',
            headers={'Accept': 'image/jpeg,image/webp,*/*'},
        )

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/jpeg'
        assert response.content.startswith(b'\xff\xd8')

    def test_serves_jpeg_fallback_with_query_param(
        self,
        authenticated_client: TestClient,
        jxl_photo_id: str,
    ) -> None:
        '''Request with ?format=jpeg should force a JPEG response.'''
        response = authenticated_client.get(
            f'/files/{jxl_photo_id}?format=jpeg',
            headers={'Accept': 'image/jxl'},
        )

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/jpeg'
        assert response.content.startswith(b'\xff\xd8')

    def test_thumbnail_is_served_as_jpeg(
        self,
        authenticated_client: TestClient,
        jxl_photo_id: str,
    ) -> None:
        '''Thumbnails are always JPEG, even when the original is JXL.'''
        response = authenticated_client.get(
            f'/files/{jxl_photo_id}/thumbnail',
        )

        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/jpeg'
        assert response.content.startswith(b'\xff\xd8')


class TestPngTextChunkPreservation:
    '''Test that PNG text chunks are captured before JXL transcoding.'''

    def test_upload_extracts_png_text_chunks(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        png_bytes_with_text: bytes,
    ) -> None:
        '''PNG tEXt/zTXt chunks should be available via metadata API.'''
        response = authenticated_client.post(
            '/upload',
            data={'folder_id': test_folder},
            headers=_csrf_headers(authenticated_client),
            files={'file': ('test.png', png_bytes_with_text, 'image/png')},
        )

        assert response.status_code == 200
        item_id = response.json()['id']

        meta_response = authenticated_client.get(f'/api/items/{item_id}/metadata')
        assert meta_response.status_code == 200
        meta = meta_response.json()
        assert meta['png_text_chunks'] == {
            'prompt': 'masterpiece, best quality',
            'workflow': '{"nodes": []}',
        }

    def test_jxl_transcode_preserves_png_text_chunks_in_db(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        png_bytes_with_text: bytes,
        jxl_enabled,
    ) -> None:
        '''When USE_JXL transcodes PNG to JXL, text chunks remain in metadata.'''
        response = authenticated_client.post(
            '/upload',
            data={'folder_id': test_folder},
            headers=_csrf_headers(authenticated_client),
            files={'file': ('test.png', png_bytes_with_text, 'image/png')},
        )

        assert response.status_code == 200
        item_id = response.json()['id']

        # File should now be JXL
        file_response = authenticated_client.get(
            f'/files/{item_id}',
            headers={'Accept': 'image/jxl'},
        )
        assert file_response.status_code == 200
        assert file_response.headers['content-type'] == 'image/jxl'

        # But metadata still has the original PNG text chunks
        meta_response = authenticated_client.get(f'/api/items/{item_id}/metadata')
        assert meta_response.status_code == 200
        meta = meta_response.json()
        assert meta['png_text_chunks'] == {
            'prompt': 'masterpiece, best quality',
            'workflow': '{"nodes": []}',
        }
