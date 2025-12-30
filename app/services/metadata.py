"""Metadata extraction service for images."""
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS


def extract_taken_date(file_path: Path) -> Optional[datetime]:
    """Extract the date when photo was taken from image metadata.

    Checks multiple metadata fields in order of priority:
    1. EXIF DateTimeOriginal (when photo was actually taken)
    2. EXIF DateTimeDigitized (when photo was digitized)
    3. EXIF DateTime (last modification in camera)
    4. PNG tEXt Creation Time
    5. PNG tEXt Date

    Returns datetime object or None if no date found.
    """
    try:
        with Image.open(file_path) as img:
            # Try EXIF data first (works for JPEG, some PNG, TIFF)
            exif_date = _extract_exif_date(img)
            if exif_date:
                return exif_date

            # Try PNG text chunks
            png_date = _extract_png_date(img)
            if png_date:
                return png_date

    except Exception:
        pass

    return None


def _extract_exif_date(img: Image.Image) -> Optional[datetime]:
    """Extract date from EXIF metadata."""
    try:
        exif_data = img._getexif()
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


def _extract_png_date(img: Image.Image) -> Optional[datetime]:
    """Extract date from PNG text chunks."""
    try:
        # PNG stores metadata in info dict
        info = img.info

        # Check common PNG date fields
        date_fields = ['Creation Time', 'Date', 'creation_time', 'date']

        for field in date_fields:
            if field in info and info[field]:
                parsed = _parse_flexible_datetime(info[field])
                if parsed:
                    return parsed

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


def get_metadata_summary(file_path: Path) -> dict:
    """Get a summary of image metadata for display purposes."""
    result = {
        'taken_at': None,
        'camera': None,
        'dimensions': None,
    }

    try:
        with Image.open(file_path) as img:
            result['dimensions'] = f"{img.width}x{img.height}"

            # Get EXIF data
            exif_data = img._getexif()
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
