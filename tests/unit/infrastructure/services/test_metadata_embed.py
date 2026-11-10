'''
File:   test_metadata_embed.py
Brief:  Unit tests for DB-metadata embedding into downloaded images.
Author: Mistress-Lukutar
Date:   2026-11-10
Version: v0.1.0

Covers the helpers behind the download-time metadata embedding feature
(``app/infrastructure/services/metadata_embed.py``):
- EXIF construction from ``png_text_chunks`` + ``taken_at`` (DB values
  overlay, exposure/f-number parsing, GPS decimal -> DMS conversion).
- JPEG APP1 segment surgery: overlay lands, existing Exif APP1 is
  replaced (never duplicated), other segments stay byte-identical,
  non-JPEG / no-content input is returned unchanged.
- PNG text-chunk and WebP EXIF/XMP save kwargs.
- ``convert_image`` integration for jpeg / png / webp targets and the
  ``embed_metadata`` off-switch.
'''
import io
import json
from fractions import Fraction

import pytest
from PIL import Image
from PIL.ExifTags import IFD

from app.infrastructure.services.image_conversion import (
    ConversionSettings,
    convert_image,
)
from app.infrastructure.services.metadata import gps_to_decimal
from app.infrastructure.services.metadata_embed import (
    EmbeddableMetadata,
    build_exif,
    build_png_info,
    embed_jpeg_metadata,
    has_content,
)


def _exif_sub(exif) -> dict:
    """Return the Exif sub-IFD as a plain dict."""
    return dict(exif.get_ifd(IFD.Exif))


def _gps_ifd(exif) -> dict:
    """Return the GPSInfo IFD as a plain dict."""
    return dict(exif.get_ifd(IFD.GPSInfo))


def _make_jpeg(exif=None) -> bytes:
    """Create a small JPEG, optionally carrying EXIF."""
    buf = io.BytesIO()
    img = Image.new('RGB', (64, 48), 'red')
    kwargs = {'quality': 90}
    if exif is not None:
        kwargs['exif'] = exif.tobytes()
    img.save(buf, 'JPEG', **kwargs)
    return buf.getvalue()


def _full_metadata() -> EmbeddableMetadata:
    """A metadata record covering every mappable chunk key."""
    return EmbeddableMetadata(
        chunks={
            'camera': 'TestCam TestModel',
            'lens': 'TestLens 24mm',
            'iso': '800',
            'exposure_time': '1/250',
            'f_number': '2.8',
            'focal_length_mm': '50',
            'image_description': 'a test description',
            'user_comment': 'hello world',
            'gps': json.dumps(
                {'latitude': 50.4367, 'longitude': 30.5, 'altitude_m': -12.3}
            ),
        },
        taken_at='2026-01-02 03:04',
    )


def _exif_app1_count(data: bytes) -> int:
    """Count APP1 segments carrying an Exif payload in JPEG bytes."""
    count = 0
    index = 2
    while index + 4 < len(data) and data[index] == 0xFF:
        marker = data[index + 1]
        if marker == 0xDA:
            break
        seg_len = int.from_bytes(data[index + 2:index + 4], 'big')
        if marker == 0xE1 and data[index + 4:index + 10] == b'Exif\x00\x00':
            count += 1
        index += 2 + seg_len
    return count


class TestHasContent:
    def test_none_and_empty(self):
        assert not has_content(None)
        assert not has_content(EmbeddableMetadata())
        assert not has_content(EmbeddableMetadata(chunks={}, taken_at=None))

    def test_chunks_or_taken_at(self):
        assert has_content(EmbeddableMetadata(chunks={'iso': '100'}))
        # taken_at alone is an mtime fallback for EXIF-less uploads and
        # must not trigger embedding (see has_content docstring).
        assert not has_content(
            EmbeddableMetadata(taken_at='2026-01-02 03:04:05')
        )

    def test_taken_at_rides_along_with_chunks(self):
        exif = build_exif(EmbeddableMetadata(
            chunks={'iso': '800'}, taken_at='2026-01-02 03:04'
        ))
        assert _exif_sub(exif)[0x9003] == '2026:01:02 03:04:00'

    def test_empty_template_values_do_not_count(self):
        # Uploads record the default summary template (None values) even
        # for metadata-free sources — that is not embeddable content.
        assert not has_content(EmbeddableMetadata(
            chunks={'camera': None, 'iso': '', 'gps': None}
        ))

    def test_single_real_value_counts(self):
        assert has_content(EmbeddableMetadata(
            chunks={'camera': None, 'iso': '800'}
        ))


class TestBuildExif:
    def test_all_chunks_roundtrip(self):
        exif = build_exif(_full_metadata())
        assert exif is not None
        assert exif[0x010F] == 'TestCam'
        assert exif[0x0110] == 'TestModel'
        assert exif[0x010E] == 'a test description'
        sub = _exif_sub(exif)
        assert sub[0x8827] == 800
        assert sub[0xA434] == 'TestLens 24mm'
        assert sub[0x9003] == '2026:01:02 03:04:00'

    def test_exposure_f_number_focal_rationals(self):
        exif = build_exif(_full_metadata())
        sub = _exif_sub(exif)
        assert pytest.approx(float(sub[0x829A]), rel=1e-6) == 1 / 250
        assert pytest.approx(float(sub[0x829D]), rel=1e-6) == 2.8
        assert pytest.approx(float(sub[0x920A]), rel=1e-6) == 50.0

    def test_exposure_time_formats(self):
        for value, expected in [('1/250', 1 / 250), ('2s', 2.0), ('0.5', 0.5)]:
            exif = build_exif(
                EmbeddableMetadata(chunks={'exposure_time': value})
            )
            sub = _exif_sub(exif)
            assert pytest.approx(float(sub[0x829A]), rel=1e-6) == expected

    def test_exposure_unparsable_ignored(self):
        # Only unparsable values -> nothing applied -> no EXIF object.
        assert build_exif(
            EmbeddableMetadata(chunks={'exposure_time': 'garbage'})
        ) is None

    def test_user_comment_unicode_prefix(self):
        exif = build_exif(EmbeddableMetadata(chunks={'user_comment': 'hi'}))
        raw = _exif_sub(exif)[0x9286]
        assert raw.startswith(b'UNICODE\x00')
        assert raw[8:].decode('utf-16-le') == 'hi'

    def test_decimal_datetime_iso_t(self):
        exif = build_exif(
            EmbeddableMetadata(taken_at='2026-11-10T12:34:56')
        )
        assert _exif_sub(exif)[0x9003] == '2026:11:10 12:34:56'

    def test_datetime_unparsable_ignored(self):
        assert build_exif(EmbeddableMetadata(taken_at='not a date')) is None

    def test_gps_roundtrip_via_gps_to_decimal(self):
        exif = build_exif(_full_metadata())
        gps = _gps_ifd(exif)
        # Normalize the freshly written IFD into the raw numeric-keyed
        # dict shape gps_to_decimal accepts (GPS rationals as Fractions).
        normalized = {}
        for tag_id, value in gps.items():
            if isinstance(value, tuple):
                normalized[tag_id] = [
                    Fraction(v.numerator, v.denominator) for v in value
                ]
            elif hasattr(value, 'numerator'):
                normalized[tag_id] = Fraction(
                    value.numerator, value.denominator
                )
            else:
                normalized[tag_id] = value
        result = gps_to_decimal(normalized)
        assert result is not None
        assert result['latitude'] == pytest.approx(50.4367)
        assert result['longitude'] == pytest.approx(30.5)
        assert result['altitude_m'] == pytest.approx(-12.3)
        assert gps[1] == 'N'
        assert gps[3] == 'E'

    def test_base_tags_kept_db_wins(self):
        base = Image.Exif()
        base[0x010F] = 'OldMake'
        _exif_sub(base)[0x8827] = 100
        exif = build_exif(_full_metadata(), base)
        assert exif[0x010F] == 'TestCam'  # DB wins
        assert _exif_sub(exif)[0x8827] == 800  # DB wins

    def test_empty_metadata_returns_none(self):
        assert build_exif(EmbeddableMetadata()) is None


class TestEmbedJpegMetadata:
    def test_embed_lands_and_replaces_existing_app1(self):
        base = Image.Exif()
        base[0x010F] = 'OldMake'
        jpeg = _make_jpeg(base)
        out = embed_jpeg_metadata(jpeg, _full_metadata())
        assert out != jpeg
        assert _exif_app1_count(out) == 1  # replaced, not duplicated
        img = Image.open(io.BytesIO(out))
        exif = img.getexif()
        assert exif[0x010F] == 'TestCam'
        assert _exif_sub(exif)[0x9003] == '2026:01:02 03:04:00'
        # JPEG still decodes as an image
        assert img.size == (64, 48)

    def test_other_segments_preserved(self):
        jpeg = _make_jpeg()  # carries a JFIF APP0
        out = embed_jpeg_metadata(jpeg, _full_metadata())
        # APP0 payload must remain byte-identical
        assert out[2:4] == jpeg[2:4]
        assert out[4:10] == jpeg[4:10]  # 'JFIF\x00\x01...'

    def test_no_content_returns_same_object_bytes(self):
        jpeg = _make_jpeg()
        assert embed_jpeg_metadata(jpeg, EmbeddableMetadata()) is jpeg
        assert embed_jpeg_metadata(jpeg, None) is jpeg

    def test_non_jpeg_returns_unchanged(self):
        png_buf = io.BytesIO()
        Image.new('RGB', (8, 8)).save(png_buf, 'PNG')
        png = png_buf.getvalue()
        assert embed_jpeg_metadata(png, _full_metadata()) is png

    def test_xmp_app1_embedded(self):
        meta = EmbeddableMetadata(
            chunks={'xmp': '<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>'}
        )
        out = embed_jpeg_metadata(_make_jpeg(), meta)
        assert b'x:xmpmeta' in out[:2048]

    def test_unmappable_overlay_keeps_bytes_unchanged(self):
        # A chunk key with no EXIF mapping and no XMP must not churn the
        # existing Exif APP1 (bit-exact reconstruction stays bit-exact).
        base = Image.Exif()
        base[0x010F] = 'OldMake'
        jpeg = _make_jpeg(base)
        meta = EmbeddableMetadata(chunks={'custom_key': 'no mapping'})
        assert embed_jpeg_metadata(jpeg, meta) is jpeg


class TestBuildPngInfo:
    def test_all_chunks_added(self):
        meta = _full_metadata()
        png_info = build_png_info(meta)
        assert png_info is not None
        # PngInfo.chunks is a list of (type, payload, ...) tuples.
        keywords = [
            chunk[1].split(b'\x00', 1)[0].decode('latin-1')
            for chunk in png_info.chunks
            if chunk[0] == b'tEXt'
        ]
        assert 'camera' in keywords
        assert 'iso' in keywords
        assert 'xmp' not in keywords  # dedicated XMP chunk instead

    def test_non_latin_value_becomes_itxt(self):
        png_info = build_png_info(
            EmbeddableMetadata(chunks={'user_comment': 'привет'})
        )
        assert png_info is not None
        assert any(chunk[0] == b'iTXt' for chunk in png_info.chunks)

    def test_xmp_dedicated_chunk(self):
        png_info = build_png_info(
            EmbeddableMetadata(chunks={'xmp': '<x:xmpmeta/>'})
        )
        assert png_info is not None
        payloads = b''.join(chunk[1] for chunk in png_info.chunks)
        assert b'XML:com.adobe.xmp' in payloads

    def test_empty_returns_none(self):
        assert build_png_info(EmbeddableMetadata()) is None


class TestConvertImageEmbedding:
    """convert_image embeds metadata per target format."""

    def _png_source(self) -> bytes:
        buf = io.BytesIO()
        Image.new('RGB', (40, 40), 'blue').save(buf, 'PNG')
        return buf.getvalue()

    def test_jpeg_target_embeds_exif(self):
        out, content_type, ext = convert_image(
            self._png_source(), 'image/png',
            ConversionSettings(format='jpeg'), _full_metadata(),
        )
        assert (content_type, ext) == ('image/jpeg', '.jpg')
        exif = Image.open(io.BytesIO(out)).getexif()
        assert exif[0x010F] == 'TestCam'
        assert _exif_sub(exif)[0x8827] == 800

    def test_png_target_embeds_text_chunks(self):
        out, content_type, ext = convert_image(
            self._png_source(), 'image/png',
            ConversionSettings(format='png'), _full_metadata(),
        )
        img = Image.open(io.BytesIO(out))
        assert img.info['camera'] == 'TestCam TestModel'
        assert img.info['iso'] == '800'
        assert content_type == 'image/png'

    def test_webp_target_embeds_exif(self):
        out, content_type, ext = convert_image(
            self._png_source(), 'image/png',
            ConversionSettings(format='webp'), _full_metadata(),
        )
        exif = Image.open(io.BytesIO(out)).getexif()
        assert exif[0x010F] == 'TestCam'
        assert content_type == 'image/webp'

    def test_embed_metadata_off_strips_embedding(self):
        out, *_ = convert_image(
            self._png_source(), 'image/png',
            ConversionSettings(format='jpeg', embed_metadata=False),
            _full_metadata(),
        )
        exif = Image.open(io.BytesIO(out)).getexif()
        assert 0x010F not in exif
        assert 0x8827 not in _exif_sub(exif)

    def test_empty_metadata_no_embedding(self):
        out, *_ = convert_image(
            self._png_source(), 'image/png',
            ConversionSettings(format='jpeg'), EmbeddableMetadata(),
        )
        assert 0x010F not in Image.open(io.BytesIO(out)).getexif()
