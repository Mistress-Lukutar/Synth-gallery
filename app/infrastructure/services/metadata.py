"""Metadata extraction service for images and videos."""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from PIL import Image
from PIL.ExifTags import TAGS


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
        with Image.open(file_path) as img:
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
        with Image.open(file_path) as img:
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
