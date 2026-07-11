'''
File:   test_jxl.py
Brief:  Unit tests for JPEG XL decoding and introspection.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.1.0
'''

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.infrastructure.services.jxl import (
    decode_jxl,
    get_jxl_dimensions,
    is_jxl_available,
    is_jxl_content,
)


@pytest.fixture
def fake_jxl_bytes() -> bytes:
    '''Return bytes that match the JPEG XL container magic.'''
    return b'\x00\x00\x00\x0cJXL fake'


@pytest.fixture
def fake_png_bytes() -> bytes:
    '''Return fake PNG bytes for djxl output.'''
    return b'\x89PNG\r\n\x1a\nfake'


def test_is_jxl_content_detects_container() -> None:
    '''Container magic bytes should be recognized as JXL.'''
    assert is_jxl_content(b'\x00\x00\x00\x0cJXL ') is True


def test_is_jxl_content_detects_codestream() -> None:
    '''Bare codestream magic bytes should be recognized as JXL.'''
    assert is_jxl_content(b'\xff\x0a') is True


def test_is_jxl_content_rejects_other_data() -> None:
    '''Non-JXL bytes should not be recognized.'''
    assert is_jxl_content(b'not jxl') is False


def test_decode_jxl_without_djxl_raises(fake_jxl_bytes: bytes) -> None:
    '''Decoding should fail when the djxl binary is missing.'''
    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=None,
    ):
        with pytest.raises(RuntimeError):
            decode_jxl(fake_jxl_bytes)


def test_decode_jxl_invokes_djxl(
    fake_jxl_bytes: bytes,
    fake_png_bytes: bytes,
) -> None:
    '''decode_jxl should run djxl and return the produced PNG bytes.'''
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ''

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        output_path = Path(cmd[2])
        output_path.write_bytes(fake_png_bytes)
        return mock_result

    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=Path('/usr/bin/djxl'),
    ):
        with patch('app.infrastructure.services.jxl.subprocess.run', side_effect=fake_run) as run_mock:
            result = decode_jxl(fake_jxl_bytes)

    assert result == fake_png_bytes
    assert run_mock.call_count == 1
    assert 'djxl' in str(run_mock.call_args[0][0][0])


def test_decode_jxl_failure_raises(fake_jxl_bytes: bytes) -> None:
    '''A non-zero djxl exit code should raise RuntimeError.'''
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = 'decode failed'

    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=Path('/usr/bin/djxl'),
    ):
        with patch('app.infrastructure.services.jxl.subprocess.run', return_value=mock_result):
            with pytest.raises(RuntimeError):
                decode_jxl(fake_jxl_bytes)


def test_get_jxl_dimensions_without_jxlinfo_returns_none(fake_jxl_bytes: bytes) -> None:
    '''Dimensions should be None when jxlinfo is not available.'''
    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=None,
    ):
        assert get_jxl_dimensions(fake_jxl_bytes) is None


def test_get_jxl_dimensions_parses_jxlinfo_output(fake_jxl_bytes: bytes) -> None:
    '''get_jxl_dimensions should parse width and height from jxlinfo output.'''
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = 'JPEG XL image, 1920x1080, lossy, 8-bit RGB'

    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=Path('/usr/bin/jxlinfo'),
    ):
        with patch('app.infrastructure.services.jxl.subprocess.run', return_value=mock_result):
            dims = get_jxl_dimensions(fake_jxl_bytes)

    assert dims == (1920, 1080)


def test_is_jxl_available_when_djxl_missing() -> None:
    '''Availability should be False when djxl is not found.'''
    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=None,
    ):
        assert is_jxl_available() is False


def test_is_jxl_available_when_djxl_present() -> None:
    '''Availability should be True when djxl is found.'''
    with patch(
        'app.infrastructure.services.jxl._get_jxl_binary',
        return_value=Path('/usr/bin/djxl'),
    ):
        assert is_jxl_available() is True
