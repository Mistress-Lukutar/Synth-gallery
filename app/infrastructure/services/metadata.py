'''
File:   metadata.py
Brief:  Metadata extraction service for images and videos.
Author: Mistress-Lukutar
Date:   2026-11-02
Version: v2.1.0
'''
import brotli
import json
import re
import struct
import subprocess
import zlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Any

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

from .jxl import JXL_CONTAINER_MAGIC, decode_jxl


def extract_taken_date(file_path: Path) -> Optional[datetime]:
    """Extract the date when media was created from metadata.

    For images, checks:
    1. EXIF DateTimeOriginal (when photo was actually taken)
    2. EXIF DateTimeDigitized (when photo was digitized)
    3. EXIF DateTime (last modification in camera)
    4. PNG tEXt Creation Time / Date
    5. GIF comment or XMP data
    6. WebP EXIF data

    For videos, uses ffprobe to extract creation_time.

    Returns datetime object or None if no date found.
    """
    suffix = file_path.suffix.lower()

    # Video files
    if suffix in ('.mp4', '.webm', '.mov', '.avi', '.mkv'):
        return _extract_video_date(file_path)

    # Image files
    try:
        if suffix == '.jxl':
            data = file_path.read_bytes()
            # Container files carry metadata as ISOBMFF boxes; djxl returns
            # bare pixels, so EXIF dates must be read from the Exif box.
            jxl_date = _extract_jxl_container_date(data)
            if jxl_date:
                return jxl_date
            decoded = decode_jxl(data)
            img = Image.open(BytesIO(decoded))
        else:
            img = Image.open(file_path)

        with img:
            # Try EXIF data first (works for JPEG, WebP, some PNG, TIFF)
            exif_date = _extract_exif_date(img)
            if exif_date:
                return exif_date

            # Try PNG/GIF text chunks and info
            info_date = _extract_info_date(img)
            if info_date:
                return info_date

            # Try XMP data (embedded XML metadata)
            xmp_date = _extract_xmp_date(img)
            if xmp_date:
                return xmp_date

    except Exception:
        pass

    return None


def _extract_exif_date(img: Image.Image) -> Optional[datetime]:
    """Extract date from EXIF metadata."""
    try:
        exif = img.getexif()
    except Exception:
        return None
    if not exif:
        return None
    return _date_from_exif_object(exif)


def _date_from_exif_object(exif: Image.Exif) -> Optional[datetime]:
    """Read the capture date from an :class:`PIL.Image.Exif` object.

    Checks the EXIF sub-IFD first (where ``DateTimeOriginal`` and
    ``DateTimeDigitized`` live on real camera files), then the root IFD
    (``DateTime`` = last in-camera modification).
    """
    try:
        sub_ifd = exif.get_ifd(0x8769)
    except Exception:
        sub_ifd = None

    date_tag_ids = (0x9003, 0x9004, 0x0132)
    for ifd in (sub_ifd, exif):
        if not ifd:
            continue
        for tag_id in date_tag_ids:
            value = _ifd_value(ifd, tag_id)
            if value:
                parsed = _parse_exif_datetime(str(value))
                if parsed:
                    return parsed
    return None


def _extract_jxl_container_date(data: bytes) -> Optional[datetime]:
    """Extract the capture date from a JPEG XL container's metadata.

    Reads EXIF date tags from the Exif box payload; falls back to text
    chunks (generation tools store a ``datetime`` key in the UserComment
    JSON object).
    """
    if not data.startswith(JXL_CONTAINER_MAGIC):
        return None

    payload = _find_jxl_exif_payload(data)
    if payload:
        tiff = _normalize_exif_payload(payload)
        if tiff:
            try:
                exif = Image.Exif()
                exif.load(tiff)
                parsed = _date_from_exif_object(exif)
                if parsed:
                    return parsed
            except Exception:
                pass

    try:
        chunks = extract_jxl_text_chunks(data)
    except Exception:
        return None
    for key in ('DateTimeOriginal', 'CreateDate', 'datetime',
                'DateTime', 'date'):
        value = chunks.get(key)
        if value:
            parsed = _parse_flexible_datetime(value)
            if parsed:
                return parsed
    return None


def _extract_info_date(img: Image.Image) -> Optional[datetime]:
    """Extract date from image info dict (PNG text chunks, GIF comments, etc.)."""
    try:
        info = img.info

        # Check common date fields in image info
        date_fields = [
            'Creation Time', 'Date', 'creation_time', 'date',
            'DateTimeOriginal', 'DateTime', 'ModifyDate',
            'comment',  # GIF comments sometimes contain dates
        ]

        for field in date_fields:
            if field in info and info[field]:
                value = info[field]
                if isinstance(value, bytes):
                    value = value.decode('utf-8', errors='ignore')
                parsed = _parse_flexible_datetime(str(value))
                if parsed:
                    return parsed

    except Exception:
        pass

    return None


def _extract_xmp_date(img: Image.Image) -> Optional[datetime]:
    """Extract date from XMP metadata (XML-based, used in many formats)."""
    try:
        # Try to get XMP data from image
        xmp_data = None

        # Check for XMP in image info
        if 'XML:com.adobe.xmp' in img.info:
            xmp_data = img.info['XML:com.adobe.xmp']
        elif 'xmp' in img.info:
            xmp_data = img.info['xmp']

        if not xmp_data:
            return None

        if isinstance(xmp_data, bytes):
            xmp_data = xmp_data.decode('utf-8', errors='ignore')

        # Look for date patterns in XMP
        # xmp:CreateDate, photoshop:DateCreated, exif:DateTimeOriginal
        date_patterns = [
            r'<xmp:CreateDate>([^<]+)</xmp:CreateDate>',
            r'<photoshop:DateCreated>([^<]+)</photoshop:DateCreated>',
            r'<exif:DateTimeOriginal>([^<]+)</exif:DateTimeOriginal>',
            r'xmp:CreateDate="([^"]+)"',
            r'photoshop:DateCreated="([^"]+)"',
        ]

        for pattern in date_patterns:
            match = re.search(pattern, xmp_data)
            if match:
                parsed = _parse_flexible_datetime(match.group(1))
                if parsed:
                    return parsed

    except Exception:
        pass

    return None


def _extract_video_date(file_path: Path) -> Optional[datetime]:
    """Extract creation date from video using ffprobe."""
    try:
        # Try ffprobe first
        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                str(file_path)
            ],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            tags = data.get('format', {}).get('tags', {})

            # Try various date fields
            date_fields = ['creation_time', 'date', 'DATE', 'Creation Time']
            for field in date_fields:
                if field in tags:
                    parsed = _parse_flexible_datetime(tags[field])
                    if parsed:
                        return parsed

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        # ffprobe not available or failed
        pass
    except Exception:
        pass

    return None


def _parse_exif_datetime(date_str: str) -> Optional[datetime]:
    """Parse EXIF datetime format: 'YYYY:MM:DD HH:MM:SS'."""
    if not date_str or not isinstance(date_str, str):
        return None

    try:
        # Standard EXIF format
        return datetime.strptime(date_str.strip(), '%Y:%m:%d %H:%M:%S')
    except ValueError:
        pass

    # Try alternative format with dashes
    try:
        return datetime.strptime(date_str.strip(), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass

    return None


def _parse_flexible_datetime(date_str: str) -> Optional[datetime]:
    """Parse various datetime formats."""
    if not date_str or not isinstance(date_str, str):
        return None

    formats = [
        '%Y:%m:%d %H:%M:%S',      # EXIF standard
        '%Y-%m-%d %H:%M:%S',      # ISO-like
        '%Y-%m-%dT%H:%M:%S',      # ISO 8601
        '%Y-%m-%dT%H:%M:%SZ',     # ISO 8601 with Z
        '%Y-%m-%d',               # Date only
        '%d/%m/%Y %H:%M:%S',      # European
        '%m/%d/%Y %H:%M:%S',      # American
    ]

    date_str = date_str.strip()

    # Handle timezone suffix
    if date_str.endswith('Z'):
        date_str = date_str[:-1]
    if '+' in date_str:
        date_str = date_str.split('+')[0]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    return None


def get_metadata_summary(file_path: Path) -> dict[str, Any]:
    """Get a summary of image metadata for display purposes."""
    result: dict[str, Any] = {
        'taken_at': None,
        'camera': None,
        'dimensions': None,
    }

    try:
        if file_path.suffix.lower() == '.jxl':
            decoded = decode_jxl(file_path.read_bytes())
            img = Image.open(BytesIO(decoded))
        else:
            img = Image.open(file_path)

        with img:
            result['dimensions'] = f"{img.width}x{img.height}"

            # Get EXIF data
            exif_data = img._getexif()  # type: ignore[attr-defined]  # noqa: W0212
            if exif_data:
                exif = {TAGS.get(k, k): v for k, v in exif_data.items()}

                # Camera model
                if 'Model' in exif:
                    result['camera'] = exif['Model']
                elif 'Make' in exif:
                    result['camera'] = exif['Make']

            # Get taken date
            result['taken_at'] = extract_taken_date(file_path)

    except Exception:
        pass

    return result


def extract_png_text_chunks(data: bytes) -> dict[str, str]:
    """Extract PNG text chunks (tEXt/zTXt/iTXt) from raw image bytes.

    JPEG XL transcoding strips PNG text chunks, so they must be captured
    from the original file before any transcode step.

    Args:
        data: Raw file bytes.

    Returns:
        Dictionary mapping chunk keyword to text value.
    """
    chunks: dict[str, str] = {}
    if len(data) < 16 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return chunks

    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            break
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8].decode('ascii', errors='ignore')
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + length
        if chunk_data_end + 4 > len(data):
            break

        if chunk_type == 'tEXt':
            _parse_text_chunk(data[chunk_data_start:chunk_data_end], chunks)
        elif chunk_type == 'zTXt':
            _parse_ztext_chunk(data[chunk_data_start:chunk_data_end], chunks)
        elif chunk_type == 'iTXt':
            _parse_itext_chunk(data[chunk_data_start:chunk_data_end], chunks)

        offset = chunk_data_end + 4  # skip CRC

    return chunks


def _parse_text_chunk(chunk_data: bytes, chunks: dict[str, str]) -> None:
    """Parse a PNG tEXt chunk."""
    try:
        null_idx = chunk_data.index(0)
        keyword = chunk_data[:null_idx].decode('latin-1', errors='ignore')
        text = chunk_data[null_idx + 1:].decode('latin-1', errors='ignore')
        if keyword and keyword not in chunks:
            chunks[keyword] = text
    except ValueError:
        pass


def _parse_ztext_chunk(chunk_data: bytes, chunks: dict[str, str]) -> None:
    """Parse a PNG zTXt chunk (compressed text)."""
    try:
        null_idx = chunk_data.index(0)
        keyword = chunk_data[:null_idx].decode('latin-1', errors='ignore')
        compression_method = chunk_data[null_idx + 1]
        if compression_method != 0:
            return
        compressed = chunk_data[null_idx + 2:]
        text = zlib.decompress(compressed).decode('latin-1', errors='ignore')
        if keyword and keyword not in chunks:
            chunks[keyword] = text
    except Exception:
        pass


def _parse_itext_chunk(chunk_data: bytes, chunks: dict[str, str]) -> None:
    """Parse a PNG iTXt chunk (international text, UTF-8)."""
    try:
        first_null = chunk_data.index(0)
        keyword = chunk_data[:first_null].decode('latin-1', errors='ignore')
        remainder = chunk_data[first_null + 1:]
        if len(remainder) < 3:
            return
        compression_flag = remainder[0]
        compression_method = remainder[1]
        second_null = remainder.index(0, 2)
        # language tag (remainder[2:second_null]) ignored
        remainder = remainder[second_null + 1:]
        third_null = remainder.index(0)
        # translated keyword (remainder[:third_null]) ignored in favor of original keyword
        text_data = remainder[third_null + 1:]
        if compression_flag == 1:
            if compression_method != 0:
                return
            text = zlib.decompress(text_data).decode('utf-8', errors='ignore')
        else:
            text = text_data.decode('utf-8', errors='ignore')
        if keyword and keyword not in chunks:
            chunks[keyword] = text
    except Exception:
        pass


#: TIFF byte-order markers that may start an Exif payload directly.
_EXIF_TIFF_MAGICS = (b'II\x2a\x00', b'MM\x00\x2a')

#: JPEG APP1-style marker some writers place before the TIFF header.
_EXIF_APP1_MARKER = b'Exif\x00\x00'

#: Field template of the compact EXIF summary (all values absent).
_EXIF_SUMMARY_DEFAULTS: dict[str, Any] = {
    'camera': None,
    'lens': None,
    'iso': None,
    'exposure_time': None,
    'f_number': None,
    'focal_length_mm': None,
    'gps': None,
    'user_comment': None,
    'image_description': None,
    'xmp': None,
}


def extract_jxl_text_chunks(data: bytes) -> dict[str, str]:
    """Extract metadata text chunks from a JPEG XL container.

    JPEG XL files carry metadata as ISOBMFF boxes: a plain ``Exif`` box
    (a TIFF payload, optionally preceded by a 4-byte big-endian offset
    to the TIFF header) or a ``brob`` box (brotli-compressed; the first
    four payload bytes name the original box type — cjxl compresses
    metadata boxes into ``brob`` by default). The EXIF UserComment
    (tag 0x9286) is decoded with :func:`_decode_user_comment`;
    generation tools store a JSON object there (ComfyUI: ``prompt`` /
    ``workflow`` / ``datetime``), so a JSON object is split into one
    chunk per key — matching the separate tEXt chunks a ComfyUI PNG
    upload carries. Any other text lands under a single
    ``user_comment`` key.

    Bare codestreams (``\\xff\\x0a``) carry no boxes and yield ``{}``.

    Args:
        data: Raw JXL file bytes.

    Returns:
        Dictionary mapping chunk keyword to text value.
    """
    payload = _find_jxl_exif_payload(data)
    if payload is None:
        return {}
    return _parse_jxl_exif_user_comment(payload)


def _normalize_exif_payload(payload: bytes) -> bytes | None:
    """Return the bare TIFF blob from an Exif box payload, or None.

    Writers emit the TIFF blob in three layouts:

    1. TIFF header at byte 0 (the ComfyUI JXL encoder);
    2. a 4-byte big-endian "TIFF header offset" field, then the TIFF
       header (ISO/IEC 18181-2; offset 0 = right after the field);
    3. the offset field, then a JPEG APP1-style ``Exif\\0\\0`` marker
       before the TIFF header (what Pillow's ``Exif.tobytes()`` emits).
    """
    if len(payload) < 4:
        return None
    if payload.startswith(_EXIF_TIFF_MAGICS):
        return payload
    if payload.startswith(_EXIF_APP1_MARKER):
        candidate = payload[6:]
    else:
        offset = struct.unpack('>I', payload[:4])[0]
        candidate = payload[4 + offset:]
    if candidate.startswith(_EXIF_TIFF_MAGICS):
        return candidate
    if candidate.startswith(_EXIF_APP1_MARKER):
        return candidate[6:]
    return None


def _iter_jxl_top_level_boxes(data: bytes):
    """Yield ``(box_type, payload)`` for a JXL container's top-level boxes.

    Malformed boxes (truncated header, size overrun) stop the walk.
    """
    pos = 12  # skip the 12-byte signature box

    while pos < len(data):
        if pos + 8 > len(data):
            break
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        box_type = data[pos + 4:pos + 8]
        header_len = 8
        if size == 1:
            # Extended size in the following 8 bytes
            if pos + 16 > len(data):
                break
            size = struct.unpack('>Q', data[pos + 8:pos + 16])[0]
            header_len = 16
        elif size == 0:
            # Final box extends to EOF
            size = len(data) - pos
        if size < header_len or pos + size > len(data):
            break

        yield box_type, data[pos + header_len:pos + size]
        pos += size


def _find_jxl_exif_payload(data: bytes) -> bytes | None:
    """Return the Exif box payload of a JXL container, or None.

    Walks the top-level boxes only; the first candidate wins when both
    a plain ``Exif`` box and a ``brob``-compressed one are present.
    """
    for box_type, payload in _iter_jxl_top_level_boxes(data):
        if box_type == b'Exif':
            return payload
        if box_type == b'brob' and len(payload) >= 4:
            if payload[:4] == b'Exif':
                try:
                    return brotli.decompress(payload[4:])
                except brotli.error:
                    pass

    return None


def _find_jxl_xmp(data: bytes) -> str | None:
    """Return the XMP document of a JXL container, or None.

    XMP lives in an ``xml `` box (note the trailing space), plain or
    brob-compressed by cjxl.
    """
    for box_type, payload in _iter_jxl_top_level_boxes(data):
        if box_type == b'xml ':
            return payload.decode('utf-8', errors='ignore')
        if box_type == b'brob' and len(payload) >= 4:
            if payload[:4] == b'xml ':
                try:
                    return brotli.decompress(payload[4:]).decode(
                        'utf-8', errors='ignore'
                    )
                except brotli.error:
                    pass

    return None


def extract_jxl_exif_summary(data: bytes) -> dict[str, Any]:
    """Extract the compact EXIF summary from a JPEG XL container.

    Parses the TIFF payload of the container's Exif box (see
    :func:`_normalize_exif_payload` for the supported layouts) and the
    XMP document when present; bare codestreams yield the all-None
    summary.

    Args:
        data: Raw JXL file bytes.

    Returns:
        The same summary shape as :func:`extract_image_exif`.
    """
    result = _EXIF_SUMMARY_DEFAULTS.copy()
    xmp = _find_jxl_xmp(data)
    payload = _find_jxl_exif_payload(data)
    if payload is None:
        result['xmp'] = xmp
        return result
    tiff = _normalize_exif_payload(payload)
    if tiff is None:
        result['xmp'] = xmp
        return result

    try:
        exif = Image.Exif()
        exif.load(tiff)
    except Exception:
        return result

    return _exif_summary_from_exif(exif, xmp=xmp)


def _parse_jxl_exif_user_comment(payload: bytes) -> dict[str, str]:
    """Parse an Exif box payload (TIFF blob) into text chunks.

    The payload layout is normalized by :func:`_normalize_exif_payload`
    (TIFF header first, offset-prefixed, or APP1-marker-prefixed). The
    EXIF UserComment (tag 0x9286) is decoded with
    :func:`_decode_user_comment`; generation tools store a JSON object
    there (ComfyUI: ``prompt`` / ``workflow`` / ``datetime``), so a JSON
    object is split into one chunk per key — matching the separate tEXt
    chunks a ComfyUI PNG upload carries. Any other text lands under a
    single ``user_comment`` key.

    Args:
        payload: Raw bytes from an Exif (or brob-decompressed) box.

    Returns:
        Dictionary of text chunks, or an empty dict on parse failure.
    """
    tiff = _normalize_exif_payload(payload)
    if tiff is None:
        return {}

    try:
        exif = Image.Exif()
        exif.load(tiff)
    except Exception:
        return {}

    # Prefer the EXIF sub-IFD; fall back to the root IFD.
    sub_ifd = exif.get_ifd(0x8769)
    raw = _ifd_value(sub_ifd, 0x9286) if sub_ifd else None
    if not raw:
        raw = exif.get(0x9286)

    text = _decode_user_comment(raw)
    if not text:
        return {}
    stripped = text.strip()
    if not stripped:
        return {}

    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return {'user_comment': stripped}
    if not isinstance(obj, dict):
        return {'user_comment': stripped}
    return {
        str(k): (v if isinstance(v, str) else json.dumps(v))
        for k, v in obj.items()
        if str(k)
    }


def extract_image_text_chunks(data: bytes) -> dict[str, str]:
    """Dispatch text-chunk extraction by image format.

    PNG bytes go to :func:`extract_png_text_chunks`, JPEG XL containers
    to :func:`extract_jxl_text_chunks` plus the full EXIF summary of the
    container's Exif box (:func:`extract_jxl_exif_summary`); everything
    else (JPEG, WebP, GIF, PNG with an eXIf chunk) contributes
    EXIF/XMP-derived entries via :func:`extract_exif_text_chunks`.
    Native text chunks always win over EXIF-derived keys.

    Args:
        data: Raw image file bytes.

    Returns:
        Dictionary mapping chunk keyword to text value.
    """
    chunks: dict[str, str] = {}

    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        chunks = extract_png_text_chunks(data)
    elif data.startswith(JXL_CONTAINER_MAGIC):
        chunks = extract_jxl_text_chunks(data)
        summary_chunks = _exif_summary_to_chunks(
            extract_jxl_exif_summary(data)
        )
        if chunks:
            # The UserComment text is already stored (split into
            # per-key chunks or verbatim) — do not duplicate it.
            summary_chunks.pop('user_comment', None)
        for key, value in summary_chunks.items():
            chunks.setdefault(key, value)
        return chunks

    for key, value in extract_exif_text_chunks(data).items():
        chunks.setdefault(key, value)
    return chunks


def extract_exif_text_chunks(data: bytes) -> dict[str, str]:
    """Extract EXIF/XMP metadata as text-chunk-style entries.

    Covers the formats without native text chunks (JPEG, WebP, GIF) and
    PNG files carrying an eXIf chunk. GPS coordinates are stored as a
    JSON object string, exposure as a photographic fraction
    (``1/250``); the raw XMP document, when present, is stored verbatim
    under the ``xmp`` key.

    Args:
        data: Raw image file bytes.

    Returns:
        Dictionary mapping chunk keyword to text value.
    """
    return _exif_summary_to_chunks(extract_image_exif(data))


def _exif_summary_to_chunks(summary: dict[str, Any]) -> dict[str, str]:
    """Flatten a compact EXIF summary into string-valued chunks."""
    chunks: dict[str, str] = {}

    for key in ('camera', 'lens', 'user_comment', 'image_description'):
        value = summary.get(key)
        if value:
            chunks[key] = str(value)

    if summary.get('iso') is not None:
        chunks['iso'] = str(summary['iso'])

    exposure = summary.get('exposure_time')
    if exposure:
        chunks['exposure_time'] = _format_exposure(exposure)

    if summary.get('f_number') is not None:
        chunks['f_number'] = f"{summary['f_number']:g}"

    if summary.get('focal_length_mm') is not None:
        chunks['focal_length_mm'] = f"{summary['focal_length_mm']:g}"

    gps = summary.get('gps')
    if gps:
        chunks['gps'] = json.dumps(gps, ensure_ascii=False)

    if summary.get('xmp'):
        chunks['xmp'] = summary['xmp']

    return chunks


def _format_exposure(seconds: float) -> str:
    """Format an exposure time as a photographic value (``1/250``, ``2s``)."""
    if seconds <= 0:
        return str(seconds)
    if seconds >= 1:
        return f'{seconds:g}s'
    denom = round(1 / seconds)
    if denom and abs(1 / denom - seconds) / seconds < 0.02:
        return f'1/{denom}'
    return f'{seconds:g}'


# ---------------------------------------------------------------------------
# Live EXIF probe (AI chat tool support)
# ---------------------------------------------------------------------------

_GPS_TAG_IDS = {name: idx for idx, name in GPSTAGS.items()}


def _ifd_value(ifd: dict, tag_id: int):
    """Read an IFD value by tag id, tolerating name-keyed dicts.

    Pillow's ``get_ifd()`` returns int-keyed dicts for some IFDs and
    name-keyed dicts for others (GPS IFD uses GPSTAGS names), and old
    Pillow versions differ; accept both key styles.
    """
    if tag_id in ifd:
        return ifd[tag_id]
    name = TAGS.get(tag_id)
    if name is not None and name in ifd:
        return ifd[name]
    gps_name = GPSTAGS.get(tag_id)
    if gps_name is not None and gps_name in ifd:
        return ifd[gps_name]
    return None


def gps_to_decimal(gps: dict) -> Optional[dict[str, float]]:
    """Convert an EXIF GPSInfo IFD into decimal degrees.

    Accepts dicts keyed either by GPSTAGS names or their numeric ids.

    Returns:
        ``{'latitude': float, 'longitude': float, 'altitude_m': float|None}``
        or None when latitude/longitude are absent or unparsable.
    """
    def _degrees(value) -> Optional[float]:
        if value is None:
            return None
        try:
            parts = list(value)
        except TypeError:
            return None
        if len(parts) != 3:
            return None
        try:
            deg, minutes, seconds = (float(p) for p in parts)
        except (TypeError, ValueError, ZeroDivisionError):
            # 0/0 rationals mean "no fix" (e.g. Pixel exports carry an
            # empty GPS IFD) — treat the coordinate as absent.
            return None
        return deg + minutes / 60.0 + seconds / 3600.0

    latitude = _degrees(_ifd_value(gps, 2))  # GPSLatitude
    longitude = _degrees(_ifd_value(gps, 4))  # GPSLongitude
    if latitude is None or longitude is None:
        return None

    lat_ref = str(_ifd_value(gps, 1) or 'N').strip().upper()  # GPSLatitudeRef
    lon_ref = str(_ifd_value(gps, 3) or 'E').strip().upper()  # GPSLongitudeRef
    if lat_ref == 'S':
        latitude = -latitude
    if lon_ref == 'W':
        longitude = -longitude

    altitude = None
    raw_alt = _ifd_value(gps, 6)  # GPSAltitude
    if raw_alt is not None:
        try:
            if isinstance(raw_alt, tuple) and len(raw_alt) == 2:
                altitude = float(raw_alt[0]) / float(raw_alt[1])
            else:
                altitude = float(raw_alt)
            alt_ref = _ifd_value(gps, 5)  # GPSAltitudeRef
            if isinstance(alt_ref, (bytes, bytearray)):
                alt_ref = ord(alt_ref[:1]) if alt_ref else 0
            if int(alt_ref or 0) == 1:  # 1 = below sea level
                altitude = -altitude
        except (TypeError, ValueError, ZeroDivisionError):
            altitude = None

    return {
        'latitude': round(latitude, 6),
        'longitude': round(longitude, 6),
        'altitude_m': None if altitude is None else round(altitude, 2),
    }


def _decode_user_comment(raw) -> Optional[str]:
    """Decode an EXIF UserComment value (8-byte charset prefix + text).

    Generation tools (e.g. A1111) store the prompt in this field, so it
    must survive the UNICODE / JIS / ASCII charset prefixes.
    """
    if isinstance(raw, str):
        return raw or None
    if not isinstance(raw, (bytes, bytearray)):
        return None
    raw = bytes(raw)
    prefix, text = raw[:8], raw[8:]
    if prefix.startswith(b'UNICODE'):
        # A BOM picks the byte order itself; BOM-less data is UTF-16-BE
        # per the EXIF convention (what A1111 and friends emit).
        if text[:2] in (b'\xff\xfe', b'\xfe\xff'):
            try:
                return text.decode('utf-16').strip('\x00') or None
            except UnicodeDecodeError:
                return None
        for encoding in ('utf-16-be', 'utf-16-le'):
            try:
                decoded = text.decode(encoding)
            except UnicodeDecodeError:
                continue
            stripped = decoded.strip('\x00')
            if stripped:
                return stripped
        return None
    if prefix.startswith(b'JIS'):
        try:
            decoded = text.decode('shift_jis', errors='ignore')
        except LookupError:
            return None
        return decoded.strip('\x00') or None
    decoded = text.decode('utf-8', errors='ignore')
    return decoded.strip('\x00') or None


def extract_image_exif(data: bytes) -> dict[str, Any]:
    """Extract a compact EXIF summary (camera, exposure, GPS, comments).

    Works on raw image bytes (JPEG, TIFF, WebP, PNG with an eXIf chunk).
    JPEG XL cannot be probed this way (djxl returns bare pixels); callers
    should rely on stored metadata or :func:`extract_jxl_exif_summary`
    for those files.

    Returns:
        Dict with plain JSON-typed fields: camera, lens, iso,
        exposure_time, f_number, focal_length_mm, gps (see
        :func:`gps_to_decimal`), user_comment, image_description and the
        raw ``xmp`` document when present.
    """
    result: dict[str, Any] = _EXIF_SUMMARY_DEFAULTS.copy()
    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return result

    with img:
        try:
            exif = img.getexif()
        except Exception:
            return result

        xmp = img.info.get('XML:com.adobe.xmp') or img.info.get('xmp')
        if isinstance(xmp, bytes):
            xmp = xmp.decode('utf-8', errors='ignore')
        if not isinstance(xmp, str) or not xmp.strip():
            xmp = None

        return _exif_summary_from_exif(exif, xmp=xmp)


def _exif_summary_from_exif(
    exif: Image.Exif,
    xmp: Optional[str] = None,
) -> dict[str, Any]:
    """Build the compact EXIF summary from an :class:`PIL.Image.Exif` object.

    Shared by :func:`extract_image_exif` (Pillow-decoded images) and
    :func:`extract_jxl_exif_summary` (JXL Exif box payloads).

    Args:
        exif: Loaded EXIF object (root IFD tags readable by id).
        xmp: Raw XMP document when the source format carries one.

    Returns:
        Dict with plain JSON-typed fields (see :func:`extract_image_exif`).
    """
    result: dict[str, Any] = _EXIF_SUMMARY_DEFAULTS.copy()

    make = exif.get(271)  # Make
    model = exif.get(272)  # Model
    camera = ' '.join(
        str(value).strip() for value in (make, model) if value
    )
    result['camera'] = camera or None
    lens = exif.get(42036)  # LensModel
    result['lens'] = str(lens) if lens else None

    sub_ifd = exif.get_ifd(0x8769)  # EXIF sub-IFD

    def _rational(tag_id: int) -> Optional[float]:
        value = _ifd_value(sub_ifd, tag_id)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    result['exposure_time'] = _rational(33434)  # ExposureTime
    result['f_number'] = _rational(33437)  # FNumber
    result['focal_length_mm'] = _rational(37386)  # FocalLength

    iso = _ifd_value(sub_ifd, 34855) or exif.get(34855)
    try:
        result['iso'] = int(iso) if iso is not None else None
    except (TypeError, ValueError):
        result['iso'] = None

    result['user_comment'] = _decode_user_comment(
        _ifd_value(sub_ifd, 37510)
    )

    description = exif.get(270)  # ImageDescription
    if isinstance(description, bytes):
        description = description.decode('utf-8', errors='ignore')
    if isinstance(description, str) and description.strip():
        result['image_description'] = description.strip()

    gps_ifd = exif.get_ifd(0x8825)  # GPSInfo
    if gps_ifd:
        result['gps'] = gps_to_decimal(dict(gps_ifd))

    result['xmp'] = xmp
    return result
