"""Media processing services (thumbnails, image handling)."""
import os
import tempfile
from io import BytesIO
from pathlib import Path

import cv2
from PIL import Image, ImageOps

from ...config import ALLOWED_VIDEO_TYPES


def create_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int] = (400, 400)):
    """Creates image thumbnail."""
    with Image.open(source_path) as img:
        # Apply EXIF orientation to fix rotated images from cameras/phones
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        # Convert RGBA/P to RGB for JPEG (no transparency support)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(thumb_path, "JPEG", quality=85)


def create_video_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int] = (400, 400)):
    """Creates thumbnail from first frame of video."""
    cap = cv2.VideoCapture(str(source_path))
    try:
        ret, frame = cap.read()
        if not ret:
            raise ValueError("Could not read video frame")

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        img.save(thumb_path, "JPEG", quality=85)
    finally:
        cap.release()


def get_media_type(content_type: str) -> str:
    """Returns 'image' or 'video' based on content type."""
    if content_type in ALLOWED_VIDEO_TYPES:
        return "video"
    return "image"


def create_thumbnail_bytes(image_data: bytes, size: tuple[int, int] = (400, 400)) -> tuple[bytes, int, int]:
    """Create thumbnail from image bytes, return (JPEG bytes, width, height)."""
    with Image.open(BytesIO(image_data)) as img:
        # Apply EXIF orientation to fix rotated images from cameras/phones
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        thumb_width, thumb_height = img.size
        output = BytesIO()
        img.save(output, "JPEG", quality=85)
        return output.getvalue(), thumb_width, thumb_height


def create_video_thumbnail_bytes(video_data: bytes, size: tuple[int, int] = (400, 400)) -> tuple[bytes, int, int]:
    """Create thumbnail from video bytes, return (JPEG bytes, width, height)."""
    # Write to temp file (OpenCV needs file path)
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        tmp.write(video_data)
        tmp_path = tmp.name

    try:
        cap = cv2.VideoCapture(tmp_path)
        try:
            ret, frame = cap.read()
            if not ret:
                raise ValueError("Could not read video frame")

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            img.thumbnail(size, Image.Resampling.LANCZOS)

            thumb_width, thumb_height = img.size
            output = BytesIO()
            img.save(output, "JPEG", quality=85)
            return output.getvalue(), thumb_width, thumb_height
        finally:
            cap.release()
    finally:
        os.unlink(tmp_path)
