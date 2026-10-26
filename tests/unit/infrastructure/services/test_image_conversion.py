'''
File:   test_image_conversion.py
Brief:  Unit tests for the download image conversion service.
Author: Mistress-Lukutar
Date:   2026-08-19
Version: v1.0.0
'''
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image

from app.infrastructure.services.image_conversion import (
    ConversionSettings,
    ImageConversionError,
    convert_image,
    needs_conversion,
)


def _png_bytes(size: tuple[int, int] = (100, 80), mode: str = 'RGB') -> bytes:
    '''Return a PNG encoded image for tests.'''
    img = Image.new(mode, size, color='green')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _jpeg_bytes(size: tuple[int, int] = (100, 80)) -> bytes:
    '''Return a JPEG encoded image for tests.'''
    img = Image.new('RGB', size, color='blue')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


class TestNeedsConversion:
    '''needs_conversion() dispatch rules.'''

    def test_jxl_target_passthrough_for_jxl_source(self) -> None:
        settings = ConversionSettings(format='jxl')
        assert needs_conversion('image/jxl', settings) is False

    def test_jxl_target_converts_other_sources(self) -> None:
        settings = ConversionSettings(format='jxl')
        assert needs_conversion('image/png', settings) is True
        assert needs_conversion('image/jpeg', settings) is True

    def test_png_target_passthrough_for_png_source(self) -> None:
        settings = ConversionSettings(format='png')
        assert needs_conversion('image/png', settings) is False

    def test_lossy_targets_always_reencode(self) -> None:
        jpeg = ConversionSettings(format='jpeg')
        webp = ConversionSettings(format='webp')
        assert needs_conversion('image/jpeg', jpeg) is True
        assert needs_conversion('image/webp', webp) is True

    def test_non_image_content_never_converts(self) -> None:
        settings = ConversionSettings(format='jpeg')
        assert needs_conversion('video/mp4', settings) is False
        assert needs_conversion('application/octet-stream', settings) is False
        assert needs_conversion('', settings) is False


class TestConvertImage:
    '''convert_image() happy paths via Pillow (no external tools).'''

    def test_png_to_jpeg(self) -> None:
        data, ct, ext = convert_image(
            _png_bytes(), 'image/png', ConversionSettings(format='jpeg')
        )
        assert ct == 'image/jpeg'
        assert ext == '.jpg'
        assert data.startswith(b'\xff\xd8')
        with Image.open(io.BytesIO(data)) as img:
            assert img.format == 'JPEG'
            assert img.size == (100, 80)

    def test_png_to_webp(self) -> None:
        data, ct, ext = convert_image(
            _png_bytes(), 'image/png', ConversionSettings(format='webp')
        )
        assert ct == 'image/webp'
        assert ext == '.webp'
        with Image.open(io.BytesIO(data)) as img:
            assert img.format == 'WEBP'

    def test_png_to_webp_lossless(self) -> None:
        source = _png_bytes(size=(60, 40))
        data, _, _ = convert_image(
            source,
            'image/png',
            ConversionSettings(format='webp', webp_lossless=True),
        )
        with Image.open(io.BytesIO(data)) as img:
            assert img.format == 'WEBP'
            assert img.size == (60, 40)

    def test_jpeg_to_png(self) -> None:
        data, ct, ext = convert_image(
            _jpeg_bytes(), 'image/jpeg', ConversionSettings(format='png')
        )
        assert ct == 'image/png'
        assert ext == '.png'
        with Image.open(io.BytesIO(data)) as img:
            assert img.format == 'PNG'

    def test_rgba_source_flattened_for_jpeg(self) -> None:
        data, _, _ = convert_image(
            _png_bytes(mode='RGBA'),
            'image/png',
            ConversionSettings(format='jpeg'),
        )
        with Image.open(io.BytesIO(data)) as img:
            assert img.mode == 'RGB'

    def test_jpeg_quality_affects_size(self) -> None:
        source = _png_bytes(size=(400, 400))
        small, _, _ = convert_image(
            source, 'image/png', ConversionSettings(format='jpeg', jpeg_quality=10)
        )
        large, _, _ = convert_image(
            source, 'image/png', ConversionSettings(format='jpeg', jpeg_quality=95)
        )
        assert len(small) < len(large)

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ImageConversionError):
            convert_image(
                _png_bytes(),
                'image/png',
                ConversionSettings(format='tiff'),
            )

    def test_corrupt_source_raises(self) -> None:
        with pytest.raises(ImageConversionError):
            convert_image(b'not an image', 'image/png', ConversionSettings(format='jpeg'))


class TestConvertImageJxl:
    '''JXL paths with mocked libjxl CLIs (no binaries required).'''

    def test_jxl_source_decoded_via_djxl(self) -> None:
        decoded = _png_bytes()
        with patch(
            'app.infrastructure.services.image_conversion.decode_jxl_to_pixels',
            return_value=decoded,
        ):
            with patch(
                'app.infrastructure.services.image_conversion.extract_jxl_exif',
                return_value=None,
            ):
                with patch(
                    'app.infrastructure.services.image_conversion.reconstruct_jpeg',
                    return_value=None,
                ):
                    data, ct, ext = convert_image(
                        b'\x00\x00\x00\x0cJXL fake',
                        'image/jxl',
                        ConversionSettings(format='jpeg'),
                    )
        assert ct == 'image/jpeg'
        assert data.startswith(b'\xff\xd8')

    def test_jxl_to_jpeg_reconstruction_short_circuits_pixel_path(self) -> None:
        '''Reconstructable JXLs return the original JPEG without decoding.'''
        original = _jpeg_bytes()
        with patch(
            'app.infrastructure.services.image_conversion.reconstruct_jpeg',
            return_value=original,
        ) as mock_reconstruct:
            with patch(
                'app.infrastructure.services.image_conversion.decode_jxl_to_pixels',
            ) as mock_decode:
                data, ct, ext = convert_image(
                    b'\x00\x00\x00\x0cJXL fake',
                    'image/jxl',
                    ConversionSettings(format='jpeg'),
                )
        mock_reconstruct.assert_called_once()
        mock_decode.assert_not_called()
        assert ct == 'image/jpeg'
        assert ext == '.jpg'
        assert data == original

    def test_jxl_target_uses_cjxl_with_effort(self) -> None:
        fake_jxl = b'\x00\x00\x00\x0cJXL encoded'
        with patch(
            'app.infrastructure.services.image_conversion.encode_to_lossless_jxl',
            return_value=fake_jxl,
        ) as mock_encode:
            data, ct, ext = convert_image(
                _png_bytes(),
                'image/png',
                ConversionSettings(format='jxl', jxl_effort=9),
            )
        assert data == fake_jxl
        assert ct == 'image/jxl'
        assert ext == '.jxl'
        mock_encode.assert_called_once()
        assert mock_encode.call_args.kwargs.get('effort') == 9

    def test_jxl_target_failure_raises_conversion_error(self) -> None:
        from app.infrastructure.services.jxl_encoder import JxlEncodeError

        with patch(
            'app.infrastructure.services.image_conversion.encode_to_lossless_jxl',
            side_effect=JxlEncodeError('cjxl not found'),
        ):
            with pytest.raises(ImageConversionError):
                convert_image(
                    _png_bytes(),
                    'image/png',
                    ConversionSettings(format='jxl'),
                )
