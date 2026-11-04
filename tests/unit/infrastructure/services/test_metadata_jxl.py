'''
File:   test_metadata_jxl.py
Brief:  Unit tests for JXL metadata extraction (ISOBMFF box parsing).
Author: Mistress-Lukutar
Date:   2026-11-02
Version: v2.1.0
'''

import brotli
import json
import struct
import zlib
from datetime import datetime

import pytest
from PIL import Image

from app.infrastructure.services.metadata import (
    extract_image_text_chunks,
    extract_jxl_exif_summary,
    extract_jxl_text_chunks,
    extract_taken_date,
)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------

def _box(box_type: bytes, payload: bytes) -> bytes:
    """Build an ISOBMFF box: [size(4B BE)][type(4B)][payload]."""
    return struct.pack('>I', 8 + len(payload)) + box_type + payload


def _signature_box() -> bytes:
    """JPEG XL signature box."""
    return b'\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a'


def _ftyp_box() -> bytes:
    """Standard ftyp box for a JXL container."""
    payload = b'jxl \x00\x00\x00\x00jxl '
    return _box(b'ftyp', payload)


def _exif_payload(text: str, offset_prefix: bool) -> bytes:
    """Build an EXIF TIFF payload (with optional 4-byte offset prefix).

    Args:
        text: Plain-text content for the UserComment field.
        offset_prefix: If True, prepend a 4-byte big-endian 'TIFF header
            offset' of zero (some writers do this; others start the TIFF
            header directly at payload[0]).

    Returns:
        Raw bytes suitable as an Exif box payload.
    """
    exif = Image.Exif()
    bom = b'\xff\xfe'  # UTF-16 LE BOM
    exif[0x9286] = b'UNICODE\x00' + bom + text.encode('utf-16-le')
    tiff = exif.tobytes()
    if offset_prefix:
        return struct.pack('>I', 0) + tiff
    return tiff


def _brob_box(original_type: bytes, inner_payload: bytes) -> bytes:
    """Build a brob box carrying *inner_payload* compressed with brotli.

    The first four payload bytes of the brob box are the original FourCC
    type (e.g. ``b'Exif'``).
    """
    return _box(b'brob', original_type + brotli.compress(inner_payload))


# ---------------------------------------------------------------------------
# Module constant used by many tests
# ---------------------------------------------------------------------------

COMFY_TEXT = json.dumps({
    'prompt': '{"1": {"inputs": {}}}',
    'workflow': '{"nodes": []}',
    'datetime': '2026-11-02',
})


# ===========================================================================
# TestExtractJxlTextChunks
# ===========================================================================

class TestExtractJxlTextChunks:
    """Tests for ``extract_jxl_text_chunks``."""

    def test_plain_exif_box_with_offset_prefix(self):
        """Plain Exif box with 4-byte offset prefix should decode UserComment."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', _exif_payload(COMFY_TEXT, offset_prefix=True))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {
            'prompt': '{"1": {"inputs": {}}}',
            'workflow': '{"nodes": []}',
            'datetime': '2026-11-02',
        }

    def test_plain_exif_box_direct_tiff_header(self):
        """Plain Exif box with TIFF header at payload[0] (no offset prefix)."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', _exif_payload(COMFY_TEXT, offset_prefix=False))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {
            'prompt': '{"1": {"inputs": {}}}',
            'workflow': '{"nodes": []}',
            'datetime': '2026-11-02',
        }

    def test_brob_compressed_exif_box(self):
        """Exif carried inside a brob box (cjxl v0.12 style)."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _brob_box(b'Exif', _exif_payload(COMFY_TEXT, True))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {
            'prompt': '{"1": {"inputs": {}}}',
            'workflow': '{"nodes": []}',
            'datetime': '2026-11-02',
        }

    def test_brob_non_exif_original_type_skipped(self):
        """A brob box whose original type is not 'Exif' must be ignored."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _brob_box(b'XML ', _exif_payload(COMFY_TEXT, True))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {}

    def test_codestream_only_returns_empty(self):
        """Bare JPEG XL codestream (no container) returns empty dict."""
        data = b'\xff\x0a' + b'\x00' * 32
        result = extract_jxl_text_chunks(data)
        assert result == {}

    def test_container_without_exif_returns_empty(self):
        """A valid JXL container with no Exif box returns empty dict."""
        data = _signature_box() + _ftyp_box()
        result = extract_jxl_text_chunks(data)
        assert result == {}

    def test_malformed_tiff_returns_empty(self):
        """A malformed TIFF (no valid IFD) must not raise."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', struct.pack('>I', 0) + b'garbage-not-tiff')
        )
        result = extract_jxl_text_chunks(data)
        assert result == {}

    def test_non_json_user_comment(self):
        """A raw string in UserComment is returned verbatim."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', _exif_payload('hello world', True))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {'user_comment': 'hello world'}

    def test_json_non_string_values_serialized(self):
        """Non-string JSON values are serialized back to strings."""
        text = json.dumps({'answer': 42, 'nested': {'a': 1}, 'ok': 'keep'})
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', _exif_payload(text, True))
        )
        result = extract_jxl_text_chunks(data)
        assert result == {
            'answer': '42',
            'nested': '{"a": 1}',
            'ok': 'keep',
        }

    def test_first_exif_box_wins(self):
        """When multiple Exif boxes are present, only the first is parsed."""
        second_payload = _exif_payload('second', True)
        data = (
            _signature_box()
            + _ftyp_box()
            + _box(b'Exif', _exif_payload(COMFY_TEXT, True))
            + _box(b'Exif', second_payload)
        )
        result = extract_jxl_text_chunks(data)
        assert 'prompt' in result
        assert result['prompt'] == '{"1": {"inputs": {}}}'
        assert 'user_comment' not in result

    def test_truncated_box_ignored(self):
        """A box claiming more bytes than available is silently skipped."""
        truncated = struct.pack('>I', 9999) + b'Exif' + b'short'
        data = _signature_box() + truncated
        result = extract_jxl_text_chunks(data)
        assert result == {}

    def test_size_zero_box_extends_to_eof(self):
        """A box with size=0 runs to end of file and is parsed normally."""
        data = (
            _signature_box()
            + _ftyp_box()
            + struct.pack('>I', 0) + b'Exif'
            + _exif_payload(COMFY_TEXT, False)
        )
        result = extract_jxl_text_chunks(data)
        assert result == {
            'prompt': '{"1": {"inputs": {}}}',
            'workflow': '{"nodes": []}',
            'datetime': '2026-11-02',
        }


# ===========================================================================
# TestExtractImageTextChunks (dispatcher)
# ===========================================================================

def _make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a PNG chunk: [length][type][data][crc]."""
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xffffffff
    return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)


def _build_png(*extra_chunks: bytes) -> bytes:
    """Build a minimal valid PNG with optional extra chunks."""
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)
    ihdr = _make_png_chunk(b'IHDR', ihdr_data)
    idat_data = zlib.compress(b'\x00\x00')
    idat = _make_png_chunk(b'IDAT', idat_data)
    iend = _make_png_chunk(b'IEND', b'')
    return signature + ihdr + b''.join(extra_chunks) + idat + iend


class TestExtractImageTextChunks:
    """Tests for the dispatcher ``extract_image_text_chunks``."""

    def test_png_bytes_route_to_png_parser(self):
        """PNG magic bytes should be routed to extract_png_text_chunks."""
        text_chunk = _make_png_chunk(b'tEXt', b'keyword\x00value')
        png = _build_png(text_chunk)
        result = extract_image_text_chunks(png)
        assert result == {'keyword': 'value'}

    def test_jxl_bytes_route_to_jxl_parser(self):
        """JXL container magic should route to extract_jxl_text_chunks."""
        data = (
            _signature_box()
            + _ftyp_box()
            + _brob_box(b'Exif', _exif_payload(COMFY_TEXT, True))
        )
        result = extract_image_text_chunks(data)
        assert 'prompt' in result
        assert result['prompt'] == '{"1": {"inputs": {}}}'

    def test_unknown_bytes_return_empty(self):
        """Unrecognised bytes should return empty dict."""
        result = extract_image_text_chunks(b'not-an-image')
        assert result == {}


# ===========================================================================
# TestJxlExifSummary (full EXIF summary from the container)
# ===========================================================================

def _camera_payload(offset_prefix: bool) -> bytes:
    """Build an Exif box payload carrying camera + capture-date tags."""
    exif = Image.Exif()
    exif[271] = "Canon"  # Make
    exif[272] = "EOS R6"  # Model
    exif[0x8769] = {  # EXIF sub-IFD
        0x9003: "2026:06:15 08:00:00",  # DateTimeOriginal
        0x8827: 800,                    # ISOSpeedRatings
    }
    tiff = exif.tobytes()
    if offset_prefix:
        return struct.pack('>I', 0) + tiff
    return tiff


XMP_DOC = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">TestXMP</x:xmpmeta>'


class TestJxlExifSummary:
    """Full EXIF summary + XMP extraction from JXL containers."""

    def test_camera_and_iso_from_plain_exif_box(self):
        data = (
            _signature_box() + _ftyp_box()
            + _box(b'Exif', _camera_payload(offset_prefix=True))
        )
        summary = extract_jxl_exif_summary(data)
        assert summary['camera'] == 'Canon EOS R6'
        assert summary['iso'] == 800
        assert summary['xmp'] is None

    def test_camera_from_brob_box(self):
        data = (
            _signature_box() + _ftyp_box()
            + _brob_box(b'Exif', _camera_payload(True))
        )
        summary = extract_jxl_exif_summary(data)
        assert summary['camera'] == 'Canon EOS R6'

    def test_xmp_from_plain_xml_box(self):
        data = (
            _signature_box() + _ftyp_box()
            + _box(b'xml ', XMP_DOC)
        )
        assert extract_jxl_exif_summary(data)['xmp'] == XMP_DOC.decode()

    def test_xmp_from_brob_xml_box(self):
        data = (
            _signature_box() + _ftyp_box()
            + _brob_box(b'xml ', XMP_DOC)
        )
        assert extract_jxl_exif_summary(data)['xmp'] == XMP_DOC.decode()

    def test_codestream_yields_empty_summary(self):
        data = b'\xff\x0a' + b'\x00' * 32
        summary = extract_jxl_exif_summary(data)
        assert summary['camera'] is None
        assert summary['xmp'] is None

    def test_dispatcher_merges_camera_into_chunks(self):
        data = (
            _signature_box() + _ftyp_box()
            + _brob_box(b'Exif', _camera_payload(True))
        )
        chunks = extract_image_text_chunks(data)
        assert chunks['camera'] == 'Canon EOS R6'
        assert chunks['iso'] == '800'

    def test_dispatcher_does_not_duplicate_user_comment(self):
        """UserComment JSON is already split into chunks — no duplicate."""
        exif = Image.Exif()
        exif[271] = "Canon"
        bom = b'\xff\xfe'
        exif[0x9286] = b'UNICODE\x00' + bom + COMFY_TEXT.encode('utf-16-le')
        data = (
            _signature_box() + _ftyp_box()
            + _box(b'Exif', struct.pack('>I', 0) + exif.tobytes())
        )
        chunks = extract_image_text_chunks(data)
        assert chunks['prompt'] == '{"1": {"inputs": {}}}'
        assert chunks['camera'] == 'Canon'
        assert 'user_comment' not in chunks


# ===========================================================================
# TestJxlTakenDate
# ===========================================================================

class TestJxlTakenDate:
    """Capture-date extraction from JXL containers (extract_taken_date)."""

    def test_datetime_original_from_exif_box(self, tmp_path):
        data = (
            _signature_box() + _ftyp_box()
            + _box(b'Exif', _camera_payload(offset_prefix=True))
        )
        path = tmp_path / 'photo.jxl'
        path.write_bytes(data)
        assert extract_taken_date(path) == datetime(2026, 6, 15, 8, 0, 0)

    def test_datetime_from_user_comment_json(self, tmp_path):
        """ComfyUI-style JXL: no EXIF dates, a 'datetime' key instead."""
        path = tmp_path / 'gen.jxl'
        path.write_bytes(
            _signature_box() + _ftyp_box()
            + _box(b'Exif', _exif_payload(COMFY_TEXT, offset_prefix=True))
        )
        assert extract_taken_date(path) == datetime(2026, 11, 2)

    def test_no_metadata_returns_none(self, tmp_path):
        path = tmp_path / 'empty.jxl'
        path.write_bytes(_signature_box() + _ftyp_box())
        assert extract_taken_date(path) is None
