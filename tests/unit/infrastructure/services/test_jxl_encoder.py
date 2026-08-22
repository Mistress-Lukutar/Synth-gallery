'''
File:   test_jxl_encoder.py
Brief:  Unit tests for the lossless JPEG XL encoder.
Author: Mistress-Lukutar
Date:   2026-07-11
Version: v0.2.0
'''

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.infrastructure.services import jxl_encoder
from app.infrastructure.services.jxl_encoder import (
    encode_to_lossless_jxl,
    is_jxl_encoding_available,
    JxlEncodeError,
)


@pytest.fixture
def jpeg_bytes() -> bytes:
    '''Return a JPEG byte string.'''
    img = Image.new('RGB', (80, 60), color='blue')
    buf = bytes(img.tobytes())
    # Simple JPEG SOI marker is enough for magic-byte detection in the encoder.
    return b'\xff\xd8' + buf


@pytest.fixture
def fake_jxl_output() -> bytes:
    '''Return bytes that look like a JPEG XL container.'''
    return b'\x00\x00\x00\x0cJXL fake'


def test_encoding_availability_when_cjxl_missing() -> None:
    '''The encoder should report unavailability when cjxl is not found.'''
    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=None,
    ):
        assert is_jxl_encoding_available() is False


def test_encoding_availability_when_cjxl_present() -> None:
    '''The encoder should report availability when cjxl is found.'''
    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=Path('/usr/bin/cjxl'),
    ):
        assert is_jxl_encoding_available() is True


def test_encode_without_cjxl_raises(jpeg_bytes: bytes) -> None:
    '''Encoding should fail fast when the cjxl binary is missing.'''
    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=None,
    ):
        with pytest.raises(JxlEncodeError):
            encode_to_lossless_jxl(jpeg_bytes, is_jpeg=True)


def test_encode_passes_progressive_flags(jpeg_bytes: bytes, fake_jxl_output: bytes) -> None:
    '''cjxl should be invoked with progressive encoding flags.'''
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ''

    def fake_run(cmd: list[str | Path], **kwargs: object) -> MagicMock:
        # Write fake JXL output so the function can read it back.
        output_path = Path(str(cmd[2]))
        output_path.write_bytes(fake_jxl_output)
        return mock_result

    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=Path('/usr/bin/cjxl'),
    ):
        with patch('app.infrastructure.services.jxl_encoder.subprocess.run', side_effect=fake_run) as run_mock:
            result = encode_to_lossless_jxl(jpeg_bytes, is_jpeg=True)

    assert result == fake_jxl_output

    cmd = [str(arg) for arg in run_mock.call_args[0][0]]
    assert '--progressive' in cmd
    assert '--progressive_ac' in cmd
    assert '--qprogressive_ac' in cmd
    assert '--progressive_dc' in cmd
    assert '1' in cmd  # JXL_PROGRESSIVE_DC default
    assert '--lossless_jpeg=1' in cmd


def test_encode_respects_disabled_progressive_flags(
    jpeg_bytes: bytes, fake_jxl_output: bytes
) -> None:
    '''When all progressive flags are disabled, --progressive must not be used.'''
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ''

    def fake_run(cmd: list[str | Path], **kwargs: object) -> MagicMock:
        output_path = Path(str(cmd[2]))
        output_path.write_bytes(fake_jxl_output)
        return mock_result

    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=Path('/usr/bin/cjxl'),
    ):
        with patch(
            'app.infrastructure.services.jxl_encoder.subprocess.run',
            side_effect=fake_run,
        ) as run_mock:
            with patch.object(
                jxl_encoder, 'JXL_PROGRESSIVE_AC', False
            ), patch.object(
                jxl_encoder, 'JXL_QPROGRESSIVE_AC', False
            ), patch.object(
                jxl_encoder, 'JXL_PROGRESSIVE_DC', 0
            ):
                result = encode_to_lossless_jxl(jpeg_bytes, is_jpeg=True)

    assert result == fake_jxl_output

    cmd = [str(arg) for arg in run_mock.call_args[0][0]]
    assert '--progressive' not in cmd
    assert '--progressive_ac' not in cmd
    assert '--qprogressive_ac' not in cmd
    assert '--progressive_dc' not in cmd
    assert '--lossless_jpeg=1' in cmd


def test_encode_non_jpeg_uses_distance_zero(fake_jxl_output: bytes) -> None:
    '''Non-JPEG sources should be encoded with -d 0 for lossless re-encoding.'''
    png_bytes = b'\x89PNG\r\n\x1a\nfake'
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ''

    def fake_run(cmd: list[str | Path], **kwargs: object) -> MagicMock:
        output_path = Path(str(cmd[2]))
        output_path.write_bytes(fake_jxl_output)
        return mock_result

    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=Path('/usr/bin/cjxl'),
    ):
        with patch('app.infrastructure.services.jxl_encoder.subprocess.run', side_effect=fake_run) as run_mock:
            result = encode_to_lossless_jxl(png_bytes, is_jpeg=False)

    assert result == fake_jxl_output

    cmd = [str(arg) for arg in run_mock.call_args[0][0]]
    assert '-d' in cmd
    assert '0' in cmd
    assert '--lossless_jpeg=1' not in cmd


def test_encode_empty_data_raises() -> None:
    '''Encoding empty data should raise a clear error.'''
    with pytest.raises(JxlEncodeError):
        encode_to_lossless_jxl(b'')


def test_encode_cjxl_failure_raises(jpeg_bytes: bytes) -> None:
    '''A non-zero cjxl exit code should raise JxlEncodeError.'''
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = 'encode failed'

    with patch(
        'app.infrastructure.services.jxl_encoder._get_jxl_binary',
        return_value=Path('/usr/bin/cjxl'),
    ):
        with patch('app.infrastructure.services.jxl_encoder.subprocess.run', return_value=mock_result):
            with pytest.raises(JxlEncodeError):
                encode_to_lossless_jxl(jpeg_bytes, is_jpeg=True)
