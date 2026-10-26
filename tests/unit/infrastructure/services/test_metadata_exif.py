"""
Live EXIF probe unit tests.

Tests the pure helpers behind the AI chat ``get_item_metadata`` tool in
isolation: GPS decimal conversion, EXIF UserComment charset prefixes and
the summary extractor on a real (Pillow-generated) JPEG.
No database or filesystem dependencies.
"""
import io

import pytest
from PIL import Image

from app.infrastructure.services.metadata import (
    _decode_user_comment,
    extract_image_exif,
    gps_to_decimal,
)


class TestGpsToDecimal:
    def test_north_east_string_keys(self):
        gps = {
            "GPSLatitudeRef": "N",
            "GPSLatitude": (55.0, 45.0, 18.0),
            "GPSLongitudeRef": "E",
            "GPSLongitude": (37.0, 36.0, 24.0),
            "GPSAltitude": (150, 1),
        }
        result = gps_to_decimal(gps)
        assert result["latitude"] == pytest.approx(55.755, abs=1e-6)
        assert result["longitude"] == pytest.approx(37.606667, abs=1e-6)
        assert result["altitude_m"] == 150.0

    def test_south_west_are_negative(self):
        gps = {
            "GPSLatitudeRef": "S",
            "GPSLatitude": (33, 52, 4.0),
            "GPSLongitudeRef": "W",
            "GPSLongitude": (151, 12, 26.0),
        }
        result = gps_to_decimal(gps)
        assert result["latitude"] < 0
        assert result["longitude"] < 0

    def test_numeric_keys_accepted(self):
        # Old Pillow / _getexif() paths key the GPS IFD by numeric ids
        # (1=LatitudeRef, 2=Latitude, 3=LongitudeRef, 4=Longitude).
        gps = {
            1: "N",
            2: (55.0, 0.0, 0.0),
            3: "E",
            4: (37.0, 0.0, 0.0),
        }
        result = gps_to_decimal(gps)
        assert result["latitude"] == 55.0
        assert result["longitude"] == 37.0

    def test_altitude_below_sea_level_is_negative(self):
        gps = {
            "GPSLatitudeRef": "N",
            "GPSLatitude": (10, 0, 0.0),
            "GPSLongitudeRef": "E",
            "GPSLongitude": (10, 0, 0.0),
            "GPSAltitudeRef": 1,
            "GPSAltitude": (430.5, 1),
        }
        assert gps_to_decimal(gps)["altitude_m"] == -430.5

    def test_missing_coordinates_return_none(self):
        assert gps_to_decimal({}) is None
        assert gps_to_decimal({"GPSLatitude": (1, 2, 3.0)}) is None


class TestDecodeUserComment:
    def test_unicode_prefix_utf16_be(self):
        raw = b"UNICODE\x00" + "привет".encode("utf-16-be") + b"\x00\x00"
        assert _decode_user_comment(raw) == "привет"

    def test_ascii_prefix(self):
        # A1111-style prompt in an ASCII UserComment.
        raw = b"ASCII\x00\x00\x00prompt: red fox, detailed"
        assert _decode_user_comment(raw) == "prompt: red fox, detailed"

    def test_plain_string_passthrough(self):
        assert _decode_user_comment("already decoded") == "already decoded"

    def test_empty_returns_none(self):
        assert _decode_user_comment(b"ASCII\x00\x00\x00\x00\x00") is None
        assert _decode_user_comment(None) is None


class TestExtractImageExif:
    def _jpeg(self, exif: "Image.Exif") -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, format="JPEG", exif=exif)
        return buf.getvalue()

    def test_camera_make_and_model(self):
        exif = Image.Exif()
        exif[271] = "TestMake"  # Make
        exif[272] = "TestModel"  # Model
        result = extract_image_exif(self._jpeg(exif))
        assert result["camera"] == "TestMake TestModel"
        assert result["gps"] is None

    def test_image_without_exif_yields_nones(self):
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, format="PNG")
        result = extract_image_exif(buf.getvalue())
        assert result["camera"] is None
        assert result["gps"] is None
        assert result["user_comment"] is None

    def test_non_image_bytes_return_empty_summary(self):
        result = extract_image_exif(b"not an image at all")
        assert result["camera"] is None
        assert result["gps"] is None
