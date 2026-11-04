'''
File:   test_upload_metadata.py
Brief:  Upload metadata integration tests (EXIF, video container tags).
Author: Mistress-Lukutar
Date:   2026-11-03
Version: v2.1.0

Verifies that image and video uploads carry their source metadata into
the database (``item_media.png_text_chunks`` / ``taken_at``):
- JPEG EXIF (camera, ISO, exposure, GPS) is stored as text chunks
- EXIF DateTimeOriginal promotes to ``taken_at``
- WebP EXIF is stored the same way
- Videos store ffprobe container tags
'''
import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.config import CSRF_COOKIE_NAME


def _csrf_headers(client: TestClient) -> dict:
    """Get CSRF headers for POST requests."""
    token = client.cookies.get(CSRF_COOKIE_NAME, "")
    return {"X-CSRF-Token": token}


def _camera_exif() -> "Image.Exif":
    """Build an EXIF block the way a phone camera would emit it."""
    exif = Image.Exif()
    exif[271] = "TestMake"  # Make
    exif[272] = "TestModel"  # Model
    exif[0x8769] = {  # EXIF sub-IFD
        0x9003: "2026:05:01 12:34:56",  # DateTimeOriginal
        0x829a: IFDRational(1, 250),    # ExposureTime
        0x8827: 400,                    # ISOSpeedRatings
    }
    exif[0x8825] = {  # GPS IFD
        1: "N",
        2: (IFDRational(55, 1), IFDRational(45, 1),
            IFDRational(18000, 1000)),
        3: "E",
        4: (IFDRational(37, 1), IFDRational(36, 1),
            IFDRational(24000, 1000)),
    }
    return exif


def _exif_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, format="JPEG", exif=_camera_exif())
    return buf.getvalue()


def _exif_webp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, format="WEBP", exif=_camera_exif())
    return buf.getvalue()


def _upload(client: TestClient, folder_id: str, filename: str,
            data: bytes, content_type: str) -> dict:
    response = client.post(
        "/api/uploads",
        data={"folder_id": folder_id},
        headers=_csrf_headers(client),
        files={"file": (filename, data, content_type)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_metadata(client: TestClient, item_id: str) -> dict:
    response = client.get(f"/api/items/{item_id}/metadata")
    assert response.status_code == 200, response.text
    return response.json()


class TestImageUploadMetadata:
    def test_jpeg_exif_stored_as_text_chunks(
        self, authenticated_client: TestClient, test_folder: str
    ):
        """JPEG EXIF (camera, ISO, exposure, GPS) lands in the database."""
        item = _upload(
            authenticated_client, test_folder,
            "exif.jpg", _exif_jpeg(), "image/jpeg",
        )
        chunks = _get_metadata(
            authenticated_client, item["id"]
        )["png_text_chunks"]

        assert chunks["camera"] == "TestMake TestModel"
        assert chunks["iso"] == "400"
        assert chunks["exposure_time"] == "1/250"
        gps = json.loads(chunks["gps"])
        assert gps["latitude"] == pytest.approx(55.755, abs=1e-6)
        assert gps["longitude"] == pytest.approx(37.606667, abs=1e-6)

    def test_jpeg_datetime_original_promotes_to_taken_at(
        self, authenticated_client: TestClient, test_folder: str
    ):
        """taken_at comes from EXIF DateTimeOriginal, not upload time."""
        item = _upload(
            authenticated_client, test_folder,
            "exif.jpg", _exif_jpeg(), "image/jpeg",
        )
        taken_at = _get_metadata(authenticated_client, item["id"])["taken_at"]
        assert taken_at is not None
        assert taken_at.startswith("2026-05-01T12:34:56")

    def test_webp_exif_stored_as_text_chunks(
        self, authenticated_client: TestClient, test_folder: str
    ):
        item = _upload(
            authenticated_client, test_folder,
            "exif.webp", _exif_webp(), "image/webp",
        )
        chunks = _get_metadata(
            authenticated_client, item["id"]
        )["png_text_chunks"]
        assert chunks["camera"] == "TestMake TestModel"
        assert chunks["iso"] == "400"


def _make_test_video(tmp_path: Path) -> bytes:
    """Render a tiny MP4 with container tags via ffmpeg."""
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.3:size=128x128:rate=10",
            "-metadata", "comment=MetadataTestVideo",
            "-metadata", "creation_time=2026-06-01T10:00:00Z",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


class TestVideoUploadMetadata:
    def test_video_container_tags_stored(
        self, authenticated_client: TestClient, test_folder: str, tmp_path
    ):
        """ffprobe container tags are stored like PNG text chunks."""
        try:
            video_bytes = _make_test_video(tmp_path)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("ffmpeg not available")

        item = _upload(
            authenticated_client, test_folder,
            "clip.mp4", video_bytes, "video/mp4",
        )
        chunks = _get_metadata(
            authenticated_client, item["id"]
        )["png_text_chunks"]

        assert chunks["comment"] == "MetadataTestVideo"
        assert chunks["creation_time"].startswith("2026-06-01")
