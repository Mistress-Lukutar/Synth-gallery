"""Media processing services (thumbnails, image handling)."""
from pathlib import Path

import cv2
from PIL import Image

from ..config import ALLOWED_VIDEO_TYPES


def create_thumbnail(source_path: Path, thumb_path: Path, size: tuple[int, int] = (400, 400)):
    """Creates image thumbnail."""
    with Image.open(source_path) as img:
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
