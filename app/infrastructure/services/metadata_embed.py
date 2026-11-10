'''
File:   metadata_embed.py
Brief:  Embed DB-stored item metadata (png_text_chunks + taken_at) into
        downloaded image files. JPEG targets get byte-level APP1 segment
        surgery (bit-exact reconstructed JPEGs stay bit-exact); PNG/WebP
        targets receive EXIF/XMP/text-chunk save kwargs for Pillow.
Author: Mistress-Lukutar
Date:   2026-11-10
Version: v0.1.0

The metadata source is the item's editable metadata record
(``item_media.png_text_chunks`` + ``item_media.taken_at``), the same data
the details-panel metadata editor shows. DB values win over any EXIF
already carried by the file; non-overlapping original EXIF tags are kept.
Embedding is best-effort: any failure logs a warning and returns the
input unchanged.
'''
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from fractions import Fraction

from PIL import Image
from PIL.ExifTags import IFD
from PIL.PngImagePlugin import PngInfo
from PIL.TiffImagePlugin import IFDRational

logger = logging.getLogger(__name__)

# Main-IFD tag ids.
_TAG_IMAGE_DESCRIPTION = 0x010E
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110

# Exif sub-IFD (0x8769) tag ids.
_TAG_EXPOSURE_TIME = 0x829A
_TAG_F_NUMBER = 0x829D
_TAG_ISO = 0x8827
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_USER_COMMENT = 0x9286
_TAG_FOCAL_LENGTH = 0x920A
_TAG_LENS_MODEL = 0xA434

# GPSInfo IFD (0x8825) tag ids.
_TAG_GPS_LATITUDE_REF = 1
_TAG_GPS_LATITUDE = 2
_TAG_GPS_LONGITUDE_REF = 3
_TAG_GPS_LONGITUDE = 4
_TAG_GPS_ALTITUDE_REF = 5
_TAG_GPS_ALTITUDE = 6

_USER_COMMENT_UNICODE_PREFIX = b'UNICODE\x00'

#: PNG iTXt keyword carrying the XMP packet (PNG standard).
XMP_PNG_KEYWORD = 'XML:com.adobe.xmp'

# JPEG segment structure.
_JPEG_SOI = b'\xff\xd8'
_JPEG_APP0 = 0xE0
_JPEG_APP1 = 0xE1
_JPEG_SOS = 0xDA
_EXIF_APP1_PREFIX = b'Exif\x00\x00'
_XMP_APP1_PREFIX = b'http://ns.adobe.com/xap/1.0/\x00'

_FRACTION_RE = re.compile(r'^\s*(\d+)\s*/\s*(\d+)\s*$')
_DECIMAL_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*s?\s*$', re.IGNORECASE)

_DATE_TIME_RE = re.compile(
    r'^(\d{4})[:\-](\d{2})[:\-](\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?'
)
_DATE_ONLY_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


@dataclass
class EmbeddableMetadata:
    '''DB-stored metadata ready for embedding into a downloaded file.

    Attributes:
        chunks: The ``png_text_chunks`` dict (chunk keyword -> text value).
        taken_at: The item capture date as stored in the DB.
    '''

    chunks: dict[str, str] | None = None
    taken_at: str | None = None


def has_content(meta: EmbeddableMetadata | None) -> bool:
    '''Return True when there is anything worth embedding.

    Uploads record a default summary template with None/empty values
    even for metadata-free sources, so chunk keys alone do not count —
    at least one chunk value must be a non-empty string. ``taken_at`` is
    NOT sufficient on its own: uploads without capture metadata carry a
    file-mtime fallback, and injecting that as EXIF DateTimeOriginal
    would both fabricate a capture date and break bit-exact JPEG
    reconstruction downloads. When real chunk content exists, a stored
    ``taken_at`` rides along as the DateTimeOriginal overlay.
    '''
    if meta is None or not meta.chunks:
        return False
    return any(
        isinstance(value, str) and value.strip()
        for value in meta.chunks.values()
    )


def _chunks_of(meta: EmbeddableMetadata) -> dict[str, str]:
    '''Return the metadata chunks as a plain dict (never None).'''
    return meta.chunks or {}


def embed_jpeg_metadata(data: bytes, meta: EmbeddableMetadata) -> bytes:
    '''Rebuild the APP1 Exif / APP1 XMP segments of JPEG bytes.

    Byte-level surgery: no re-encode, so a bit-exact reconstructed JPEG
    stays bit-exact apart from the replaced segments. The existing APP1
    Exif (if any) is parsed as the overlay base, DB metadata wins on
    overlapping tags. The result carries at most one Exif APP1 and one
    XMP APP1. When the DB metadata applies no EXIF tag and the file
    already carries one, the original segment is kept untouched (no
    byte-level churn).

    Args:
        data: JPEG file bytes.
        meta: DB-stored metadata to embed.

    Returns:
        The modified bytes, or ``data`` unchanged when embedding is not
        applicable or fails (best-effort).
    '''
    if not has_content(meta) or not data.startswith(_JPEG_SOI):
        return data
    try:
        base = _parse_jpeg_exif(data)
        exif, applied = _build_exif_with_flag(meta, base)
        new_segments: list[tuple[int, bytes]] = []
        if exif is not None and applied:
            new_segments.append(
                (_JPEG_APP1, _EXIF_APP1_PREFIX + exif.tobytes())
            )
        xmp = _chunks_of(meta).get('xmp')
        if xmp:
            new_segments.append(
                (_JPEG_APP1, _XMP_APP1_PREFIX + xmp.encode('utf-8'))
            )
        if not new_segments:
            return data
        return _rebuild_jpeg_segments(data, new_segments)
    except Exception:  # noqa: BLE001 - metadata embedding is best-effort
        logger.warning(
            'metadata embed: JPEG APP1 injection failed', exc_info=True
        )
        return data


def _build_exif_with_flag(
    meta: EmbeddableMetadata, base: Image.Exif | None = None
) -> tuple[Image.Exif, bool]:
    '''Core of :func:`build_exif`; also reports whether DB tags applied.

    Args:
        meta: DB-stored metadata (chunk keyword -> text value + taken_at).
        base: Existing EXIF object parsed from the source file; its tags
            are kept, DB values override overlapping ones.

    Returns:
        Tuple (exif, applied): the EXIF object (possibly the base
        instance with overlays) and whether at least one DB value was
        written.
    '''
    chunks = _chunks_of(meta)
    exif = base if base is not None else Image.Exif()
    applied = False

    camera = chunks.get('camera', '').strip()
    if camera:
        make, _, model = camera.partition(' ')
        if model.strip():
            exif[_TAG_MAKE] = make.strip()
            exif[_TAG_MODEL] = model.strip()
        else:
            exif[_TAG_MODEL] = camera
        applied = True

    sub_ifd = exif.get_ifd(IFD.Exif)
    lens = chunks.get('lens', '').strip()
    if lens:
        sub_ifd[_TAG_LENS_MODEL] = lens
        applied = True
    iso = _parse_int(chunks.get('iso'))
    if iso is not None:
        sub_ifd[_TAG_ISO] = iso
        applied = True
    exposure = _parse_exposure(chunks.get('exposure_time'))
    if exposure is not None:
        sub_ifd[_TAG_EXPOSURE_TIME] = IFDRational(
            exposure.numerator, exposure.denominator
        )
        applied = True
    f_number = _parse_float(chunks.get('f_number'))
    if f_number is not None:
        sub_ifd[_TAG_F_NUMBER] = _simple_rational(f_number)
        applied = True
    focal = _parse_float(chunks.get('focal_length_mm'))
    if focal is not None:
        sub_ifd[_TAG_FOCAL_LENGTH] = _simple_rational(focal)
        applied = True
    description = chunks.get('image_description', '').strip()
    if description:
        exif[_TAG_IMAGE_DESCRIPTION] = description
        applied = True
    comment = chunks.get('user_comment', '').strip()
    if comment:
        sub_ifd[_TAG_USER_COMMENT] = (
            _USER_COMMENT_UNICODE_PREFIX + comment.encode('utf-16-le')
        )
        applied = True
    taken_at = _normalize_datetime(meta.taken_at)
    if taken_at:
        sub_ifd[_TAG_DATETIME_ORIGINAL] = taken_at
        applied = True

    if _apply_gps(exif, chunks.get('gps')):
        applied = True

    return exif, applied


def build_exif(meta: EmbeddableMetadata, base: Image.Exif | None = None) -> Image.Exif | None:
    '''Build an EXIF object from DB metadata over an optional base.

    Args:
        meta: DB-stored metadata (chunk keyword -> text value + taken_at).
        base: Existing EXIF object parsed from the source file; its tags
            are kept, DB values override overlapping ones.

    Returns:
        The EXIF object (possibly the base instance with overlays), or
        None when no DB value applied and there is no base to keep.
    '''
    exif, applied = _build_exif_with_flag(meta, base)
    if applied or base is not None:
        return exif
    return None


def build_png_info(meta: EmbeddableMetadata) -> PngInfo | None:
    '''Build a PngInfo object carrying every DB chunk as a PNG text chunk.

    Values encodable as latin-1 become tEXt chunks, everything else iTXt
    (UTF-8). The XMP document (when present) additionally lands in the
    standard ``XML:com.adobe.xmp`` iTXt chunk. Invalid PNG keywords are
    skipped with a debug log.

    Args:
        meta: DB-stored metadata.

    Returns:
        A PngInfo instance, or None when there is nothing to add.
    '''
    chunks = _chunks_of(meta)
    if not chunks:
        return None
    png_info = PngInfo()
    added = False
    for key, value in chunks.items():
        if key == 'xmp':
            continue  # goes into the dedicated XMP chunk below
        keyword = key if isinstance(key, str) else str(key)
        text = value if isinstance(value, str) else str(value)
        if not keyword or len(keyword) > 79:
            logger.debug('metadata embed: skipping invalid PNG keyword %r', key)
            continue
        try:
            keyword.encode('latin-1')
        except UnicodeEncodeError:
            logger.debug('metadata embed: skipping non-latin keyword %r', key)
            continue
        try:
            text.encode('latin-1')
            png_info.add_text(keyword, text)
        except UnicodeEncodeError:
            png_info.add_itxt(keyword, text)
        added = True
    xmp = chunks.get('xmp')
    if xmp:
        png_info.add_itxt(XMP_PNG_KEYWORD, xmp)
        added = True
    return png_info if added else None


def _parse_jpeg_exif(data: bytes) -> Image.Exif | None:
    '''Parse the APP1 Exif payload of JPEG bytes into an Image.Exif.

    Args:
        data: JPEG file bytes.

    Returns:
        The parsed EXIF object, or None when the file carries none.

    Raises:
        ValueError: When the segment structure cannot be walked.
    '''
    payload = _find_jpeg_app1(data, _EXIF_APP1_PREFIX)
    if payload is None:
        return None
    exif = Image.Exif()
    exif.load(payload)
    return exif


def _find_jpeg_app1(data: bytes, prefix: bytes) -> bytes | None:
    '''Scan JPEG segments for the first APP1 payload with the given prefix.

    Args:
        data: JPEG file bytes.
        prefix: Expected start of the APP1 payload (after the length).

    Returns:
        The APP1 payload (contents after the 2-byte segment length), or
        None when absent.

    Raises:
        ValueError: When the segment structure cannot be walked.
    '''
    index = _walk_jpeg_headers(data)
    while index is not None:
        if index.marker == _JPEG_APP1 and index.payload.startswith(prefix):
            return index.payload
        index = _walk_jpeg_headers(data, index.position)
    return None


class _SegmentRef:
    '''One walked JPEG header segment: its marker, payload and end offset.'''

    __slots__ = ('marker', 'payload', 'position')

    def __init__(self, marker: int, payload: bytes, position: int) -> None:
        self.marker = marker
        self.payload = payload
        self.position = position  # offset just past this segment


def _walk_jpeg_headers(data: bytes, start: int = 2) -> _SegmentRef | None:
    '''Return the next JPEG header segment starting at ``start``.

    Walks only the header area (before SOS); markers without a length
    field (standalone/RST) are skipped. ``start`` must point just past a
    previously returned segment (2 = right after SOI).

    Args:
        data: JPEG file bytes.
        start: Offset to begin scanning at.

    Returns:
        A :class:`_SegmentRef` for the next header segment, or None when
        SOS (start of scan data) or the end of the buffer is reached.

    Raises:
        ValueError: On structurally invalid segment data.
    '''
    index = start
    while index + 1 < len(data):
        if data[index] != 0xFF:
            raise ValueError(f'expected marker at offset {index}')
        marker = data[index + 1]
        if marker == 0xFF:  # fill bytes before a marker
            index += 1
            continue
        if marker == _JPEG_SOS:
            return None
        if marker == 0xD8:
            index += 2
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 3 >= len(data):
            raise ValueError('truncated segment header')
        seg_len = int.from_bytes(data[index + 2:index + 4], 'big')
        if seg_len < 2 or index + 2 + seg_len > len(data):
            raise ValueError(f'invalid segment length {seg_len}')
        payload = data[index + 4:index + 2 + seg_len]
        return _SegmentRef(marker, payload, index + 2 + seg_len)
    return None


def _rebuild_jpeg_segments(
    data: bytes, new_segments: list[tuple[int, bytes]]
) -> bytes:
    '''Drop old Exif/XMP APP1s, insert new APP1 segments, keep the rest.

    New segments are inserted after any APP0 (JFIF) segment, otherwise
    directly after SOI. The scan data (from SOS on) is copied verbatim.

    Args:
        data: JPEG file bytes.
        new_segments: ``(marker, payload)`` tuples to insert; the payload
            excludes the marker and length bytes.

    Returns:
        The rebuilt JPEG bytes.

    Raises:
        ValueError: When the header structure cannot be parsed.
    '''
    kept: list[tuple[int, bytes]] = []
    insert_at = 0
    index = 2
    while True:
        ref = _walk_jpeg_headers(data, index)
        if ref is None:
            break
        payload = ref.payload
        drop = ref.marker == _JPEG_APP1 and (
            payload.startswith(_EXIF_APP1_PREFIX)
            or payload.startswith(_XMP_APP1_PREFIX)
        )
        if not drop:
            if ref.marker == _JPEG_APP0:
                insert_at = len(kept) + 1
            kept.append((ref.marker, data[index:ref.position]))
        index = ref.position
    if index <= 2:
        raise ValueError('no JPEG header segments found')

    out = [_JPEG_SOI]
    built: list[bytes] = []
    for marker, payload in new_segments:
        built.append(
            bytes((0xFF, marker))
            + (len(payload) + 2).to_bytes(2, 'big')
            + payload
        )
    for i, (marker, segment) in enumerate(kept):
        if i == insert_at:
            out.extend(built)
        out.append(segment)
    if insert_at >= len(kept):
        out.extend(built)
    out.append(data[index:])  # SOS marker + scan data, verbatim
    return b''.join(out)


def _apply_gps(exif: Image.Exif, raw_gps: str | None) -> bool:
    '''Write decimal-degree GPS data into the GPSInfo IFD.

    Args:
        exif: EXIF object to mutate.
        raw_gps: The ``gps`` chunk value (a JSON object string shaped
            like :func:`metadata.gps_to_decimal` output).

    Returns:
        True when any GPS tag was written.
    '''
    if not raw_gps:
        return False
    try:
        gps_data = json.loads(raw_gps)
    except (TypeError, ValueError):
        logger.debug('metadata embed: unparsable gps chunk ignored')
        return False
    if not isinstance(gps_data, dict):
        return False
    latitude = _parse_float(gps_data.get('latitude'))
    longitude = _parse_float(gps_data.get('longitude'))
    if latitude is None or longitude is None:
        return False
    gps_ifd = exif.get_ifd(IFD.GPSInfo)
    gps_ifd[_TAG_GPS_LATITUDE] = _to_dms_rationals(latitude)
    gps_ifd[_TAG_GPS_LATITUDE_REF] = 'S' if latitude < 0 else 'N'
    gps_ifd[_TAG_GPS_LONGITUDE] = _to_dms_rationals(longitude)
    gps_ifd[_TAG_GPS_LONGITUDE_REF] = 'W' if longitude < 0 else 'E'
    altitude = _parse_float(gps_data.get('altitude_m'))
    if altitude is not None:
        gps_ifd[_TAG_GPS_ALTITUDE] = _simple_rational(abs(altitude))
        gps_ifd[_TAG_GPS_ALTITUDE_REF] = 1 if altitude < 0 else 0
    return True


def _to_dms_rationals(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    '''Convert a signed decimal degree into DMS EXIF rationals (absolute).'''
    value = abs(float(value))
    degrees = int(value)
    minutes_frac = (value - degrees) * 60
    minutes = int(minutes_frac)
    seconds = int(round((minutes_frac - minutes) * 60 * 1000))
    if seconds >= 60000:
        minutes += 1
        seconds = 0
    return (
        IFDRational(degrees, 1),
        IFDRational(minutes, 1),
        IFDRational(seconds, 1000),
    )


def _simple_rational(value: float) -> IFDRational:
    '''Convert a float into a two-decimal-place EXIF rational.'''
    return IFDRational(int(round(value * 100)), 100)


def _parse_int(value: str | None) -> int | None:
    '''Parse an optional integer string.'''
    if not value:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_float(value) -> float | None:
    '''Parse an optional float value (accepts non-str input).'''
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_exposure(value: str | None) -> Fraction | None:
    '''Parse an exposure-time chunk ("1/250", "2s", "0.5") into a Fraction.

    Args:
        value: The raw chunk value.

    Returns:
        The exposure time in seconds, or None when unparsable.
    '''
    if not value:
        return None
    text = str(value).strip()
    match = _FRACTION_RE.match(text)
    if match:
        denominator = int(match.group(2))
        if denominator == 0:
            return None
        return Fraction(int(match.group(1)), denominator)
    match = _DECIMAL_RE.match(text)
    if match:
        try:
            return Fraction(match.group(1))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def _normalize_datetime(value: str | None) -> str | None:
    '''Normalize a stored date into EXIF "YYYY:MM:DD HH:MM:SS" form.

    Accepts "YYYY-MM-DD HH:MM", ISO "T" separators, EXIF colons and bare
    dates (midnight is assumed for date-only input).

    Args:
        value: The stored ``taken_at`` string.

    Returns:
        The EXIF-format datetime string, or None when unparsable.
    '''
    if not value:
        return None
    text = str(value).strip()
    match = _DATE_TIME_RE.match(text)
    if match:
        year, month, day, hour, minute, second = match.groups()
        return f'{year}:{month}:{day} {hour}:{minute}:{second or "00"}'
    match = _DATE_ONLY_RE.match(text)
    if match:
        year, month, day = match.groups()
        return f'{year}:{month}:{day} 00:00:00'
    return None


def parse_exif_bytes(blob: bytes | None) -> Image.Exif | None:
    '''Parse an EXIF blob (as stored in ``img.info['exif']``) into Exif.

    Args:
        blob: Raw EXIF bytes, or None.

    Returns:
        The parsed EXIF object, or None when the blob is absent/unparsable.
    '''
    if not blob:
        return None
    try:
        exif = Image.Exif()
        exif.load(blob)
        return exif
    except Exception:  # noqa: BLE001 - metadata is best-effort
        logger.debug('metadata embed: unparsable EXIF blob ignored')
        return None


def xmp_document(meta: EmbeddableMetadata) -> str | None:
    '''Return the raw XMP document from the chunks, if any.'''
    value = _chunks_of(meta).get('xmp')
    return value if value else None


def ensure_exif_object(candidate) -> Image.Exif:
    '''Coerce an EXIF-like value into a writable Image.Exif instance.

    ``img.getexif()`` may return a read-only view on some code paths;
    anything that is not an :class:`Image.Exif` is re-parsed through its
    ``tobytes``/``load`` round-trip so tags can be overlaid safely.

    Args:
        candidate: An Image.Exif, or an object exposing ``tobytes``.

    Returns:
        A mutable Image.Exif instance.
    '''
    if isinstance(candidate, Image.Exif):
        return candidate
    if hasattr(candidate, 'tobytes'):
        return parse_exif_bytes(candidate.tobytes()) or Image.Exif()
    return Image.Exif()
