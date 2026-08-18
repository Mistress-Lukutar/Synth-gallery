"""Integration tests for batch download (ZIP archives, single files, conversion).

Covers the reworked POST /api/items/batch-download endpoint:
- Album downloads include ALL album items inside a subfolder named after
  the album (regression: the frontend never used to send album_ids).
- A single selected file is served directly (no ZIP).
- Two or more files are packed into a ZIP (spooled, streamed).
- Images are converted to the requested format; videos pass through
  unchanged regardless of the chosen options.
- Duplicate titles are deduplicated inside the archive.
- JXL-stored images are served as-is for the default (jxl) format.
"""
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.application.services.item_service as item_service_module
import app.config as config_module
from app.infrastructure.services.jxl_encoder import is_jxl_encoding_available


def _csrf_headers(client: TestClient) -> dict[str, str]:
    '''CSRF headers helper matching the rest of the suite.'''
    from app.config import CSRF_COOKIE_NAME

    token = client.cookies.get(CSRF_COOKIE_NAME, '')
    return {'X-CSRF-Token': token}


def _upload(client: TestClient, folder_id: str, filename: str,
            data: bytes, content_type: str) -> dict:
    '''Upload one file and return the response JSON.'''
    resp = client.post(
        '/api/uploads',
        data={'folder_id': folder_id},
        files={'file': (filename, data, content_type)},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _batch_download(client: TestClient, payload: dict):
    '''POST /api/items/batch-download and return the response.'''
    return client.post(
        '/api/items/batch-download',
        json=payload,
        headers=_csrf_headers(client),
    )


def _make_mkv_bytes() -> bytes:
    '''Generate a tiny MKV via ffmpeg (skips if unavailable).'''
    from app.infrastructure.services.ffmpeg import is_ffmpeg_available

    if not is_ffmpeg_available():
        pytest.skip('ffmpeg/ffprobe not available on PATH')

    tmp_dir = Path(tempfile.mkdtemp())
    mkv_path = tmp_dir / 'sample.mkv'
    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=160x120:rate=5:duration=1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                str(mkv_path),
            ],
            check=True,
            timeout=60,
        )
        return mkv_path.read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _make_album(client: TestClient, folder_id: str, name: str,
                item_ids: list[str]) -> str:
    '''Create an album with the given items and return its id.'''
    resp = client.post(
        '/api/albums',
        json={'name': name, 'folder_id': folder_id, 'item_ids': item_ids},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()['album_id']


class TestAlbumDownload:
    '''Album selections become subfolders containing every album item.'''

    def test_album_download_includes_all_items_in_subfolder(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        item_ids = [
            _upload(authenticated_client, test_folder,
                    f'album_{i}.jpg', test_image_bytes, 'image/jpeg')['id']
            for i in range(4)
        ]
        album_id = _make_album(
            authenticated_client, test_folder, 'Trip 2026', item_ids
        )

        resp = _batch_download(authenticated_client, {
            'item_ids': [],
            'album_ids': [album_id],
            'options': {'format': 'png'},
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'].startswith('application/zip')
        assert 'Trip 2026.zip' in resp.headers['content-disposition']

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
            assert names == sorted(
                f'Trip 2026/album_{i}.png' for i in range(4)
            )
            for name in names:
                with Image.open(io.BytesIO(zf.read(name))) as img:
                    assert img.format == 'PNG'

    def test_single_album_zip_named_after_album(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        ids = [
            _upload(authenticated_client, test_folder,
                    f'one_{i}.jpg', test_image_bytes, 'image/jpeg')['id']
            for i in range(2)
        ]
        album_id = _make_album(
            authenticated_client, test_folder, 'Solo Album', ids
        )

        resp = _batch_download(authenticated_client, {
            'album_ids': [album_id],
            'options': {'format': 'jpeg'},
        })
        assert resp.status_code == 200
        assert 'Solo Album.zip' in resp.headers['content-disposition']

    def test_single_item_album_served_directly_without_zip(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        data = _upload(authenticated_client, test_folder,
                       'lone.jpg', test_image_bytes, 'image/jpeg')
        album_id = _make_album(
            authenticated_client, test_folder, 'Lone Album', [data['id']]
        )

        resp = _batch_download(authenticated_client, {
            'album_ids': [album_id],
            'options': {'format': 'png'},
        })

        # One resolved file -> direct download even though an album was chosen
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/png'
        assert 'lone.png' in resp.headers['content-disposition']


class TestSingleFileDownload:
    '''A one-file selection is served directly, without a ZIP.'''

    def test_single_photo_served_directly_as_jpeg(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        data = _upload(authenticated_client, test_folder,
                       'photo.jpg', test_image_bytes, 'image/jpeg')

        resp = _batch_download(authenticated_client, {
            'item_ids': [data['id']],
            'options': {'format': 'jpeg', 'jpeg_quality': 85},
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'image/jpeg'
        disposition = resp.headers['content-disposition']
        assert disposition.startswith('attachment')
        assert 'photo.jpg' in disposition
        # Not a ZIP: JPEG magic bytes
        assert resp.content.startswith(b'\xff\xd8')

    def test_single_photo_converted_to_webp(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        data = _upload(authenticated_client, test_folder,
                       'photo.jpg', test_image_bytes, 'image/jpeg')

        resp = _batch_download(authenticated_client, {
            'item_ids': [data['id']],
            'options': {'format': 'webp', 'webp_quality': 80},
        })

        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/webp'
        assert 'photo.webp' in resp.headers['content-disposition']
        with Image.open(io.BytesIO(resp.content)) as img:
            assert img.format == 'WEBP'

    def test_single_video_passthrough_despite_format(
        self, authenticated_client, test_folder
    ):
        mkv_bytes = _make_mkv_bytes()
        data = _upload(authenticated_client, test_folder,
                       'clip.mkv', mkv_bytes, 'video/x-matroska')

        resp = _batch_download(authenticated_client, {
            'item_ids': [data['id']],
            'options': {'format': 'jpeg'},
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'video/x-matroska'
        assert 'clip.mkv' in resp.headers['content-disposition']
        assert resp.content == mkv_bytes


class TestZipDownload:
    '''Multi-file selections are packed into a ZIP.'''

    def test_two_photos_returned_as_zip(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        ids = [
            _upload(authenticated_client, test_folder,
                    f'photo_{i}.jpg', test_image_bytes, 'image/jpeg')['id']
            for i in range(2)
        ]

        resp = _batch_download(authenticated_client, {
            'item_ids': ids,
            'options': {'format': 'jpeg'},
        })

        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('application/zip')
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
            assert names == ['photo_0.jpg', 'photo_1.jpg']
            for name in names:
                assert zf.read(name).startswith(b'\xff\xd8')

    def test_duplicate_titles_are_deduplicated(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        ids = [
            _upload(authenticated_client, test_folder,
                    'same.jpg', test_image_bytes, 'image/jpeg')['id']
            for _ in range(2)
        ]

        resp = _batch_download(authenticated_client, {
            'item_ids': ids,
            'options': {'format': 'png'},
        })

        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert sorted(zf.namelist()) == ['same (2).png', 'same.png']

    def test_video_and_photo_mixed_zip(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        mkv_bytes = _make_mkv_bytes()
        video = _upload(authenticated_client, test_folder,
                        'clip.mkv', mkv_bytes, 'video/x-matroska')
        photo = _upload(authenticated_client, test_folder,
                        'photo.jpg', test_image_bytes, 'image/jpeg')

        resp = _batch_download(authenticated_client, {
            'item_ids': [video['id'], photo['id']],
            'options': {'format': 'jpeg'},
        })

        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
            # The video keeps its original name; the photo is re-encoded.
            assert names == ['clip.mkv', 'photo.jpg']
            assert zf.read('clip.mkv') == mkv_bytes
            assert zf.read('photo.jpg').startswith(b'\xff\xd8')


class TestJxlStoredItems:
    '''Default (jxl) format serves JXL-stored photos unchanged.'''

    @pytest.fixture
    def jxl_enabled(self, monkeypatch):
        '''Enable JXL transcoding for the duration of a test.'''
        monkeypatch.setattr(config_module, 'USE_JXL', True)
        monkeypatch.setattr(item_service_module, 'USE_JXL', True)

    def test_jxl_item_downloaded_as_is_by_default(
        self, authenticated_client, test_folder, test_image_bytes, jxl_enabled
    ):
        if not is_jxl_encoding_available():
            pytest.skip('cjxl encoder not available')

        data = _upload(authenticated_client, test_folder,
                       'photo.jpg', test_image_bytes, 'image/jpeg')
        assert data['content_type'] == 'image/jxl'

        resp = _batch_download(authenticated_client, {
            'item_ids': [data['id']],
            # default options = format jxl
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'image/jxl'
        assert 'photo.jxl' in resp.headers['content-disposition']
        # JXL container magic (cjxl --container=1)
        assert resp.content[:4] == b'\x00\x00\x00\x0c'


class TestValidation:
    '''Input validation for the batch download endpoint.'''

    def test_empty_selection_returns_404(self, authenticated_client):
        resp = _batch_download(authenticated_client, {
            'item_ids': [],
            'album_ids': [],
        })
        assert resp.status_code == 404

    def test_unknown_format_rejected(self, authenticated_client):
        resp = _batch_download(authenticated_client, {
            'item_ids': ['whatever'],
            'options': {'format': 'tiff'},
        })
        assert resp.status_code == 422

    def test_quality_out_of_range_rejected(self, authenticated_client):
        resp = _batch_download(authenticated_client, {
            'item_ids': ['whatever'],
            'options': {'format': 'jpeg', 'jpeg_quality': 150},
        })
        assert resp.status_code == 422
