"""Integration tests for MKV video upload and HTTP Range serving.

Covers the new chunked AEAD envelope end to end:
- Upload of an MKV file (Matroska container)
- HEAD reports correct Content-Length and Accept-Ranges
- GET with Range header returns 206 Partial Content with correct Content-Range
- Full GET returns the entire plaintext matching the source bytes
- Thumbnail endpoint returns a valid JPEG
"""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import CSRF_COOKIE_NAME
from app.infrastructure.services.encryption import (
    ENVELOPE_MAGIC,
    EncryptionService,
)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    '''CSRF headers helper matching the rest of the suite.'''
    token = client.cookies.get(CSRF_COOKIE_NAME, '')
    return {'X-CSRF-Token': token}


def _make_mkv_bytes() -> bytes:
    '''Generate a short MKV (H.264 in Matroska) via ffmpeg.

    Skips the test if ffmpeg is unavailable.
    '''
    from app.infrastructure.services.ffmpeg import is_ffmpeg_available

    if not is_ffmpeg_available():
        pytest.skip('ffmpeg/ffprobe not available on PATH')

    tmp_dir = Path(tempfile.mkdtemp())
    mkv_path = tmp_dir / 'sample.mkv'
    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=10:duration=5',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                str(mkv_path),
            ],
            check=True,
            timeout=60,
        )
        return mkv_path.read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope='module')
def mkv_bytes() -> bytes:
    '''Real MKV bytes shared across tests in this module.'''
    return _make_mkv_bytes()


def test_mkv_upload_creates_video_item(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''MKV upload should produce a video-typed item with the Matroska MIME.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data['media_type'] == 'video'
    assert data['content_type'] == 'video/x-matroska'
    assert data['filename'] == data['id']


def test_mkv_stored_in_chunked_envelope(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''Encrypted bytes on disk must start with the SGE1 envelope magic.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    assert response.status_code == 200
    item_id = response.json()['id']

    from app.infrastructure.storage import get_storage
    storage = get_storage()
    path = storage.get_path(item_id, 'uploads')
    assert path.exists()
    on_disk = path.read_bytes()
    assert on_disk[:4] == ENVELOPE_MAGIC
    # And the encrypted bytes must NOT match the plaintext MKV header.
    assert on_disk[:4] != mkv_bytes[:4]


def test_head_reports_size_and_accept_ranges(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''HEAD /files/{id} returns correct plaintext length and Accept-Ranges.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    head = authenticated_client.head(f'/files/{item_id}')
    assert head.status_code == 200
    assert head.headers.get('accept-ranges') == 'bytes'
    assert int(head.headers['content-length']) == len(mkv_bytes)


def test_range_request_returns_partial_content(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''GET with Range returns 206 + Content-Range + exact bytes.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    total = len(mkv_bytes)
    r = authenticated_client.get(
        f'/files/{item_id}', headers={'Range': 'bytes=0-99'}
    )
    assert r.status_code == 206
    assert r.headers['content-range'] == f'bytes 0-99/{total}'
    assert r.headers['content-length'] == '100'
    assert r.content == mkv_bytes[0:100]


def test_range_request_middle_slice(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''A range in the middle of the file returns the right bytes.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    mid = len(mkv_bytes) // 2
    r = authenticated_client.get(
        f'/files/{item_id}', headers={'Range': f'bytes={mid}-{mid + 999}'}
    )
    assert r.status_code == 206
    assert r.content == mkv_bytes[mid:mid + 1000]


def test_suffix_range_returns_last_bytes(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''bytes=-N returns the last N bytes.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    r = authenticated_client.get(
        f'/files/{item_id}', headers={'Range': 'bytes=-50'}
    )
    assert r.status_code == 206
    assert r.headers['content-length'] == '50'
    assert r.content == mkv_bytes[-50:]


def test_full_get_returns_complete_plaintext(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''A GET without Range returns the entire file, byte-identical.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    r = authenticated_client.get(f'/files/{item_id}')
    assert r.status_code == 200
    assert r.content == mkv_bytes


def test_mkv_thumbnail_is_jpeg(
    authenticated_client: TestClient,
    test_folder: str,
    csrf_token: str,
    mkv_bytes: bytes,
):
    '''Thumbnail endpoint produces a valid JPEG even for MKV.'''
    response = authenticated_client.post(
        '/api/uploads',
        data={'folder_id': test_folder},
        files={'file': ('sample.mkv', mkv_bytes, 'video/x-matroska')},
        headers={'X-CSRF-Token': csrf_token},
    )
    item_id = response.json()['id']

    r = authenticated_client.get(f'/files/{item_id}/thumbnail')
    assert r.status_code == 200
    assert r.content[:3] == b'\xff\xd8\xff'  # JPEG magic
