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


def _make_jpeg_bytes(color: str) -> bytes:
    '''Create a small JPEG of the given color.'''
    buf = io.BytesIO()
    Image.new('RGB', (64, 48), color=color).save(buf, format='JPEG', quality=90)
    return buf.getvalue()


@pytest.fixture
def jxl_enabled(monkeypatch):
    '''Enable JXL transcoding for the duration of a test.'''
    monkeypatch.setattr(config_module, 'USE_JXL', True)
    monkeypatch.setattr(item_service_module, 'USE_JXL', True)


class TestOriginalFormat:
    '''The default "original" option serves stored bytes untouched.'''

    def test_original_zip_contains_untouched_bytes(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        red = _make_jpeg_bytes('red')
        blue = _make_jpeg_bytes('blue')
        a = _upload(authenticated_client, test_folder, 'a.jpg', red, 'image/jpeg')
        b = _upload(authenticated_client, test_folder, 'b.jpg', blue, 'image/jpeg')

        resp = _batch_download(authenticated_client, {
            'item_ids': [a['id'], b['id']],
            'options': {'format': 'original'},
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'].startswith('application/zip')
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert sorted(zf.namelist()) == ['a.jpg', 'b.jpg']
            # Media payloads are incompressible: entries must be STORED.
            for info in zf.infolist():
                assert info.compress_type == zipfile.ZIP_STORED
            assert zf.read('a.jpg') == red
            assert zf.read('b.jpg') == blue

    def test_default_options_download_as_stored(
        self, authenticated_client, test_folder, test_image_bytes
    ):
        uploaded = _upload(authenticated_client, test_folder,
                           'photo.jpg', test_image_bytes, 'image/jpeg')

        resp = _batch_download(authenticated_client, {
            'item_ids': [uploaded['id']],
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'image/jpeg'
        assert 'photo.jpg' in resp.headers['content-disposition']
        # No re-encode: the served bytes match the upload bit-for-bit.
        assert resp.content == test_image_bytes

    def test_original_format_is_accepted_validation(
        self, authenticated_client
    ):
        resp = _batch_download(authenticated_client, {
            'item_ids': ['whatever'],
            'options': {'format': 'original'},
        })
        # 404 (no accessible items), NOT 422 — the format itself is valid.
        assert resp.status_code == 404


class TestJxlReconstruction:
    '''jxl -> jpeg downloads rebuild the original JPEG bit-exactly.'''

    def test_jxl_to_jpeg_reconstruction_is_bit_exact(
        self, authenticated_client, test_folder, test_image_bytes, jxl_enabled
    ):
        if not is_jxl_encoding_available():
            pytest.skip('cjxl encoder not available')

        data = _upload(authenticated_client, test_folder,
                       'photo.jpg', test_image_bytes, 'image/jpeg')
        assert data['content_type'] == 'image/jxl'

        resp = _batch_download(authenticated_client, {
            'item_ids': [data['id']],
            'options': {'format': 'jpeg', 'jpeg_quality': 85},
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'image/jpeg'
        assert 'photo.jpg' in resp.headers['content-disposition']
        # djxl reconstruction: the stored JPEG comes back byte-for-byte.
        assert resp.content == test_image_bytes


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
    '''Default (original) format serves JXL-stored photos unchanged.'''

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
            # default options = format original (no conversion)
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'] == 'image/jxl'
        assert 'photo.jxl' in resp.headers['content-disposition']
        # JXL container magic (cjxl --container=1)
        assert resp.content[:4] == b'\x00\x00\x00\x0c'


class TestNullNameAlbums:
    '''Albums with falsy names must not crash the download.

    NULL names are no longer representable (NOT NULL migration), but
    empty-string names can still exist as legacy data and exercise the
    same sanitizer fallback path.
    '''

    def _insert_album(self, db, album_id, folder_id, user_id, name=''):
        db.execute(
            '''INSERT INTO albums (id, name, folder_id, user_id, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))''',
            (album_id, name, folder_id, user_id),
        )

    def test_empty_name_album_downloads_into_unique_subfolder(
        self, authenticated_client, test_folder, test_user, test_image_bytes
    ):
        from app.database import create_connection

        # Two photos so the selection resolves to a ZIP (a single file
        # would be served directly by design).
        item_ids = [
            _upload(authenticated_client, test_folder,
                    f'u{i}.jpg', test_image_bytes, 'image/jpeg')['id']
            for i in range(2)
        ]

        album_id = 'aaaaaaaa-dead-beef-0000-000000000001'
        db = create_connection()
        try:
            self._insert_album(db, album_id, test_folder, test_user['id'])
            for position, item_id in enumerate(item_ids):
                db.execute(
                    'INSERT INTO album_items (album_id, item_id, position) '
                    'VALUES (?, ?, ?)',
                    (album_id, item_id, position),
                )
            db.commit()
        finally:
            db.close()

        resp = _batch_download(authenticated_client, {
            'album_ids': [album_id],
            'options': {'format': 'png'},
        })

        assert resp.status_code == 200, resp.text
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            expected_dir = f'album-{album_id[:8]}'
            assert sorted(zf.namelist()) == [
                f'{expected_dir}/u0.png',
                f'{expected_dir}/u1.png',
            ]

    def test_two_nameless_albums_do_not_merge(
        self, authenticated_client, test_folder, test_user, test_image_bytes
    ):
        from app.database import create_connection

        photos = [
            _upload(authenticated_client, test_folder,
                    f'u{i}.jpg', test_image_bytes, 'image/jpeg')['id']
            for i in range(2)
        ]
        album_ids = [
            'aaaaaaaa-0000-0000-0000-000000000001',
            'bbbbbbbb-0000-0000-0000-000000000002',
        ]

        db = create_connection()
        try:
            for album_id, item_id in zip(album_ids, photos):
                self._insert_album(db, album_id, test_folder, test_user['id'])
                db.execute(
                    'INSERT INTO album_items (album_id, item_id, position) '
                    'VALUES (?, ?, 0)',
                    (album_id, item_id),
                )
            db.commit()
        finally:
            db.close()

        resp = _batch_download(authenticated_client, {
            'album_ids': album_ids,
            'options': {'format': 'png'},
        })

        assert resp.status_code == 200, resp.text
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
            # Two distinct subfolders despite both albums being nameless
            assert names == [
                'album-aaaaaaaa/u0.png',
                'album-bbbbbbbb/u1.png',
            ]


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
