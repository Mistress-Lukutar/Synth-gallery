'''
File:   test_batch_download_metadata.py
Brief:  Integration tests: DB metadata embedded into downloaded images.
Author: Mistress-Lukutar
Date:   2026-11-10
Version: v0.1.0

Covers the download-time metadata embedding feature end-to-end through
POST /api/items/batch-download:
- With ``embed_metadata: true`` and a converted format (jpeg), the item's
  DB metadata (png_text_chunks + taken_at, as edited via the metadata
  editor) lands in the downloaded file as EXIF tags.
- With ``embed_metadata: false`` the conversion output carries no DB tags.
- ``original`` format passes the stored bytes through untouched (no
  embedding, no re-encode).
'''
import io

import pytest
from PIL import Image
from PIL.ExifTags import IFD
from fastapi.testclient import TestClient


def _csrf_headers(client: TestClient) -> dict[str, str]:
    '''CSRF headers helper matching the rest of the suite.'''
    from app.config import CSRF_COOKIE_NAME

    token = client.cookies.get(CSRF_COOKIE_NAME, '')
    return {'X-CSRF-Token': token}


def _make_jpeg_bytes() -> bytes:
    '''Create a small JPEG without any EXIF.'''
    buf = io.BytesIO()
    Image.new('RGB', (64, 48), color='red').save(
        buf, format='JPEG', quality=90
    )
    return buf.getvalue()


def _upload_jpeg(client: TestClient, folder_id: str) -> dict:
    '''Upload a plain JPEG and return the response JSON.'''
    resp = client.post(
        '/api/uploads',
        data={'folder_id': folder_id},
        files={'file': ('meta-test.jpg', _make_jpeg_bytes(), 'image/jpeg')},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_metadata(client: TestClient, item_id: str) -> None:
    '''Write DB metadata via the metadata editor endpoint.'''
    resp = client.put(
        f'/api/items/{item_id}/metadata',
        json={
            'taken_at': '2026-01-02T03:04:05',
            'png_text_chunks': {
                'camera': 'TestCam TestModel',
                'iso': '800',
                'user_comment': 'embedded hello',
            },
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text


def _batch_download(client: TestClient, item_id: str, options: dict):
    '''POST /api/items/batch-download for one item.'''
    return client.post(
        '/api/items/batch-download',
        json={'item_ids': [item_id], 'options': options},
        headers=_csrf_headers(client),
    )


def test_converted_download_embeds_db_metadata(
    authenticated_client: TestClient, test_folder: dict
):
    '''embed_metadata=true + jpeg target: DB tags land as EXIF.'''
    item = _upload_jpeg(authenticated_client, test_folder)
    item_id = item['id']
    _set_metadata(authenticated_client, item_id)

    resp = _batch_download(
        authenticated_client, item_id,
        {'format': 'jpeg', 'embed_metadata': True},
    )
    assert resp.status_code == 200, resp.text
    assert 'attachment' in resp.headers.get('Content-Disposition', '')

    img = Image.open(io.BytesIO(resp.content))
    assert img.format == 'JPEG'
    exif = img.getexif()
    assert exif[0x010F] == 'TestCam'          # Make
    assert exif[0x0110] == 'TestModel'        # Model
    sub = dict(exif.get_ifd(IFD.Exif))
    assert sub[0x8827] == 800                 # ISOSpeedRatings
    assert sub[0x9003] == '2026:01:02 03:04:05'  # DateTimeOriginal


def test_embed_metadata_false_skips_embedding(
    authenticated_client: TestClient, test_folder: dict
):
    '''embed_metadata=false: conversion output carries no DB EXIF tags.'''
    item = _upload_jpeg(authenticated_client, test_folder)
    item_id = item['id']
    _set_metadata(authenticated_client, item_id)

    resp = _batch_download(
        authenticated_client, item_id,
        {'format': 'jpeg', 'embed_metadata': False},
    )
    assert resp.status_code == 200, resp.text
    exif = Image.open(io.BytesIO(resp.content)).getexif()
    assert 0x010F not in exif
    assert 0x8827 not in dict(exif.get_ifd(IFD.Exif))


def test_original_format_passes_through_untouched(
    authenticated_client: TestClient, test_folder: dict
):
    '''original format: stored bytes served as-is, no embedding applied.'''
    item = _upload_jpeg(authenticated_client, test_folder)
    item_id = item['id']
    _set_metadata(authenticated_client, item_id)

    resp = _batch_download(
        authenticated_client, item_id, {'format': 'original'}
    )
    assert resp.status_code == 200, resp.text
    # The stored upload had no EXIF; embedding must not have added any.
    exif = Image.open(io.BytesIO(resp.content)).getexif()
    assert 0x010F not in exif
