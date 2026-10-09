"""Metadata extraction service for images and videos."""
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

from .jxl import decode_jxl


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
            decoded = decode_jxl(file_path.read_bytes())
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
        exif_data = img._getexif()  # type: ignore[attr-defined]  # noqa: W0212
        if not exif_data:
            return None

        # Map EXIF tag IDs to names
        exif = {TAGS.get(k, k): v for k, v in exif_data.items()}

        # Priority order for date fields
        date_fields = ['DateTimeOriginal', 'DateTimeDigitized', 'DateTime']

        for field in date_fields:
            if field in exif and exif[field]:
                date_str = exif[field]
                parsed = _parse_exif_datetime(date_str)
                if parsed:
                    return parsed

    except Exception:
        pass

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
        except (TypeError, ValueError):
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
    should rely on stored metadata for those files.

    Returns:
        Dict with plain JSON-typed fields: camera, lens, iso,
        exposure_time, f_number, focal_length_mm, gps (see
        :func:`gps_to_decimal`), user_comment, image_description and the
        raw ``xmp`` document when present.
    """
    result: dict[str, Any] = {
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
    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return result

    with img:
        try:
            exif = img.getexif()
        except Exception:
            return result

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

        xmp = img.info.get('XML:com.adobe.xmp') or img.info.get('xmp')
        if isinstance(xmp, bytes):
            xmp = xmp.decode('utf-8', errors='ignore')
        if isinstance(xmp, str) and xmp.strip():
            result['xmp'] = xmp

    return result
