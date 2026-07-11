"""Unit tests for metadata extraction service."""
import io
import struct
import zlib

import pytest
from PIL import Image

from app.infrastructure.services.metadata import extract_png_text_chunks


def _make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xffffffff
    return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)


def _build_png(*chunks: bytes) -> bytes:
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)
    ihdr = _make_png_chunk(b'IHDR', ihdr_data)
    idat_data = zlib.compress(b'\x00\x00')  # minimal 1x1 grayscale-ish
    idat = _make_png_chunk(b'IDAT', idat_data)
    iend = _make_png_chunk(b'IEND', b'')
    return signature + ihdr + b''.join(chunks) + idat + iend


def test_extract_png_text_chunks_text():
    text_chunk = _make_png_chunk(b'tEXt', b'prompt\x00ComfyUI positive prompt')
    png = _build_png(text_chunk)
    assert extract_png_text_chunks(png) == {'prompt': 'ComfyUI positive prompt'}


def test_extract_png_text_chunks_ztxt():
    raw = b'workflow\x00\x00' + zlib.compress(b'{"nodes": []}')
    ztxt_chunk = _make_png_chunk(b'zTXt', raw)
    png = _build_png(ztxt_chunk)
    assert extract_png_text_chunks(png) == {'workflow': '{"nodes": []}'}


def test_extract_png_text_chunks_itext():
    keyword = b'parameters'
    language = b'en'
    translated = b''
    text = b'1girl, masterpiece'
    data = keyword + b'\x00\x01\x00' + language + b'\x00' + translated + b'\x00' + zlib.compress(text)
    itxt_chunk = _make_png_chunk(b'iTXt', data)
    png = _build_png(itxt_chunk)
    assert extract_png_text_chunks(png) == {'parameters': '1girl, masterpiece'}


def test_extract_png_text_chunks_not_png():
    assert extract_png_text_chunks(b'not a png') == {}


def test_extract_png_text_chunks_jpeg():
    assert extract_png_text_chunks(b'\xff\xd8\xff\xe0') == {}
